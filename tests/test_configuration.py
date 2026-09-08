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
