import asyncio
import itertools

import pytest
import yaml
from conftest import PACKAGE, ROOT


@pytest.mark.asyncio
async def test_priority_matrix_and_independent_surfaces(rig):
    for sync, manual, game, holiday, daily, spa in itertools.product((False, True), repeat=6):
        for s in ["main_area", "front_eve", "path", "backyard"]:
            rig.set("input_boolean.home_lighting_manual_" + s, "on" if manual else "off")
            rig.set("input_boolean.home_lighting_" + s + "_window", "on" if daily else "off")
        for s in ["living_room", "backyard"]:
            rig.set("binary_sensor.hue_bridge_" + s, "on" if sync else "off")
        rig.set("input_boolean.spa_gauge_active", "on" if spa else "off")
        rig.set("input_boolean.home_lighting_holiday_active", "on" if holiday else "off")
        rig.set("input_text.home_lighting_holiday_key", "halloween")
        rig.set("sensor.nfl_san_francisco_49ers", "IN" if game else "POST")
        owners = (await rig.run("home_lighting_resolve_owners")).service_response
        lower = "holiday" if holiday else "daily" if daily else "off"
        assert owners["main_area"]["owner"] == (
            "sync" if sync else "manual" if manual else "49ers" if game else lower
        )
        assert owners["front_eve"]["owner"] == ("manual" if manual else "49ers" if game else lower)
        assert owners["path"]["owner"] == ("manual" if manual else lower)
        assert owners["backyard"]["owner"] == (
            "sync" if sync else "manual" if manual else "spa" if spa else lower
        )
    # A Main Area exception must not consume Front Eve's game ownership.
    rig.set("binary_sensor.hue_bridge_living_room", "off")
    rig.set("input_boolean.home_lighting_manual_front_eve", "off")
    rig.calls.clear()
    await rig.run("home_lighting_evaluate_and_apply")
    assert [d["entity_id"] for _, d in rig.lights()] == ["scene.front_eve_zone_49ers"]


def automation(rig, filename, name, fast=False):
    a = yaml.safe_load((ROOT / "automations" / filename).read_text())[0]
    return rig.install_script(
        name,
        {"alias": a["alias"], "sequence": a["actions"], "mode": a["mode"], "max": a.get("max", 10)},
        domain="automation",
        fast=fast,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["right", "left"])
@pytest.mark.parametrize("mode", ["xy", "color_temp", "off"])
async def test_two_doors_preserve_first_snapshot(rig, first, mode):
    automation(rig, "liquor_cabinet_display_lighting.yaml", "liquor_cabinet_display_lighting")
    bulb = "light.living_room_liquor_cabinet_light"
    rig.set("input_boolean.home_lighting_manual_main_area", "on")
    attrs = {
        "brightness": 91,
        "color_mode": mode,
        "xy_color": [0.21, 0.32],
        "color_temp_kelvin": 3210,
    }
    rig.set(bulb, "off" if mode == "off" else "on", attrs)
    door = {
        "right": "binary_sensor.liquor_cabinet_door_r",
        "left": "binary_sensor.liquor_cabinet_l_door",
    }
    second = "left" if first == "right" else "right"

    async def event(which, opened):
        rig.set(door[which], "on" if opened else "off")
        await rig.run(
            "liquor_cabinet_display_lighting",
            {"trigger": {"id": which + ("_opened" if opened else "_closed")}},
        )

    await event(first, True)
    assert rig.get(bulb).attributes["brightness"] == 255
    assert rig.get(bulb).attributes["color_temp_kelvin"] == 4000
    await event(second, True)
    assert rig.get("input_number.home_lighting_liquor_snapshot_brightness").state == "91"
    await event(first, False)
    assert rig.get(bulb).attributes["brightness"] == 255
    await event(second, False)
    assert rig.get("input_boolean.home_lighting_liquor_snapshot_valid").state == "off"
    assert rig.get(bulb).state == ("off" if mode == "off" else "on")
    if mode != "off":
        assert rig.get(bulb).attributes["brightness"] == 91
        key = "xy_color" if mode == "xy" else "color_temp_kelvin"
        assert rig.get(bulb).attributes[key] == attrs[key]


@pytest.mark.asyncio
async def test_second_open_during_capture_cannot_cancel_first(rig):
    automation(rig, "liquor_cabinet_display_lighting.yaml", "liquor_cabinet_display_lighting")
    rig.set("input_boolean.home_lighting_manual_main_area", "on")
    rig.set(
        "light.living_room_liquor_cabinet_light",
        "on",
        {"brightness": 71, "xy_color": [0.2, 0.3], "color_mode": "xy"},
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold(call):
        if (
            call.data.get("entity_id") == "input_number.home_lighting_liquor_snapshot_brightness"
            and not entered.is_set()
        ):
            entered.set()
            await release.wait()

    rig.before_service = hold
    rig.set("binary_sensor.liquor_cabinet_door_r", "on")
    first = asyncio.create_task(
        rig.run("liquor_cabinet_display_lighting", {"trigger": {"id": "right_opened"}})
    )
    await entered.wait()
    rig.set("binary_sensor.liquor_cabinet_l_door", "on")
    second = asyncio.create_task(
        rig.run("liquor_cabinet_display_lighting", {"trigger": {"id": "left_opened"}})
    )
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)
    assert rig.get("input_number.home_lighting_liquor_snapshot_brightness").state == "71"


