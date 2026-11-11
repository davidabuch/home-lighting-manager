"""Contract tests through real cabinet execution, HLM projection and HA services."""

import asyncio
from dataclasses import asdict

import pytest
from test_effective_rendering import publish, setup
from test_layered_operations import operation
from test_ownership import automation

from custom_components.home_lighting_manager import register_command_tracking
from custom_components.home_lighting_manager.engine import NIGHTLY_BOUNDARY
from custom_components.home_lighting_manager.ha_observer import HomeAssistantShadowObserver
from custom_components.home_lighting_manager.model import Appearance, LayerKind
from custom_components.home_lighting_reconciliation.cabinet import DOORS, SYNC, Cabinet
from custom_components.home_lighting_reconciliation.engine import LIQUOR

MANUAL = Appearance(True, brightness=91, xy_color=(0.21, 0.32), color_mode="xy", effect="off")


def prepare(rig, owner="daily", appearance=None):
    runtime, members = setup(rig, "main_area", "holiday" if owner == "holiday" else "daily")
    if owner == "off":
        rig.set("input_boolean.home_lighting_main_area_window", "off")
    if owner == "49ers":
        rig.set("sensor.nfl_san_francisco_49ers", "IN")
    if owner in ("manual", "manual_off"):
        runtime.observe_operation(operation(1, members=(LIQUOR,),
                                            kind="off" if owner == "manual_off" else "appearance",
                                            appearance=appearance or MANUAL))
    publish(rig, runtime, members)
    observer = HomeAssistantShadowObserver(rig.hass, members, dict.fromkeys(members, 250))
    observer.runtime = runtime
    observer._surface_members_by_group["light.holiday_main_area"] = members
    register_command_tracking(rig.hass, observer)
    rig.cabinet_runtime, rig.cabinet_members, rig.cabinet_observer = runtime, members, observer
    rig.set(LIQUOR, "off" if owner in ("manual_off", "off") else "on",
            {k: v for k, v in asdict(appearance or MANUAL).items() if v is not None and k != "on"})
    if owner in ("holiday", "49ers"):
        scene_id = "scene.holiday_main_area_pumpkin_spice" if owner == "holiday" else (
            "scene.holiday_main_area_49ers"
        )
        # Actual loaded resolver scene selection below is used, not this alias guess.
        rig.static_scene_id = scene_id
    return runtime


async def event(rig, index, opened):
    rig.set(DOORS[index], "on" if opened else "off")
    return await rig.renderer.cabinet.update()


def assert_isolated(rig):
    for service, data in rig.calls:
        if service.startswith(("light.", "hue.")):
            assert data["entity_id"] == LIQUOR
        assert service != "scene.turn_on"
    assert not any(s == "script.home_lighting_evaluate_and_apply" for s, _ in rig.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["daily", "holiday", "49ers", "off", "manual", "manual_off"])
@pytest.mark.parametrize("first", [0, 1])
async def test_current_owner_release_exact_leaf(rig, owner, first):
    runtime = prepare(rig, owner)
    owners = await rig.renderer.owners()
    if owner in ("holiday", "49ers"):
        scene = owners["main_area"]["scene"]
        rig.scene_actions[scene] = {
            "auto_dynamic": False, "active": "static", "latest": True,
            "actions": {e: {"rid": e, "action": {"on": {"on": True}}}
                        for e in rig.cabinet_members},
        }
    before = tuple(runtime.engine.layers(LIQUOR))
    await event(rig, first, True)
    identity = rig.renderer.cabinet.interval.identity
    snapshot = rig.renderer.cabinet.interval.snapshot
    assert rig.get(LIQUOR).attributes["brightness"] == 255
    await event(rig, 1 - first, True)
    assert rig.renderer.cabinet.interval.identity == identity
    assert rig.renderer.cabinet.interval.snapshot == snapshot
    await event(rig, first, False)
    assert rig.get(LIQUOR).attributes["brightness"] == 255
    result = await event(rig, 1 - first, False)
    assert result["status"] == "released_verified"
    assert rig.renderer.cabinet.interval is None
    assert tuple(runtime.engine.layers(LIQUOR)) == before
    assert (LIQUOR in runtime.reconciliation_protected_entities()) == owner.startswith("manual")
    assert rig.get(LIQUOR).state == ("off" if owner in ("off", "manual_off") else "on")
    if owner in ("daily", "manual"):
        assert rig.get(LIQUOR).attributes["brightness"] == (183 if owner == "daily" else 91)
    if owner in ("holiday", "49ers"):
        assert any(s == "hue.apply_action" for s, _ in rig.calls)
    assert_isolated(rig)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["owners", "guardless_marker", "native_read"])
