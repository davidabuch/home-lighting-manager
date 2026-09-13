from copy import deepcopy
from types import SimpleNamespace

import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from test_engine import world
from test_ownership import automation

from custom_components.home_lighting_reconciliation import Adapter
from custom_components.home_lighting_reconciliation.const import (
    CONTROL,
    EVALUATORS,
    TRANSIENTS,
)


def find_light_turn_on(sequence, entity_id):
    """Find a light.turn_on command recursively by target entity."""

    for step in sequence:
        if not isinstance(step, dict):
            continue

        if step.get("action") == "light.turn_on":
            target = step.get("target", {}).get("entity_id")
            targets = target if isinstance(target, list) else [target]

            if entity_id in targets:
                return step

        for key in ("sequence", "parallel", "then", "else"):
            child = step.get(key)
            if isinstance(child, list):
                found = find_light_turn_on(child, entity_id)
                if found is not None:
                    return found

    return None

def adapter_for(rig):
    a = Adapter(rig.hass)
    args = world()
    for s in ["main_area", "front_eve", "path", "backyard"]:
        rig.set("input_boolean.home_lighting_" + s + "_window", "on")
    for entity in CONTROL:
        if not rig.get(entity):
            rig.set(
                entity, "2026-09-08T01:00:00+00:00" if entity.endswith("_last_recall") else "off"
            )
    for entity in {**TRANSIENTS, **EVALUATORS}:
        if not rig.get(entity):
            rig.set(entity, "on", {"current": 0})
    for entity, state in args[1].items():
        rig.set(entity, state["state"], state["attributes"])

    async def read(scenes, surfaces):
        return args[3], args[4]

    a.hue.read = read
    return a, args



@pytest.mark.asyncio
async def test_adapter_selective_scene_repair_uses_hue_executor(rig):
    """Selective scene repair uses Hue V2 and protects the Manual member."""

    a, args = adapter_for(rig)

    protected = "light.kitchen_kitchen_left_cabinet_light"

    rig.set(
        "input_boolean.home_lighting_manual_kitchen_left_cabinet",
        "on",
    )
    rig.set("input_boolean.home_lighting_holiday_active", "on")
    rig.set("input_text.home_lighting_holiday_key", "halloween")

    owners = await a.owners()

    assert owners["main_area"]["owner"] == "holiday"
    assert protected in owners["main_area"]["manual_entities"]

    scene = owners["main_area"]["scene"]
    members = list(args[3]["main_area"])

    assert protected in members

    scene_info = {
        "actions": {
            entity: {
                "rid": entity,
                "action": {"on": {"on": True}},
            }
            for entity in members
        },
        "latest": True,
    }

    async def read(scenes, surfaces):
        metadata = {}
        if scene in scenes:
            metadata[scene] = scene_info
        return {"main_area": members}, metadata

    a.hue.read = read

    # Protected member may disagree freely. At least one automatic member
    # must disagree so reconciliation requests a selective scene repair.
    for entity in members:
        rig.set(entity, "off", {})

    check, current = await a.inspect()

    selective = [
        command
        for command in check.commands
        if command.service == "hue.apply_scene_actions"
    ]

    assert len(selective) == 1
    command = selective[0]

    assert command.surface == "main_area"
    assert command.entity == scene
    assert command.data == {"protected": [protected]}

    calls = []

    async def apply_actions(actual_scene_info, protected=()):
        calls.append((actual_scene_info, tuple(protected)))
        return [
            entity
            for entity in members
            if entity not in set(protected)
        ]

    a.hue.apply_actions = apply_actions

    assert await a.repair(command, current, lambda: True)

    assert calls == [
        (
            scene_info,
            (protected,),
        )
    ]

    assert not any(
        service == "scene.turn_on"
        and data.get("entity_id") == scene
        for service, data in rig.lights()
    )


