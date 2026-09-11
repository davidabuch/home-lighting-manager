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

    assert triggers["main_on"]["from"] == "off"
    assert triggers["main_on"]["to"] == "on"

    assert triggers["front_eve_on"]["from"] == "off"
    assert triggers["front_eve_on"]["to"] == "on"

    assert triggers["path_on"]["from"] == "off"
    assert triggers["path_on"]["to"] == "on"

    assert triggers["backyard_on"]["from"] == "off"
    assert triggers["backyard_on"]["to"] == "on"