async def test_old_close_fenced_before_dispatch_and_cannot_clear_new_interval(rig, monkeypatch, boundary):
    prepare(rig, "manual")
    await event(rig, 0, True)
    entered, release = asyncio.Event(), asyncio.Event()
    if boundary == "owners":
        original = rig.renderer.owners

        async def owners():
            result = await original()
            if not entered.is_set():
                entered.set()
                await release.wait()
            return result

        monkeypatch.setattr(rig.renderer, "owners", owners)
    elif boundary == "guardless_marker":
        original = rig.renderer.cabinet.track

        async def track(*args):
            await original(*args)
            if args[1] == "prepare" and not entered.is_set():
                entered.set()
                await release.wait()

        monkeypatch.setattr(rig.renderer.cabinet, "track", track)
    else:
        # Exercise the scene-read boundary under an automatic static scene.
        rig.set("input_boolean.home_lighting_holiday_active", "on")
        rig.set("input_text.home_lighting_holiday_key", "halloween")
        rig.cabinet_runtime.engine.manual_release(LIQUOR)
        publish(rig, rig.cabinet_runtime, rig.cabinet_members)
        original = rig.renderer.hue.read

        async def read(*args):
            members, scenes = await original(*args)
            for scene in scenes.values():
                scene["auto_dynamic"] = False
            entered.set()
            await release.wait()
            return members, scenes

        monkeypatch.setattr(rig.renderer.hue, "read", read)
    rig.calls.clear()
    closing = asyncio.create_task(event(rig, 0, False))
    await asyncio.wait_for(entered.wait(), 2)
    opening = asyncio.create_task(event(rig, 0, True))
    await asyncio.sleep(0)  # Let ingress invalidate; no elapsed-time qualification.
    new_id = rig.renderer.cabinet.interval.identity
    release.set()
    await asyncio.gather(closing, opening)
    assert rig.renderer.cabinet.interval.identity == new_id
    assert rig.get(LIQUOR).attributes["brightness"] == 255
    assert not any(s == "light.turn_on" and d.get("brightness") == 91 for s, d in rig.calls)
    assert_isolated(rig)


@pytest.mark.asyncio
async def test_reopen_captures_intent_not_still_white(rig):
    prepare(rig, "manual")
    await event(rig, 0, True)
    rig.set(DOORS[0], "off")
    rig.renderer.cabinet.invalidate_inputs()
    # No close render completed: the physical bulb is still white.
    await event(rig, 0, True)
    assert rig.renderer.cabinet.interval.snapshot["desired"]["brightness"] == 91
    await event(rig, 1, True)
    await event(rig, 0, False)
    await event(rig, 1, False)
    assert rig.get(LIQUOR).attributes["brightness"] == 91


@pytest.mark.asyncio
@pytest.mark.parametrize("new", ["manual", "manual_off", "off"])
async def test_owner_changes_while_open_release_current_truth(rig, new):
    runtime = prepare(rig)
    await event(rig, 0, True)
    if new == "off":
        rig.set("input_boolean.home_lighting_main_area_window", "off")
    else:
        runtime.observe_operation(operation(2, members=(LIQUOR,),
                                            kind="off" if new == "manual_off" else "appearance",
                                            appearance=MANUAL))
    publish(rig, runtime, rig.cabinet_members)
    await rig.renderer.cabinet.update("ownership_changed")
    await event(rig, 0, False)
    assert rig.get(LIQUOR).state == ("on" if new == "manual" else "off")
    if new == "manual":
        assert rig.get(LIQUOR).attributes["brightness"] == 91
    assert_isolated(rig)


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["opening", "release_marker"])
async def test_sync_prevents_functional_dispatch(rig, monkeypatch, when):
    prepare(rig, "manual")
    if when == "opening":
        rig.set(SYNC, "on")
        await event(rig, 0, True)
        assert not rig.lights()
        rig.set(SYNC, "off")
        await rig.renderer.cabinet.update("sync_changed")
        assert rig.get(LIQUOR).attributes["brightness"] == 255
    else:
        await event(rig, 0, True)
        rig.calls.clear()
        existing = set(rig.cabinet_observer.command_receipts.records)
        original = rig.renderer.cabinet.track

        async def mark(*args):
            await original(*args)
            if args[1] == "prepare":
                rig.set(SYNC, "on")

        monkeypatch.setattr(rig.renderer.cabinet, "track", mark)
        result = await event(rig, 0, False)
        assert result["status"] == "stale_plan"
        assert not rig.lights()
        assert set(rig.cabinet_observer.command_receipts.records) == existing
        assert rig.renderer.cabinet.interval is not None


