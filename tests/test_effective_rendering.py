"""Acceptance across ownership -> projection -> real YAML -> dispatch -> verifier."""

from dataclasses import replace

import pytest
from test_layered_operations import operation

from custom_components.home_lighting_manager.engine import NIGHTLY_BOUNDARY
from custom_components.home_lighting_manager.model import Appearance, LayerKind, OwnershipLayer
from custom_components.home_lighting_manager.projection import effective_ownership
from custom_components.home_lighting_manager.shadow import ShadowRuntime
from custom_components.home_lighting_reconciliation.const import EVALUATORS, TRANSIENTS
from custom_components.home_lighting_reconciliation.engine import GROUPS

EVALUATE = dict(
    main_area="home_lighting_evaluate_and_apply",
    front_eve="home_lighting_evaluate_and_apply",
    path="home_lighting_apply_path_state",
    backyard="home_lighting_evaluate_backyard",
)


def setup(rig, surface, owner="daily"):
    members = tuple(rig.members[surface])
    runtime = ShadowRuntime(managed_entities=frozenset(members))
    for entity in members:
        runtime.engine.push(
            entity,
            OwnershipLayer(
                "baseline:" + entity,
                owner,
                LayerKind.AUTOMATIC,
                1,
                0,
                appearance=Appearance(True, brightness=100),
            ),
        )
    rig.set("input_boolean.home_lighting_" + surface + "_window", "on")
    if owner == "holiday":
        rig.set("input_boolean.home_lighting_holiday_active", "on")
        rig.set("input_text.home_lighting_holiday_key", "halloween")
    for entity in TRANSIENTS:
        rig.set(entity, "off", {"current": 0})
    for entity in EVALUATORS:
        if not rig.get(entity):
            rig.set(entity, "off", {"current": 0})
    for name in GROUPS:
        rig.set("sensor." + name + "_last_recall", "2026-09-27T12:00:00+00:00")
    original = rig.service

    async def physical_scene(call):
        await original(call)
        scene = call.data["entity_id"]
        if isinstance(scene, list):
            scene = scene[0]
        _, info = await rig.renderer.hue.read([scene], [surface])
        for entity, action in info[scene]["actions"].items():
            rig.set(entity, "on" if action["action"]["on"]["on"] else "off")

    rig.hass.services.async_register("scene", "turn_on", physical_scene)

    async def mark_command_consequence(call):
        rig.calls.append(("home_lighting_manager.mark_command_consequence", dict(call.data)))

    rig.hass.services.async_register(
        "home_lighting_manager", "mark_command_consequence", mark_command_consequence
    )
    return runtime, members


def publish(rig, runtime, members):
    rig.set(
        "sensor.home_lighting_manager_shadow_health",
        "observing",
        {
            "command_authority": False,
            "manual_precedence_entities": list(members),
            "reconciliation_protected_entities": runtime.reconciliation_protected_entities(),
            "last_mutation_reason": runtime.engine.last_mutation_reason,
            "effective_ownership": effective_ownership(
                runtime.engine,
                members,
                {
                    g: m
                    for s, g in GROUPS.items()
                    if (m := rig.members[s]) and set(m) & set(members)
                },
            ),
        },
    )


async def evaluate(rig, surface):
    rig.calls.clear()
    await rig.run(EVALUATE[surface])


