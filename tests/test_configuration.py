import pytest
import yaml
from conftest import PACKAGE, ROOT
from homeassistant.components.automation.config import PLATFORM_SCHEMA
from homeassistant.components.script.config import SCRIPT_ENTITY_SCHEMA


@pytest.mark.asyncio
async def test_ha_schemas_and_unique_ids(rig):
    for obj in PACKAGE["script"].values():
        SCRIPT_ENTITY_SCHEMA(__import__("copy").deepcopy(obj))
    automations = PACKAGE["automation"][:]
    for f in (ROOT / "automations").glob("*.yaml"):
        automations += yaml.safe_load(f.read_text())
    for a in automations:
        PLATFORM_SCHEMA(a)
    ids = [a["id"] for a in automations]
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_owner_resolution(rig):
    result = await rig.run("home_lighting_resolve_owners")
    assert result.service_response["main_area"]["owner"] == "off"
    rig.set("input_boolean.home_lighting_main_area_window", "on")
    result = await rig.run("home_lighting_resolve_owners")
    assert result.service_response["main_area"]["owner"] == "daily"
    assert not rig.lights()


def test_yaml_duplicate_keys_and_removed_ghost_scene():
    class UniqueLoader(yaml.SafeLoader):
        pass

    def unique(loader, node, deep=False):
        result = {}
        for key, value in node.value:
            key = loader.construct_object(key, deep=deep)
            assert key not in result, f"Duplicate YAML key: {key}"
            result[key] = loader.construct_object(value, deep=deep)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique)
    for file in [
        ROOT / "packages/home_lighting_manager.yaml",
        *sorted((ROOT / "automations").glob("*.yaml")),
    ]:
        source = file.read_text()
        yaml.load(source, Loader=UniqueLoader)
        assert "scene.liquor_cabinet_manual_previous" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (
            "2026-09-11T03:24:01+00:00",
            "2026-09-11T03:25:01+00:00",
            True,
        ),
        (
            "2026-09-11T03:24:01+00:00",
            "unavailable",
            False,
        ),
        (
            "2026-09-11T03:24:01+00:00",
            "unknown",
            False,
        ),
        (
            "unavailable",
            "2026-09-11T03:24:01+00:00",
            False,
        ),
        (
            "unknown",
            "2026-09-11T03:24:01+00:00",
            False,
        ),
        (
            "2026-09-11T03:24:01+00:00",
            "2026-09-11T03:24:01+00:00",
            False,
        ),
        (
            "not-a-timestamp",
            "2026-09-11T03:25:01+00:00",
            False,
        ),
        (
            "2026-09-11T03:24:01+00:00",
            "not-a-timestamp",
            False,
        ),
    ],
)
async def test_hue_recall_timestamp_transition_validation(rig, old, new, expected):
    """Only a strictly newer valid Hue recall timestamp is Manual intent."""
    from types import SimpleNamespace

    from homeassistant.helpers.template import Template

    detector = next(
        a
        for a in PACKAGE["automation"]
        if a["id"] == "home_lighting_manual_ownership_detector_v1"
    )

    template_source = detector["actions"][0]["variables"][
        "hue_scene_recall_is_newer"
    ]

    trigger = SimpleNamespace(
        from_state=SimpleNamespace(state=old),
        to_state=SimpleNamespace(state=new),
    )

    result = Template(
        template_source,
        rig.hass,
    ).async_render(
        {"trigger": trigger},
        parse_result=True,
    )

    assert bool(result) is expected


def test_all_raw_hue_recall_branches_require_valid_newer_timestamp():
    """Every raw-Hue recall path must use the same positive-intent gate."""
    detector = next(
        a
        for a in PACKAGE["automation"]
        if a["id"] == "home_lighting_manual_ownership_detector_v1"
    )

    expected_ids = {
        "main_scene_recall",
        "front_eve_scene_recall",
        "path_scene_recall",
        "backyard_scene_recall",
    }

    found = set()

    for branch in detector["actions"][1]["choose"]:
        conditions = branch.get("conditions", [])

        trigger_ids = [
            c.get("id")
            for c in conditions
            if c.get("condition") == "trigger"
        ]

        matching = expected_ids.intersection(trigger_ids)

        if not matching:
            continue

        found.update(matching)

        assert any(
            c.get("condition") == "template"
            and "hue_scene_recall_is_newer" in c.get("value_template", "")
            for c in conditions
        )

    assert found == expected_ids