@pytest.mark.asyncio
async def test_dynamic_manual_scene_is_explicitly_blocked_never_recalls_siblings(rig):
    scene = Appearance(True, scene_id="original-native-scene", scene_evidence="recall-proof")
    runtime = prepare(rig, "manual", scene)
    sibling = next(e for e in rig.cabinet_members if e != LIQUOR)
    runtime.observe_operation(operation(2, members=(sibling,), kind="off"))
    publish(rig, runtime, rig.cabinet_members)
    await event(rig, 0, True)
    result = await event(rig, 0, False)
    assert result["status"] == "blocked"
    assert result["reason"] == "native_manual_scene_rejoin_requires_capability"
    assert runtime.engine.resolve(LIQUOR).appearance.scene_id == "original-native-scene"
    assert runtime.engine.resolve(sibling).layer.kind is LayerKind.MANUAL_OFF
    assert rig.renderer.cabinet.interval.snapshot["desired"]["scene_id"] == "original-native-scene"
    assert_isolated(rig)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["failed", "cancelled", "rejected", "unavailable", "unknown_door"])
async def test_unsuccessful_release_retains_interval(rig, monkeypatch, failure):
    prepare(rig, "manual")
    await event(rig, 0, True)
    identity = rig.renderer.cabinet.interval.identity
    if failure == "failed":
        rig.fail_light = True
        with pytest.raises(RuntimeError):
            await event(rig, 0, False)
    elif failure == "cancelled":
        original = rig.renderer.cabinet.track

        async def cancel(*args):
            await original(*args)
            if args[1] == "prepare":
                raise asyncio.CancelledError

        monkeypatch.setattr(rig.renderer.cabinet, "track", cancel)
        with pytest.raises(asyncio.CancelledError):
            await event(rig, 0, False)
    else:
        if failure == "unavailable":
            rig.set(LIQUOR, "unavailable")
        elif failure == "unknown_door":
            rig.set(DOORS[1], "unknown")
        else:
            state = rig.get("sensor.home_lighting_manager_shadow_health")
            rig.set(state.entity_id, state.state, {**state.attributes,
                                                  "pending_intent_entities": [LIQUOR]})
        result = await event(rig, 0, False)
        assert result["status"].startswith("blocked")
    assert rig.renderer.cabinet.interval.identity == identity
    assert rig.cabinet_runtime.engine.resolve(LIQUOR).layer.kind is LayerKind.MANUAL


@pytest.mark.asyncio
async def test_nightly_and_restart_do_not_replay_snapshot(rig):
    runtime = prepare(rig, "manual")
    await event(rig, 0, True)
    runtime.engine.expire_boundary(NIGHTLY_BOUNDARY)
    publish(rig, runtime, rig.cabinet_members)
    rig.set("input_boolean.home_lighting_main_area_window", "off")
    # A new executor starts with no runtime interval; current HLM state governs.
    rig.renderer.cabinet = Cabinet(rig.renderer)
    await rig.renderer.cabinet.update("startup")
    assert rig.get(LIQUOR).attributes["brightness"] == 255
    await event(rig, 0, False)
    assert rig.get(LIQUOR).state == "off"
    assert not runtime.reconciliation_protected_entities()
    assert_isolated(rig)


@pytest.mark.asyncio
async def test_daily_automation_never_awaits_whole_main_evaluator(rig):
    prepare(rig)
    automation(rig, "liquor_cabinet_display_lighting.yaml", "cabinet_test")
    async def forbidden(_call):
        raise AssertionError("whole evaluator invoked")
    rig.hass.services.async_register("script", "home_lighting_evaluate_and_apply", forbidden)
    for state in ("on", "off", "on", "off"):
        rig.set(DOORS[0], state)
        await rig.run("cabinet_test", {"trigger": {"id": "door_state"}})
    assert rig.get(LIQUOR).attributes["brightness"] == 183
    assert rig.renderer.cabinet.interval is None
    assert_isolated(rig)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["owners", "prepare", "submitted"])