def touched(rig):
    targets = set()
    for service, data in rig.calls:
        if service.startswith(("light.", "hue.")):
            target = data.get("entity_id", ())
            targets.update([target] if isinstance(target, str) else target)
    return targets


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", GROUPS)
@pytest.mark.parametrize("owner", ["daily", "holiday"])
async def test_full_group_sequence_crosses_actual_renderer_and_reconciliation(rig, surface, owner):
    runtime, members = setup(rig, surface, owner)
    extra = "light.unmanaged_hue_extra"
    rig.members[surface].append(extra)
    rig.set(extra, "on")
    assert runtime.observe_operation(operation(1, members=members, group=GROUPS[surface])).mutated
    publish(rig, runtime, members)
    for e in members:
        rig.set(e, "off")
    first = runtime.observe_operation(
        operation(2, members=members, group=GROUPS[surface], kind="off")
    )
    assert first.reason == "released_to_hlm"
    publish(rig, runtime, members)
    await evaluate(rig, surface)
    assert all(rig.get(e).state == "on" for e in members)
    assert runtime.engine.group_off_sequences()[GROUPS[surface]] == tuple(sorted(members))
    for e in members:
        rig.set(e, "off")
    second = runtime.observe_operation(
        operation(3, members=members, group=GROUPS[surface], kind="off")
    )
    assert second.reason == "created_group_manual_off"
    publish(rig, runtime, members)
    await evaluate(rig, surface)
    assert all(rig.get(e).state == "off" for e in members)
    assert not (set(members) & touched(rig))
    assert not any(s == "scene.turn_on" for s, _ in rig.calls)
    owners = await rig.renderer.owners()
    assert owners[surface]["owner"] == owner
    assert all(any(layer.owner == owner for layer in runtime.engine.layers(e)) for e in members)
    assert not runtime.engine.layers(extra)
    check, current = await rig.renderer.inspect()
    assert not [c for c in check.commands if c.surface == surface]
    assert not [i for i in check.issues if i["surface"] == surface]
    assert set(current[surface]["manual_off_entities"]) == set(members)
    assert (
        rig.get("sensor.home_lighting_manager_shadow_health").attributes["command_authority"]
        is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", GROUPS)
@pytest.mark.parametrize("owner", ["daily", "holiday"])
async def test_mixed_appearance_and_manual_off_never_receive_automatic_commands(
    rig, surface, owner
):
    runtime, members = setup(rig, surface, owner)
    off = members[0]
    runtime.observe_operation(operation(1, members=(off,), kind="off"))
    protected = {off}
    if len(members) > 1:
        manual = members[1]
        runtime.observe_operation(operation(2, members=(manual,)))
        rig.set(manual, "on", {"brightness": 91, "xy_color": [0.3, 0.15]})
        protected.add(manual)
    publish(rig, runtime, members)
    before = {e: rig.get(e) for e in protected}
    await evaluate(rig, surface)
    assert not (protected & touched(rig))
    assert not any(s == "scene.turn_on" for s, _ in rig.calls)
    for e in protected:
        assert rig.get(e).state == before[e].state
        assert dict(rig.get(e).attributes) == dict(before[e].attributes)
    assert all(rig.get(e).state == "on" for e in set(members) - protected)


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", GROUPS)
async def test_individual_release_second_off_and_reset_rejoin_current_owner(rig, surface):
    runtime, members = setup(rig, surface)
    entity = members[0]
    runtime.observe_operation(operation(1, members=(entity,)))
    publish(rig, runtime, members)
    rig.set(entity, "off")
    runtime.observe_operation(operation(2, members=(entity,), kind="off"))
    publish(rig, runtime, members)
    await evaluate(rig, surface)
    assert rig.get(entity).state == "on"
    runtime.observe_operation(operation(3, members=(entity,), kind="off"))
    rig.set(entity, "off")
    publish(rig, runtime, members)
    await evaluate(rig, surface)
    assert rig.get(entity).state == "off"
    runtime.reset_homeowner_control()
    publish(rig, runtime, members)
    await evaluate(rig, surface)
    assert rig.get(entity).state == "on"
    assert not runtime.engine.group_off_sequences()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["reset", "nightly", "generation"])
async def test_changed_projection_during_hue_read_cancels_old_scene_plan(rig, boundary):
    runtime, members = setup(rig, "backyard")
    publish(rig, runtime, members)
    original = rig.renderer.hue.read

    async def read(scenes, surfaces):
        if boundary == "reset":
            runtime.reset_homeowner_control()
        elif boundary == "nightly":
            runtime.engine.expire_boundary(NIGHTLY_BOUNDARY)
        else:
            runtime.engine.next_generation()
        publish(rig, runtime, members)
        return await original(scenes, surfaces)

    rig.renderer.hue.read = read
    await evaluate(rig, "backyard")
    assert not rig.lights() and not touched(rig)
    assert rig.renderer.runner.diag["last_render"]["reason"] == "stale_plan"