@pytest.mark.asyncio
async def test_adapter_selective_scene_repair_fails_closed_without_scene_info(rig):
    """Loss of authoritative Hue metadata cancels a pending selective repair."""

    a, args = adapter_for(rig)

    protected = "light.kitchen_kitchen_left_cabinet_light"

    rig.set(
        "input_boolean.home_lighting_manual_kitchen_left_cabinet",
        "on",
    )
    rig.set("input_boolean.home_lighting_holiday_active", "on")
    rig.set("input_text.home_lighting_holiday_key", "halloween")

    owners = await a.owners()
    scene = owners["main_area"]["scene"]
    members = list(args[3]["main_area"])

    assert protected in members

    scene_info = {
        "actions": {
            entity: {
                "rid": entity,
                "action": {"on": {"on": True}},
            }
            for entity in members
        },
        "latest": True,
    }

    metadata_available = True

    async def read(scenes, surfaces):
        if metadata_available and scene in scenes:
            return {"main_area": members}, {scene: scene_info}
        return {"main_area": members}, {}

    a.hue.read = read

    for entity in members:
        rig.set(entity, "off", {})

    check, current = await a.inspect()

    selective = [
        command
        for command in check.commands
        if command.service == "hue.apply_scene_actions"
    ]

    assert len(selective) == 1
    command = selective[0]

    # Authoritative evidence disappears before repair's mandatory
    # reinspection. The stale command must be rejected.
    metadata_available = False

    called = False

    async def apply_actions(scene_info, protected=()):
        nonlocal called
        called = True
        return []

    a.hue.apply_actions = apply_actions

    assert not await a.repair(command, current, lambda: True)
    assert called is False

    assert not any(
        service == "scene.turn_on"
        and data.get("entity_id") == scene
        for service, data in rig.lights()
    )


@pytest.mark.asyncio
async def test_adapter_selective_scene_repair_fails_closed_on_hue_error(rig):
    """Hue V2 executor errors never fall back to whole-scene recall."""

    a, args = adapter_for(rig)

    protected = "light.kitchen_kitchen_left_cabinet_light"

    rig.set(
        "input_boolean.home_lighting_manual_kitchen_left_cabinet",
        "on",
    )
    rig.set("input_boolean.home_lighting_holiday_active", "on")
    rig.set("input_text.home_lighting_holiday_key", "halloween")

    owners = await a.owners()
    scene = owners["main_area"]["scene"]
    members = list(args[3]["main_area"])

    assert protected in members

    scene_info = {
        "actions": {
            entity: {
                "rid": entity,
                "action": {"on": {"on": True}},
            }
            for entity in members
        },
        "latest": True,
    }

    async def read(scenes, surfaces):
        metadata = {}
        if scene in scenes:
            metadata[scene] = scene_info
        return {"main_area": members}, metadata

    a.hue.read = read

    for entity in members:
        rig.set(entity, "off", {})

    check, current = await a.inspect()

    selective = [
        command
        for command in check.commands
        if command.service == "hue.apply_scene_actions"
    ]

    assert len(selective) == 1

    async def apply_actions(scene_info, protected=()):
        raise ValueError("Hue light action failed")

    a.hue.apply_actions = apply_actions

    assert not await a.repair(
        selective[0],
        current,
        lambda: True,
    )

    assert not any(
        service == "scene.turn_on"
        and data.get("entity_id") == scene
        for service, data in rig.lights()
    )


@pytest.mark.asyncio
async def test_adapter_minimal_repair_uses_guard_without_asserting_manual(rig):
    a, args = adapter_for(rig)
    entity = "light.kitchen_kitchen_right_cabinet_lights"
    rig.set(entity, "off", args[1][entity]["attributes"])
    check, owners = await a.inspect()
    assert len(check.commands) == 1
    assert await a.repair(check.commands[0], owners, lambda: True)
    assert rig.get("input_boolean.home_lighting_ha_guard_main_area").state == "on"
    assert rig.get("input_boolean.home_lighting_manual_main_area").state == "off"
    assert len(rig.lights()) == 1
    assert not (await a.inspect())[0].commands