@pytest.mark.asyncio
async def test_failed_liquor_restore_retains_snapshot(rig):
    automation(rig, "liquor_cabinet_display_lighting.yaml", "liquor_cabinet_display_lighting")
    rig.set("input_boolean.home_lighting_manual_main_area", "on")
    rig.set(
        "light.living_room_liquor_cabinet_light",
        "on",
        {"brightness": 71, "color_mode": "xy", "xy_color": [0.2, 0.3]},
    )
    rig.set("binary_sensor.liquor_cabinet_door_r", "on")
    await rig.run("liquor_cabinet_display_lighting", {"trigger": {"id": "right_opened"}})
    rig.set("binary_sensor.liquor_cabinet_door_r", "off")
    rig.fail_light = True
    with pytest.raises(RuntimeError):
        await rig.run("liquor_cabinet_display_lighting", {"trigger": {"id": "right_closed"}})
    assert rig.get("input_boolean.home_lighting_liquor_snapshot_valid").state == "on"


@pytest.mark.asyncio
async def test_spa_manual_no_commands_and_normal_ready_blinks(rig):
    automation(rig, "spa_gauge_temperature_color.yaml", "spa_gauge_temperature_color", fast=True)
    rig.set("input_boolean.spa_gauge_active", "on")
    rig.set(
        "climate.poolos_native_intellicenter_hot_tub_thermostat",
        "heat",
        {"current_temperature": 100, "temperature": 100, "hvac_action": "idle"},
    )
    rig.set("input_boolean.home_lighting_manual_backyard", "on")
    await rig.run("spa_gauge_temperature_color")
    assert not rig.lights()
    rig.set("input_boolean.home_lighting_manual_backyard", "off")
    await rig.run("spa_gauge_temperature_color")
    assert len(rig.lights()) == 11  # Five ON/OFF blinks, then steady ON.


@pytest.mark.asyncio
async def test_manual_assertion_mid_spa_sequence_stops_future_commands(rig):
    automation(rig, "spa_gauge_temperature_color.yaml", "spa_gauge_temperature_color", fast=True)
    rig.set("input_boolean.spa_gauge_active", "on")
    rig.set(
        "climate.poolos_native_intellicenter_hot_tub_thermostat",
        "heat",
        {"current_temperature": 100, "temperature": 100, "hvac_action": "idle"},
    )

    async def manual_after_first_command(call):
        if call.domain == "light":
            rig.set("input_boolean.home_lighting_manual_backyard", "on")

    rig.before_service = manual_after_first_command
    await rig.run("spa_gauge_temperature_color")
    assert len(rig.lights()) == 1


@pytest.mark.asyncio
async def test_backyard_sync_release_uses_poolos(rig):
    a = next(
        a for a in PACKAGE["automation"] if a["id"] == "home_lighting_backyard_sync_ownership_v1"
    )
    rig.install_script("sync_release", {"sequence": a["actions"]})
    rig.set("input_boolean.spa_gauge_active", "on")
    rig.set("climate.poolos_native_intellicenter_hot_tub_thermostat", "heat")
    await rig.run("sync_release", {"trigger": {"id": "sync_stopped"}})
    assert any(
        c[0] == "automation.trigger"
        and c[1]["entity_id"] == "automation.spa_gauge_temperature_color"
        for c in rig.calls
    )
    assert not rig.lights()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["main_area", "front_eve", "path", "backyard"])