@pytest.mark.asyncio
async def test_diagnostic_revision_churn_does_not_abort_surface_off(rig):
    runtime, members = setup(rig, "backyard", "off")
    rig.set("input_boolean.home_lighting_backyard_window", "off")
    for entity in members:
        rig.set(entity, "on")
    publish(rig, runtime, members)
    churned = False

    async def diagnostic_churn(call):
        nonlocal churned
        if call.domain == "light" and call.service == "turn_off" and not churned:
            churned = True
            runtime.engine.invalidate_work("guarded_physical_telemetry")
            publish(rig, runtime, members)

    rig.before_service = diagnostic_churn
    await rig.hass.services.async_call(
        "home_lighting_reconciliation",
        "render",
        {
            "surface": "backyard",
            "command": "light.turn_off",
            "entity_id": "light.backyard",
            "parameters": {"transition": 0},
        },
        blocking=True,
    )
    assert churned
    assert all(rig.get(e).state == "off" for e in members)
    assert rig.renderer.runner.diag["last_render"]["reason"] == "selective_light_command"


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", GROUPS)
async def test_unavailable_is_not_off_and_not_replayed(rig, surface):
    runtime, members = setup(rig, surface)
    entity = members[0]
    op = operation(1, members=(entity,), kind="off")
    runtime.observe_operation(
        replace(op, members=tuple(replace(m, available=False) for m in op.members))
    )
    rig.set(entity, "unavailable")
    publish(rig, runtime, members)
    await evaluate(rig, surface)
    assert entity not in touched(rig)
    assert not any(s == "scene.turn_on" for s, _ in rig.calls)
    assert runtime.engine.resolve(entity).layer.kind == LayerKind.AUTOMATIC
    rig.set(entity, "off")
    assert rig.get(entity).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("surface,sync", [("main_area", "living_room"), ("backyard", "backyard")])
async def test_sync_structurally_blocks_shared_renderer(rig, surface, sync):
    runtime, members = setup(rig, surface)
    publish(rig, runtime, members)
    rig.set("binary_sensor.hue_bridge_" + sync, "on")
    await evaluate(rig, surface)
    assert not (set(members) & touched(rig))


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", GROUPS)
async def test_full_manual_appearance_is_not_off_and_stale_helper_cannot_block_release(
    rig, surface
):
    runtime, members = setup(rig, surface)
    runtime.observe_operation(operation(1, members=members, group=GROUPS[surface]))
    for e in members:
        rig.set(e, "on", {"brightness": 73})
    rig.set("input_boolean.home_lighting_manual_" + surface, "on")
    publish(rig, runtime, members)
    await evaluate(rig, surface)
    assert not touched(rig)
    assert not any(s == "scene.turn_on" for s, _ in rig.calls)
    assert all(rig.get(e).attributes["brightness"] == 73 for e in members)
    runtime.observe_operation(operation(2, members=members, group=GROUPS[surface], kind="off"))
    for e in members:
        rig.set(e, "off")
    publish(rig, runtime, members)
    await evaluate(rig, surface)
    assert all(rig.get(e).state == "on" for e in members)


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", GROUPS)
async def test_nightly_expiration_projection_and_manual_off_drift_verification(rig, surface):
    runtime, members = setup(rig, surface)
    entity = members[0]
    runtime.observe_operation(operation(1, members=(entity,), kind="off"))
    publish(rig, runtime, members)
    rig.set(entity, "on")  # Drift is not intent. Verifier enforces existing accepted OFF.
    check, _ = await rig.renderer.inspect()
    commands = [c for c in check.commands if c.entity == entity]
    assert len(commands) == 1 and commands[0].service == "light.turn_off"
    assert runtime.engine.resolve(entity).layer.kind == LayerKind.MANUAL_OFF
    runtime.engine.expire_boundary(NIGHTLY_BOUNDARY)
    publish(rig, runtime, members)
    assert not rig.get("sensor.home_lighting_manager_shadow_health").attributes[
        "effective_ownership"
    ]["entities"][entity]["protected"]
    await evaluate(rig, surface)
    assert rig.get(entity).state == "on"
    assert set(runtime.export_persistence()) == {"version", "layers", "suppressed_sessions"}