@pytest.mark.asyncio
async def test_guard_await_cannot_hide_new_manual_intent(rig):
    a, args = adapter_for(rig)
    entity = "light.kitchen_kitchen_right_cabinet_lights"
    rig.set(entity, "off", args[1][entity]["attributes"])
    check, owners = await a.inspect()

    async def new_intent(call):
        if call.domain == "input_boolean" and call.service == "turn_on":
            rig.set(
                "input_boolean.home_lighting_manual_kitchen_right_cabinet",
                "on",
            )

    rig.before_service = new_intent
    assert not await a.repair(check.commands[0], owners, lambda: True)
    assert not rig.lights()

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename,name",
    [
        ("pool_ready_light_alert.yaml", "pool_and_hot_tub_ready_light_alert"),
        ("powerwall_light_alert.yaml", "powerwall_flash_house_lights_on_grid_failure"),
    ],
)
@pytest.mark.parametrize("owner", ["daily", "holiday", "49ers", "manual"])
async def test_real_alert_script_suppresses_checks_through_restore(rig, filename, name, owner):
    a, args = adapter_for(rig)
    if owner == "manual":
        rig.set(
            "input_boolean.home_lighting_manual_kitchen_right_cabinet",
            "on",
        )
    elif owner == "holiday":
        rig.set("input_boolean.home_lighting_holiday_active", "on")
        rig.set("input_text.home_lighting_holiday_key", "halloween")
    elif owner == "49ers":
        rig.set("sensor.nfl_san_francisco_49ers", "IN")
    for e in [
        "light.kitchen_kitchen",
        "light.living_room_living_room",
        "light.dining_room_dining_room_main_lights_1",
    ]:
        rig.set(e, "on", {"brightness": 71})
    automation(rig, filename, name, fast=True)
    inspections = []

    async def inspect_during_command(call):
        if call.domain in ("light", "scene"):
            check, owners = await a.inspect()
            inspections.append(check)
            assert "main_area" in check.skipped
            assert not any(c.surface == "main_area" for c in check.commands)

    rig.before_service = inspect_during_command
    await rig.run(name, {"trigger": {"id": "hot_tub"}})
    assert inspections
    assert "main_area" not in a.suppression()
    assert rig.lights()[-1][0] == "scene.turn_on"  # Original snapshot restoration retained.
    final_main = (await a.owners())["main_area"]

    if owner == "manual":
        assert final_main["owner"] == "daily"
        assert final_main["manual_entities"] == [
            "light.kitchen_kitchen_right_cabinet_lights"
        ]
    else:
        assert final_main["owner"] == owner

@pytest.mark.asyncio
async def test_lifecycle_invalidation_and_own_recall_does_not_reset_retry_budget(rig):
    a, args = adapter_for(rig)
    a.started = True
    unsub = rig.hass.bus.async_listen(EVENT_STATE_CHANGED, a.changed)
    entity = "automation.pool_and_hot_tub_ready_light_alert"
    rig.set(entity, "on", {"current": 1})
    await rig.hass.async_block_till_done()
    start = a.runner.generation
    assert start > 0
    rig.set(entity, "on", {"current": 0})
    await rig.hass.async_block_till_done()
    assert a.runner.generation > start
    rig.set("input_boolean.home_lighting_ha_guard_main_area", "on")
    old = a.runner.generation
    rig.set("sensor.main_area_last_recall", "2026-09-08T02:00:00+00:00")
    await rig.hass.async_block_till_done()
    assert a.runner.generation == old
    a.runner.close()
    unsub()

@pytest.mark.asyncio
async def test_missing_transient_state_fails_closed(rig):
    a, args = adapter_for(rig)
    rig.hass.states.async_remove("automation.powerwall_flash_house_lights_on_grid_failure")
    rig.set("light.kitchen_kitchen_right_cabinet_lights", "off")
    check, _ = await a.inspect()
    assert not any(c.surface == "main_area" for c in check.commands)
    assert any(i.get("error", "").startswith("missing lifecycle") for i in check.issues)