async def test_raw_recall_triggers_manual_while_on_but_not_attribute_changes(rig, surface):
    from homeassistant.components.homeassistant.triggers.state import (
        async_attach_trigger,
    )

    a = next(
        a for a in PACKAGE["automation"] if a["id"] == "home_lighting_manual_ownership_detector_v1"
    )
    script = rig.install_script(
        "manual_detector", {"sequence": a["actions"], "mode": a["mode"], "max": 20}
    )
    sensor = "sensor." + surface + "_last_recall"
    t = next(t for t in a["triggers"] if t["entity_id"] == sensor)
    assert "to" in t and t["to"] is None
    group = {
        "main_area": "light.holiday_main_area",
        "front_eve": "light.front_eve_zone",
        "path": "light.holiday_path",
        "backyard": "light.holiday_backyard",
    }[surface]
    rig.set(group, "on")
    rig.set(sensor, "2026-09-08T01:00:00Z", {"scene_id": "original", "active": "dynamic_palette"})
    config = {"platform": "state", "entity_id": [sensor], "to": None}

    async def act(variables, context):
        await script.async_run(variables, context)

    unsub = await async_attach_trigger(
        rig.hass,
        config,
        act,
        {"trigger_data": {"id": t["id"]}, "variables": {}, "name": "manual detector"},
    )
    try:
        rig.set(sensor, "2026-09-08T01:00:00Z", {"scene_id": "original", "active": "inactive"})
        await rig.hass.async_block_till_done()
        assert rig.get("input_boolean.home_lighting_manual_" + surface).state == "off"
        rig.set(
            sensor, "2026-09-08T02:00:00Z", {"scene_id": "external", "active": "dynamic_palette"}
        )
        await rig.hass.async_block_till_done()
        assert rig.get("input_boolean.home_lighting_manual_" + surface).state == "on"
        assert not rig.lights()
        rig.set("input_boolean.home_lighting_manual_" + surface, "off")
        rig.set("input_boolean.home_lighting_ha_guard_" + surface, "on")
        rig.set(
            sensor, "2026-09-08T03:00:00Z", {"scene_id": "ha-owned", "active": "dynamic_palette"}
        )
        await rig.hass.async_block_till_done()
        assert rig.get("input_boolean.home_lighting_manual_" + surface).state == "off"
    finally:
        unsub()


@pytest.mark.asyncio
async def test_overnight_boundary_releases_owners_without_master_zone(rig):
    a = next(a for a in PACKAGE["automation"] if a["id"] == "home_lighting_daily_schedule_v1")
    assert len([t for t in a["triggers"] if t.get("trigger") == "sun" and not t.get("offset")]) == 1
    rig.install_script("daily_schedule_test", {"sequence": a["actions"], "mode": a["mode"]})
    for s in ["main_area", "front_eve", "path", "backyard"]:
        rig.set("input_boolean.home_lighting_manual_" + s, "on")
        rig.set("input_boolean.home_lighting_" + s + "_window", "on")
    rig.set("input_boolean.home_lighting_holiday_active", "on")
    await rig.run("daily_schedule_test", {"trigger": {"id": "overnight_boundary"}})
    result = (await rig.run("home_lighting_resolve_owners")).service_response
    assert all(result[s]["owner"] == "off" for s in ["main_area", "front_eve", "path", "backyard"])
    assert all(d.get("entity_id") != "light.holiday_lighting" for _, d in rig.lights())


@pytest.mark.asyncio
async def test_startup_reconstructs_evening_without_stealing_manual(rig, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from homeassistant.util import dt as dt_util

    evening = datetime(2026, 9, 8, 20, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    monkeypatch.setattr(dt_util, "now", lambda *args: evening)
    rig.set("sun.sun", "below_horizon", {"next_setting": "2026-09-10T02:20:00+00:00"})
    rig.set("sensor.home_holiday_calendar_tonight", "christmas")
    rig.set("sensor.home_holiday_calendar_previous_evening", "none")
    rig.set("input_boolean.home_lighting_manual_main_area", "on")
    rig.set("light.holiday_main_area", "on")
    a = next(a for a in PACKAGE["automation"] if a["id"] == "home_lighting_daily_schedule_v1")
    rig.install_script("startup_test", {"sequence": a["actions"], "mode": a["mode"]})
    await rig.run("startup_test", {"trigger": {"id": "startup_reconcile"}})
    owners = (await rig.run("home_lighting_resolve_owners")).service_response
    assert owners["main_area"]["owner"] == "manual"
    assert all(owners[s]["owner"] == "holiday" for s in ["front_eve", "path", "backyard"])
    assert all(
        rig.get("input_boolean.home_lighting_" + s + "_window").state == "on"
        for s in ["main_area", "front_eve", "path", "backyard"]
    )


@pytest.mark.asyncio
async def test_liquor_known_manual_scene_restores_native_scene(rig):
    automation(rig, "liquor_cabinet_display_lighting.yaml", "liquor_cabinet_display_lighting")
    rig.set("input_boolean.home_lighting_manual_main_area", "on")
    rig.set(
        "input_text.home_lighting_manual_main_area_scene_id", "5228223d-532f-4bef-b266-0ceceb780819"
    )
    rig.set(
        "light.living_room_liquor_cabinet_light",
        "on",
        {"brightness": 71, "color_mode": "xy", "xy_color": [0.2, 0.3]},
    )
    rig.set("binary_sensor.liquor_cabinet_door_r", "on")
    await rig.run("liquor_cabinet_display_lighting", {"trigger": {"id": "right_opened"}})
    rig.set("binary_sensor.liquor_cabinet_door_r", "off")
    await rig.run("liquor_cabinet_display_lighting", {"trigger": {"id": "right_closed"}})
    assert rig.lights()[-1] == ("scene.turn_on", {"entity_id": "scene.holiday_main_area_49ers"})
    assert not any(service == "scene.create" for service, _ in rig.calls)
