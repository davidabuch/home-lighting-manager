from copy import deepcopy

import pytest
from conftest import PACKAGE

from custom_components.home_lighting_reconciliation.engine import (
    LIQUOR,
    SURFACES,
    commands_in,
    verify,
)


def world():
    owners = {s: {"owner": "daily", "scene": ""} for s in SURFACES}
    owners["liquor_cabinet"] = {"owner": "daily"}
    states = {}
    members = {}
    scenes = {}
    for surface in SURFACES:
        members[surface] = []
        for service, entity, data in commands_in(
            PACKAGE["script"]["home_lighting_apply_" + surface + "_baseline"]["sequence"]
        ):
            if service == "scene.turn_on":
                group = [f"light.{surface}_{i}" for i in range(3)]
                members[surface] += group
                scenes[entity] = {"actions": dict.fromkeys(group, True), "latest": True}
                states.update(
                    {
                        e: {
                            "state": "on",
                            "attributes": {"brightness": i * 10 + 20, "xy_color": [0.1, 0.6]},
                        }
                        for i, e in enumerate(group)
                    }
                )
            else:
                members[surface].append(entity)
                states[entity] = {"state": "on", "attributes": deepcopy(data)}
    return owners, states, PACKAGE["script"], members, scenes


def test_correct_state_zero_commands_and_canonical_dropout_minimal():
    args = world()
    assert not verify(*args).commands
    assert not verify(*args).issues
    entity = "light.kitchen_kitchen_right_cabinet_lights"
    args[1][entity]["state"] = "off"
    result = verify(*args)
    assert len(result.commands) == 1
    assert result.commands[0].entity == entity
    assert result.commands[0].data["brightness"] == 255
    assert result.commands[0].data["color_temp_kelvin"] == 2724


@pytest.mark.parametrize("owner", ["daily", "holiday", "49ers", "manual"])
def test_liquor_white_correct_under_every_lower_owner(owner):
    args = world()
    owners, states, _, members, scenes = args
    owners["main_area"]["owner"] = owner
    owners["liquor_cabinet"]["owner"] = "door"
    if owner in ("holiday", "49ers"):
        owners["main_area"]["scene"] = "scene.example"
        scenes["scene.example"] = {
            "actions": dict.fromkeys(members["main_area"], True),
            "latest": True,
        }
    states[LIQUOR] = {"state": "on", "attributes": {"brightness": 255, "color_temp_kelvin": 4000}}
    assert not verify(*args).issues
    assert not verify(*args).commands


@pytest.mark.parametrize(
    "surface,owner",
    [
        ("main_area", "sync"),
        ("main_area", "manual"),
        ("front_eve", "manual"),
        ("path", "manual"),
        ("backyard", "sync"),
        ("backyard", "manual"),
        ("backyard", "spa"),
    ],
)
def test_higher_owner_no_commands(surface, owner):
    args = world()
    args[0][surface]["owner"] = owner
    for e in args[3][surface]:
        args[1][e]["state"] = "off"
    assert not any(c.surface == surface for c in verify(*args).commands)


def test_dynamic_does_not_compare_instantaneous_colour_or_brightness():
    args = world()
    for e in args[3]["path"]:
        args[1][e]["attributes"] = {"brightness": 1, "xy_color": [0.99, 0.01]}
    assert not verify(*args).commands
    args[1][args[3]["path"][0]]["state"] = "off"
    cmds = verify(*args).commands
    assert len(cmds) == 1 and cmds[0].service == "scene.turn_on"


def test_dynamic_intent_ambiguity_and_unavailability_do_not_recall():
    args = world()
    scene = next(iter(args[4]))
    args[4][scene]["latest"] = False
    args[1][args[3]["path"][0]]["state"] = "off"
    assert not verify(*args).commands
    args[4][scene]["latest"] = True
    args[1][args[3]["path"][1]]["state"] = "unavailable"
    assert not verify(*args).commands
    assert verify(*args).issues


@pytest.mark.parametrize("unavailable", ["unknown", "unavailable"])
def test_unavailable_static_member_not_commanded(unavailable):
    args = world()
    args[1][args[3]["main_area"][0]]["state"] = unavailable
    result = verify(*args)
    assert result.issues
    assert not result.commands


def test_off_preserves_open_liquor_and_no_broad_group_command():
    args = world()
    args[0]["main_area"]["owner"] = "off"
    args[0]["liquor_cabinet"]["owner"] = "door"
    args[1][LIQUOR] = {"state": "on", "attributes": {"brightness": 255, "color_temp_kelvin": 4000}}
    commands = verify(*args).commands
    assert len(commands) == len(args[3]["main_area"]) - 1
    assert all(c.entity != LIQUOR and c.service == "light.turn_off" for c in commands)


@pytest.mark.asyncio
async def test_baseline_reader_accepts_ha_validated_configuration(rig):
    from homeassistant.components.script.config import SCRIPT_ENTITY_SCHEMA

    obj = SCRIPT_ENTITY_SCHEMA(
        deepcopy(PACKAGE["script"]["home_lighting_apply_main_area_baseline"])
    )
    assert len(commands_in(obj["sequence"])) == 7