@pytest.mark.asyncio
async def test_actual_score_sequence_suppressed_and_restores(rig):
    a, args = adapter_for(rig)
    rig.set("sensor.nfl_san_francisco_49ers", "IN")
    automation(
        rig,
        "49ers_live_game_lighting.yaml",
        "49ers_live_game_lighting_and_score_celebration",
        fast=True,
    )
    checks = []

    async def inspect_during_command(call):
        if call.domain == "light" and "flash" in call.data:
            check, _ = await a.inspect()
            checks.append(check)
            assert {"main_area", "front_eve"}.issubset(check.skipped)

    rig.before_service = inspect_during_command
    trigger = {
        "id": "score_change",
        "from_state": SimpleNamespace(state="IN", attributes={"team_score": 3}),
        "to_state": SimpleNamespace(state="IN", attributes={"team_score": 10}),
    }
    await rig.run("49ers_live_game_lighting_and_score_celebration", {"trigger": trigger})
    assert len(checks) == 7
    assert [d["entity_id"] for s, d in rig.lights() if s == "scene.turn_on"] == [
        "scene.front_eve_zone_49ers",
        "scene.holiday_main_area_49ers",
    ]

@pytest.mark.asyncio
async def test_manual_off_event_invalidates_before_release_settles(rig):
    a, args = adapter_for(rig)
    a.started = True
    rig.set("input_boolean.home_lighting_manual_main_area", "on")
    rig.set("light.holiday_main_area", "on")
    a.runner.schedule("previous Daily")
    unsub = rig.hass.bus.async_listen(EVENT_STATE_CHANGED, a.changed)
    token = a.runner.generation
    rig.set("light.holiday_main_area", "off")
    await rig.hass.async_block_till_done()
    assert a.runner.generation > token
    assert (
        rig.get("input_boolean.home_lighting_manual_main_area").state == "on"
    )  # detector has not released yet
    a.runner.close()
    unsub()

@pytest.mark.asyncio
async def test_loaded_baseline_change_supersedes_cached_configuration(rig):
    from conftest import PACKAGE

    a, args = adapter_for(rig)
    new = deepcopy(PACKAGE["script"]["home_lighting_apply_main_area_baseline"])
    command = find_light_turn_on(
        new["sequence"],
        "light.kitchen_kitchen_right_cabinet_lights",
    )
    assert command is not None
    command["data"]["brightness"] = 220
    rig.install_script("home_lighting_apply_main_area_baseline", new)
    check, _ = await a.inspect()
    assert len(check.commands) == 1
    assert check.commands[0].entity == "light.kitchen_kitchen_right_cabinet_lights"
    assert check.commands[0].data["brightness"] == 220

@pytest.mark.asyncio
async def test_baseline_reload_during_guard_cancels_old_command(rig):
    from conftest import PACKAGE

    a, args = adapter_for(rig)
    entity = "light.kitchen_kitchen_right_cabinet_lights"
    rig.set(entity, "off", args[1][entity]["attributes"])
    check, owners = await a.inspect()

    async def reload_during_guard(call):
        if call.domain == "input_boolean" and call.service == "turn_on":
            new = deepcopy(
                PACKAGE["script"]["home_lighting_apply_main_area_baseline"]
            )
            command = find_light_turn_on(
                new["sequence"],
                "light.kitchen_kitchen_right_cabinet_lights",
            )
            assert command is not None
            command["data"]["brightness"] = 220
            rig.install_script(
                "home_lighting_apply_main_area_baseline",
                new,
            )

    rig.before_service = reload_during_guard
    assert not await a.repair(check.commands[0], owners, lambda: True)
    assert not rig.lights()

@pytest.mark.asyncio
async def test_recovery_during_guard_remains_a_noop(rig):
    a, args = adapter_for(rig)
    entity = "light.kitchen_kitchen_right_cabinet_lights"
    rig.set(entity, "off", args[1][entity]["attributes"])
    check, owners = await a.inspect()

    async def recover(call):
        if call.domain == "input_boolean" and call.service == "turn_on":
            rig.set(entity, "on", args[1][entity]["attributes"])

    rig.before_service = recover
    assert not await a.repair(check.commands[0], owners, lambda: True)
    assert not rig.lights()