@pytest.mark.parametrize("change", ["manual", "manual_off", "nightly", "generation", "unavailable"])
async def test_new_truth_at_every_static_dispatch_boundary(rig, monkeypatch, boundary, change):
    runtime = prepare(rig, "manual")
    await event(rig, 0, True)
    rig.calls.clear()
    injected = False

    def mutate():
        nonlocal injected
        if injected:
            return
        injected = True
        if change in ("manual", "manual_off"):
            runtime.observe_operation(operation(2, members=(LIQUOR,),
                kind="off" if change == "manual_off" else "appearance",
                appearance=Appearance(True, brightness=53, color_temp_kelvin=3100,
                                      color_mode="color_temp")))
        elif change == "nightly":
            runtime.engine.expire_boundary(NIGHTLY_BOUNDARY)
            rig.set("input_boolean.home_lighting_main_area_window", "off")
        elif change == "generation":
            runtime.engine.next_generation()
        else:
            rig.set(LIQUOR, "unavailable")
        publish(rig, runtime, rig.cabinet_members)

    if boundary == "owners":
        original = rig.renderer.owners
        async def owners():
            result = await original()
            mutate()
            return result
        monkeypatch.setattr(rig.renderer, "owners", owners)
    else:
        original = rig.renderer.cabinet.track
        async def track(*args):
            await original(*args)
            if args[1] == boundary:
                mutate()
        monkeypatch.setattr(rig.renderer.cabinet, "track", track)
    await event(rig, 0, False)
    assert injected
    assert not rig.lights()
    assert rig.renderer.cabinet.interval is not None
    assert not any(r.expected.get("brightness") == 91
                   for r in rig.cabinet_observer.command_receipts.records.values())


@pytest.mark.asyncio
async def test_slow_submitted_command_and_newer_open_are_ordered(rig):
    prepare(rig, "manual")
    await event(rig, 0, True)
    entered, release = asyncio.Event(), asyncio.Event()
    async def hold(call):
        if call.domain == "light" and call.data.get("brightness") == 91:
            entered.set()
            await release.wait()
    rig.before_service = hold
    closing = asyncio.create_task(event(rig, 0, False))
    await asyncio.wait_for(entered.wait(), 2)
    opening = asyncio.create_task(event(rig, 0, True))
    await asyncio.sleep(0)
    identity = rig.renderer.cabinet.interval.identity
    release.set()
    await asyncio.gather(closing, opening)
    assert rig.renderer.cabinet.interval.identity == identity
    assert rig.get(LIQUOR).attributes["brightness"] == 255
    assert rig.lights()[-1][1]["brightness"] == 255
    assert rig.renderer.cabinet.pending is None
    assert_isolated(rig)


@pytest.mark.asyncio
async def test_accepted_release_is_not_success_until_observed(rig):
    prepare(rig, "manual")
    await event(rig, 0, True)
    async def accepted_without_report(call):
        rig.calls.append(("light.turn_on", dict(call.data)))
    rig.hass.services.async_register("light", "turn_on", accepted_without_report)
    result = await event(rig, 0, False)
    assert result["status"] == "awaiting_verification"
    assert rig.renderer.cabinet.interval is not None
    rig.set(LIQUOR, "on", {"brightness": 91, "xy_color": [0.21, 0.32], "effect": "off"})
    rig.renderer.cabinet.observe()
    assert rig.renderer.cabinet.interval is None
    assert rig.renderer.cabinet.last["status"] == "released_verified"


@pytest.mark.asyncio
async def test_unknown_sensors_recover_by_resolving_current_owner(rig):
    prepare(rig, "manual_off")
    await event(rig, 0, True)
    rig.set(DOORS[1], "unavailable")
    assert (await event(rig, 0, False))["status"] == "blocked_sensors_unknown"
    rig.set(DOORS[1], "off")
    result = await rig.renderer.cabinet.update("recovery")
    assert result["status"] == "released_verified"
    assert rig.get(LIQUOR).state == "off"
    assert rig.cabinet_runtime.engine.resolve(LIQUOR).layer.kind is LayerKind.MANUAL_OFF