@pytest.mark.asyncio
async def test_newer_intent_during_selective_scene_stops_remaining_writes(rig):
    runtime, members = setup(rig, "backyard")
    runtime.observe_operation(operation(1, members=(members[0],), kind="off"))
    publish(rig, runtime, members)
    applied = []

    async def apply(info, protected=(), valid=None):
        for e in info["actions"]:
            if e in protected:
                continue
            if not await valid():
                break
            applied.append(e)
            runtime.observe_operation(operation(2, members=members, group=GROUPS["backyard"]))
            publish(rig, runtime, members)
        return applied

    rig.renderer.hue.apply_actions = apply
    await evaluate(rig, "backyard")
    assert len(applied) == 1


@pytest.mark.asyncio
async def test_awaited_guard_new_manual_prevents_scene_dispatch(rig):
    runtime, members = setup(rig, "backyard")
    publish(rig, runtime, members)

    async def new_intent(call):
        if call.domain == "input_boolean" and call.service == "turn_on":
            assert runtime.observe_operation(
                operation(runtime.reserve_sequence(), members=members, group=GROUPS["backyard"])
            ).mutated
            publish(rig, runtime, members)

    rig.before_service = new_intent
    await evaluate(rig, "backyard")
    assert not touched(rig) and not rig.lights()


@pytest.mark.asyncio
async def test_missing_or_invalid_projection_never_dispatches(rig):
    rig.hass.states.async_remove("sensor.home_lighting_manager_shadow_health")
    await evaluate(rig, "backyard")
    assert not rig.lights()
    rig.set(
        "sensor.home_lighting_manager_shadow_health",
        "observing",
        {
            "command_authority": False,
            "effective_ownership": {"version": 1, "entities": {}},
        },
    )
    await evaluate(rig, "backyard")
    assert not rig.lights()


@pytest.mark.asyncio
async def test_direct_spa_write_cannot_bypass_manual_off(rig):
    from test_ownership import automation

    runtime, members = setup(rig, "backyard")
    spa = "light.backyard_spa_strip_lights"
    runtime.observe_operation(operation(1, members=(spa,), kind="off"))
    publish(rig, runtime, members)
    rig.set("input_boolean.spa_gauge_active", "on")
    rig.set(
        "climate.poolos_native_intellicenter_hot_tub_thermostat",
        "heat",
        {
            "current_temperature": 95,
            "temperature": 100,
            "hvac_action": "idle",
        },
    )
    automation(rig, "spa_gauge_temperature_color.yaml", "spa_gauge_temperature_color", fast=True)
    rig.calls.clear()
    await rig.run("spa_gauge_temperature_color")
    assert spa not in touched(rig)
    assert rig.get(spa).state == "off"


@pytest.mark.asyncio
async def test_49ers_flash_does_not_touch_protected_members(rig):
    from types import SimpleNamespace

    from test_ownership import automation

    runtime, members = setup(rig, "main_area", "49ers")
    protected = members[0]
    runtime.observe_operation(operation(1, members=(protected,), kind="off"))
    publish(rig, runtime, members)
    rig.set("sensor.nfl_san_francisco_49ers", "IN")
    automation(
        rig,
        "49ers_live_game_lighting.yaml",
        "49ers_live_game_lighting_and_score_celebration",
        fast=True,
    )
    await rig.run(
        "49ers_live_game_lighting_and_score_celebration",
        {
            "trigger": {
                "id": "score_change",
                "from_state": SimpleNamespace(state="IN", attributes={"team_score": 3}),
                "to_state": SimpleNamespace(state="IN", attributes={"team_score": 6}),
            }
        },
    )
    assert protected not in touched(rig)
    assert all(data.get("entity_id") != "light.celebration" for _, data in rig.calls)
    assert any("flash" in d for _, d in rig.calls)