def test_existing_light_manual_transition_triggers_remain_intact():
    """The Hue telemetry fix must not alter direct OFF/ON Manual detection."""
    detector = next(
        a
        for a in PACKAGE["automation"]
        if a["id"] == "home_lighting_manual_ownership_detector_v1"
    )

    triggers = {
        trigger["id"]: trigger
        for trigger in detector["triggers"]
    }

    assert triggers["main_member_on"]["from"] == "off"
    assert triggers["main_member_on"]["to"] == "on"
    assert triggers["main_member_off"]["from"] == "on"
    assert triggers["main_member_off"]["to"] == "off"

    assert triggers["front_eve_on"]["from"] == "off"
    assert triggers["front_eve_on"]["to"] == "on"

    assert triggers["path_on"]["from"] == "off"
    assert triggers["path_on"]["to"] == "on"

    assert triggers["backyard_on"]["from"] == "off"
    assert triggers["backyard_on"]["to"] == "on"


@pytest.mark.asyncio
async def test_main_area_resolver_reports_entity_level_manual_ownership(rig):
    """Main Area exposes the exact lights claimed by Manual ownership."""

    left = "light.kitchen_kitchen_left_cabinet_light"
    right = "light.kitchen_kitchen_right_cabinet_lights"
    liquor = "light.living_room_liquor_cabinet_light"

    owners = (
        await rig.run("home_lighting_resolve_owners")
    ).service_response

    assert owners["main_area"]["manual_entities"] == []

    rig.set(
        "input_boolean.home_lighting_manual_kitchen_left_cabinet",
        "on",
    )
    owners = (
        await rig.run("home_lighting_resolve_owners")
    ).service_response

    assert owners["main_area"]["manual_entities"] == [left]

    rig.set(
        "input_boolean.home_lighting_manual_kitchen_right_cabinet",
        "on",
    )
    rig.set(
        "input_boolean.home_lighting_manual_liquor_cabinet",
        "on",
    )

    owners = (
        await rig.run("home_lighting_resolve_owners")
    ).service_response

    assert owners["main_area"]["manual_entities"] == [
        left,
        right,
        liquor,
    ]


def test_all_main_area_manual_helpers_exist():
    """Every managed Main Area Hue light has its own Manual helper."""

    expected = {
        "home_lighting_manual_kitchen_left_cabinet",
        "home_lighting_manual_kitchen_right_cabinet",
        "home_lighting_manual_living_left_cabinets",
        "home_lighting_manual_living_right_cabinets",
        "home_lighting_manual_living_left_ceiling",
        "home_lighting_manual_living_right_ceiling",
        "home_lighting_manual_liquor_cabinet",
    }

    assert expected.issubset(PACKAGE["input_boolean"])


def _manual_ownership_detector():
    automations = PACKAGE["automation"]

    if isinstance(automations, dict):
        automations = list(automations.values())

    return next(
        automation
        for automation in automations
        if automation.get("id") == "home_lighting_manual_ownership_detector_v1"
    )


def _detector_choice(trigger_id):
    detector = _manual_ownership_detector()

    choose_step = next(
        step
        for step in detector["actions"]
        if isinstance(step, dict) and "choose" in step
    )

    for choice in choose_step["choose"]:
        for condition in choice.get("conditions", []):
            if (
                condition.get("condition") == "trigger"
                and condition.get("id") == trigger_id
            ):
                return choice

    raise AssertionError(f"Detector choice not found: {trigger_id}")


def _contains_action(sequence, action):
    for step in sequence:
        if not isinstance(step, dict):
            continue

        if step.get("action") == action:
            return True

        for key in ("sequence", "then", "else", "default"):
            child = step.get(key)
            if isinstance(child, list) and _contains_action(child, action):
                return True

        choose = step.get("choose")
        if isinstance(choose, list):
            for choice in choose:
                if _contains_action(choice.get("sequence", []), action):
                    return True

    return False