@pytest.mark.asyncio
async def test_pending_functional_release_cannot_be_bypassed_by_reconciliation(rig):
    scene = Appearance(True, scene_id="dynamic-original", scene_evidence="recall")
    prepare(rig, "manual", scene)
    await event(rig, 0, True)
    await event(rig, 0, False)
    check, owners = await rig.renderer.inspect()
    assert owners["main_area"]["functional_pending_entities"] == [LIQUOR]
    assert any("Functional release pending" in i.get("error", "") for i in check.issues)
    assert not any(c.entity == LIQUOR for c in check.commands)
    assert rig.renderer.cabinet.interval is not None


@pytest.mark.asyncio
async def test_startup_waits_for_projection_then_open_white_is_eligible(rig):
    prepare(rig)
    saved = rig.get("sensor.home_lighting_manager_shadow_health")
    rig.hass.states.async_remove(saved.entity_id)
    rig.set(DOORS[0], "on")
    assert (await rig.renderer.cabinet.update("startup"))["status"] == "blocked"
    rig.set(saved.entity_id, saved.state, dict(saved.attributes))
    assert (await rig.renderer.cabinet.update("ownership_changed"))["status"] == "white_submitted"
    assert rig.get(LIQUOR).attributes["brightness"] == 255


@pytest.mark.asyncio
@pytest.mark.parametrize("entity", ["input_boolean.home_lighting_main_area_window",
                                   "input_boolean.home_lighting_holiday_active",
                                   "sensor.nfl_san_francisco_49ers"])
async def test_automatic_release_never_treats_missing_control_as_off(rig, entity):
    prepare(rig)
    await event(rig, 0, True)
    rig.set(entity, "unavailable")
    rig.calls.clear()
    result = await event(rig, 0, False)
    assert result["reason"] == "automatic_ownership_inputs_unavailable"
    assert rig.renderer.cabinet.interval is not None
    assert not rig.lights()


@pytest.mark.asyncio
async def test_pending_cabinet_does_not_block_eligible_siblings(rig):
    from test_effective_rendering import evaluate, touched
    prepare(rig, "manual", Appearance(True, scene_id="dynamic", scene_evidence="recall"))
    await event(rig, 0, True)
    await event(rig, 0, False)
    await evaluate(rig, "main_area")
    assert LIQUOR not in touched(rig)
    assert set(rig.cabinet_members) - {LIQUOR} <= touched(rig)


@pytest.mark.asyncio
async def test_native_automatic_dynamics_are_not_silently_frozen_to_action(rig):
    prepare(rig, "holiday")
    scene = (await rig.renderer.owners())["main_area"]["scene"]
    rig.scene_actions[scene] = {"auto_dynamic": True, "active": "dynamic_palette",
        "actions": {LIQUOR: {"rid": LIQUOR, "action": {"on": {"on": True}}}}}
    await event(rig, 0, True)
    rig.calls.clear()
    result = await event(rig, 0, False)
    assert result["reason"] == "native_dynamic_scene_rejoin_requires_capability"
    assert rig.renderer.cabinet.interval is not None
    assert not rig.lights()
    assert not any(s.startswith("hue.") for s, _ in rig.calls)


@pytest.mark.asyncio
async def test_unlisted_exact_aggregate_cannot_bypass_new_interval_at_guard_await(rig, monkeypatch):
    from custom_components.home_lighting_reconciliation.cabinet import DoorInterval
    from custom_components.home_lighting_reconciliation.rendering import render

    prepare(rig)
    alias = "light.exact_members_alias"
    rig.set(alias, "on", {"entity_id": list(rig.cabinet_members)})
    registry = type(rig.hass.services)
    original = registry.async_call

    async def interleave(self, domain, service, *args, **kwargs):
        result = await original(self, domain, service, *args, **kwargs)
        if domain == "script" and service == "home_lighting_rearm_ha_guard":
            rig.renderer.cabinet.interval = DoorInterval()
        return result

    monkeypatch.setattr(registry, "async_call", interleave)
    rig.calls.clear()
    await render(rig.renderer, "main_area", "light.turn_off", [alias], {}, None, "daily")
    assert not [s for s, _ in rig.calls if s.startswith(("light.", "hue."))]
    assert rig.renderer.cabinet.interval is not None
    assert rig.renderer.runner.diag["last_render"]["reason"] == "stale_plan"