@pytest.mark.asyncio
async def test_exposed_system_family_cannot_be_flattened_by_legacy_daily(rig):
    runtime, members = setup(rig, "backyard")
    runtime.engine.start_family("future_family", "session-a")
    for entity in members:
        runtime.engine.push(
            entity,
            OwnershipLayer(
                "family:" + entity,
                "future_family",
                LayerKind.AUTOMATIC,
                1,
                0,
                family="future_family",
                session_id="session-a",
                precedence=150,
            ),
        )
    publish(rig, runtime, members)
    await evaluate(rig, "backyard")
    assert not touched(rig) and not rig.lights()
    check, _ = await rig.renderer.inspect()
    assert not [c for c in check.commands if c.surface == "backyard"]
    runtime.engine.suppress_family("future_family", "session-a", "homeowner_override")
    publish(rig, runtime, members)
    await evaluate(rig, "backyard")
    assert all(rig.get(e).state == "on" for e in members)


def test_projection_changes_on_new_runtime_but_never_reconstructs_from_physical_off():
    first = ShadowRuntime()
    first.observe_operation(operation(1, members=("light.test",), kind="off"))
    initial = effective_ownership(first.engine, ["light.test"])
    assert initial["entities"]["light.test"]["kind"] == "manual_off"
    restored = ShadowRuntime()
    current = effective_ownership(restored.engine, ["light.test"])
    assert current["authority_id"] != initial["authority_id"]
    assert current["entities"]["light.test"]["kind"] is None
    assert current["entities"]["light.test"]["desired"] is None
    assert not restored.engine.group_off_sequences()


@pytest.mark.asyncio
async def test_render_parameters_cannot_override_filtered_target_scope(rig):
    runtime, members = setup(rig, "main_area")
    runtime.observe_operation(operation(1, members=(members[0],), kind="off"))
    publish(rig, runtime, members)
    await rig.hass.services.async_call(
        "home_lighting_reconciliation",
        "render",
        {
            "surface": "main_area",
            "command": "light.turn_on",
            "entity_id": members[1],
            "parameters": {"entity_id": members[0]},
        },
        blocking=True,
    )
    assert not rig.lights()
    assert (
        rig.renderer.runner.diag["last_render"]["reason"]
        == "Renderer parameters cannot override target scope"
    )


@pytest.mark.asyncio
async def test_obsolete_scene_cannot_run_under_same_owner_after_holiday_changes(rig):
    runtime, members = setup(rig, "backyard", "holiday")
    publish(rig, runtime, members)
    await rig.hass.services.async_call(
        "home_lighting_reconciliation",
        "render",
        {
            "surface": "backyard",
            "expected_owner": "holiday",
            "command": "scene.turn_on",
            "entity_id": "scene.holiday_backyard_jolly",
        },
        blocking=True,
    )
    assert not rig.lights()
    assert rig.renderer.runner.diag["last_render"]["reason"] == "obsolete_scene_selection"


@pytest.mark.asyncio
async def test_renderer_marks_each_leaf_before_physical_light_dispatch(rig):
    runtime, members = setup(rig, "main_area")
    publish(rig, runtime, members)
    target = members[0]
    rig.calls.clear()
    await rig.hass.services.async_call(
        "home_lighting_reconciliation",
        "render",
        {
            "surface": "main_area",
            "command": "light.turn_on",
            "entity_id": target,
            "parameters": {},
        },
        blocking=True,
    )
    mark_indexes = [
        i for i, (service, data) in enumerate(rig.calls)
        if service == "home_lighting_manager.mark_command_consequence"
        and target in data.get("entity_ids", [])
    ]
    light_indexes = [
        i for i, (service, data) in enumerate(rig.calls)
        if service == "light.turn_on" and data.get("entity_id") == target
    ]
    assert mark_indexes and light_indexes
    assert mark_indexes[0] < light_indexes[0]