def test_main_area_external_off_asserts_per_light_manual_ownership():
    """External Main Area ON->OFF is per-light Manual intent, not release."""

    choice = _detector_choice("main_member_off")
    conditions = choice["conditions"]
    sequence = choice["sequence"]

    assert {
        "condition": "state",
        "entity_id": "input_boolean.home_lighting_ha_guard_main_area",
        "state": "off",
    } in conditions

    assert {
        "condition": "state",
        "entity_id": "binary_sensor.hue_bridge_living_room",
        "state": "off",
    } in conditions

    assert sequence[0] == {
        "action": "input_boolean.turn_on",
        "target": {"entity_id": "{{ main_area_manual_helper }}"},
    }

    assert sequence[1] == {
        "action": "input_boolean.turn_on",
        "target": {
            "entity_id": "input_boolean.home_lighting_manual_main_area"
        },
    }

    assert not _contains_action(
        sequence,
        "script.home_lighting_evaluate_and_apply",
    )

    assert not _contains_action(
        sequence,
        "input_boolean.turn_off",
    )


def test_main_area_external_on_and_off_have_same_manual_claim_semantics():
    """Both homeowner state directions claim the exact Main Area member."""

    on_choice = _detector_choice("main_member_on")
    off_choice = _detector_choice("main_member_off")

    for choice in (on_choice, off_choice):
        sequence = choice["sequence"]

        assert sequence[0] == {
            "action": "input_boolean.turn_on",
            "target": {"entity_id": "{{ main_area_manual_helper }}"},
        }

        assert sequence[1] == {
            "action": "input_boolean.turn_on",
            "target": {
                "entity_id": "input_boolean.home_lighting_manual_main_area"
            },
        }


def test_main_area_manual_member_transitions_are_guarded_from_ha_commands():
    """HA-owned Main Area transitions remain excluded from Manual claiming."""

    for trigger_id in ("main_member_on", "main_member_off"):
        choice = _detector_choice(trigger_id)

        assert {
            "condition": "state",
            "entity_id": "input_boolean.home_lighting_ha_guard_main_area",
            "state": "off",
        } in choice["conditions"]



@pytest.mark.asyncio
async def test_migrated_main_area_leaf_ignores_stale_legacy_helper(rig):
    """HLM migration scope, not the old helper, decides protection for migrated leaves."""

    entity = "light.living_room_living_room_right_ceiling"
    rig.set(
        "sensor.home_lighting_manager_shadow_health",
        "observing",
        {
            "command_authority": False,
            "manual_precedence_entities": [entity],
            "reconciliation_protected_entities": [],
        },
    )
    rig.set("input_boolean.home_lighting_manual_living_right_ceiling", "on")

    owners = (await rig.run("home_lighting_resolve_owners")).service_response

    assert entity not in owners["main_area"]["manual_entities"]


@pytest.mark.asyncio
async def test_hlm_protected_main_area_leaf_joins_resolver_manual_entities(rig):
    """The resolver consumes the HLM protection projection without mirroring a helper."""

    entity = "light.living_room_living_room_right_ceiling"
    rig.set(
        "sensor.home_lighting_manager_shadow_health",
        "observing",
        {
            "command_authority": False,
            "manual_precedence_entities": [entity],
            "reconciliation_protected_entities": [entity],
        },
    )
    rig.set("input_boolean.home_lighting_manual_living_right_ceiling", "off")

    owners = (await rig.run("home_lighting_resolve_owners")).service_response

    assert entity in owners["main_area"]["manual_entities"]


def test_migrated_main_area_detector_does_not_reassert_legacy_helper():
    """Legacy state-transition ownership is disabled only for HLM-migrated leaves."""

    for trigger_id in ("main_member_on", "main_member_off"):
        choice = _detector_choice(trigger_id)
        assert any(
            condition.get("condition") == "template"
            and "trigger.entity_id not in hlm_manual_precedence_entities"
            in condition.get("value_template", "")
            for condition in choice["conditions"]
        )
