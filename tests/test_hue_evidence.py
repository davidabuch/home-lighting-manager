from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.home_hue_scene_monitor.const import MANAGED_ZONES
from custom_components.home_hue_scene_monitor.coordinator import HomeHueSceneCoordinator
from custom_components.home_lighting_reconciliation.hue import HueEvidence


class Response:
    def __init__(self, data):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    async def json(self):
        return {"data": self.data}


class Session:
    def __init__(self, data):
        self.data = data

    def get(self, *args, **kwargs):
        return Response(self.data)


@pytest.mark.asyncio
async def test_hue_actions_membership_and_equivalent_group_recalls(rig):
    resources = [
        {"id": "group-light", "type": "grouped_light", "owner": {"rid": "holiday"}},
        {"id": "holiday", "type": "zone", "children": [{"rid": "device", "rtype": "device"}]},
        {"id": "daily", "type": "room", "children": [{"rid": "device", "rtype": "device"}]},
        {"id": "device", "type": "device", "services": [{"rid": "bulb", "rtype": "light"}]},
        {"id": "bulb", "type": "light", "on": {"on": False}},  # Observed OFF is not desired truth.
        {
            "id": "expected",
            "type": "scene",
            "group": {"rid": "holiday", "rtype": "zone"},
            "actions": [
                {"target": {"rid": "bulb", "rtype": "light"}, "action": {"on": {"on": True}}}
            ],
            "status": {"last_recall": "2026-09-08T01:00:00Z"},
        },
        {
            "id": "external",
            "type": "scene",
            "group": {"rid": "daily", "rtype": "room"},
            "status": {"last_recall": "2026-09-08T02:00:00Z"},
        },
    ]
    entities = {
        "light.holiday_path": SimpleNamespace(config_entry_id="bridge", unique_id="group-light"),
        "scene.expected": SimpleNamespace(config_entry_id="bridge", unique_id="expected"),
    }
    registry = SimpleNamespace(
        async_get=entities.get,
        async_get_entity_id=lambda d, p, u: "light.member" if u == "bulb" else None,
    )
    entry = SimpleNamespace(
        domain="hue", data={"host": "test.invalid", "api_key": "test-only-placeholder"}
    )
    with (
        patch(
            "custom_components.home_lighting_reconciliation.hue.er.async_get", return_value=registry
        ),
        patch(
            "custom_components.home_lighting_reconciliation.hue.async_get_clientsession",
            return_value=Session(resources),
        ),
        patch.object(rig.hass, "config_entries", SimpleNamespace(async_get_entry=lambda _: entry)),
    ):
        members, scenes = await HueEvidence(rig.hass).read(["scene.expected"], ["path"])
    assert members == {"path": ["light.member"]}
    assert scenes["scene.expected"]["actions"] == {"light.member": True}
    assert (
        scenes["scene.expected"]["latest"] is False
    )  # Unmonitored alias recall prevents a takeover.


@pytest.mark.asyncio
async def test_monitor_detects_equivalent_group_recalls_without_changing_sensor_ids():
    data = []
    for key, group in MANAGED_ZONES.items():
        data.append(
            {
                "id": key + "-old",
                "group": {"rid": group["group_id"]},
                "metadata": {"name": "Old"},
                "status": {"last_recall": "2026-09-08T01:00:00Z", "active": "inactive"},
            }
        )
    for key, group in [
        ("path", "0ba84dd4-0fe1-4440-9b65-ea7ea1feff35"),
        ("backyard", "bd7521e2-86b3-4679-8de1-e585c1089d66"),
    ]:
        data.append(
            {
                "id": key + "-external",
                "group": {"rid": group},
                "metadata": {"name": "External"},
                "status": {"last_recall": "2026-09-08T02:00:00Z", "active": "dynamic_palette"},
            }
        )
    c = object.__new__(HomeHueSceneCoordinator)
    c._host = "test.invalid"
    c._api_key = "test-only-placeholder"
    c._session = Session(data)
    result = await c._async_update_data()
    assert result["path"]["scene_id"] == "path-external"
    assert result["backyard"]["scene_id"] == "backyard-external"
    assert result["main_area"]["scene_id"] == "main_area-old"
    assert result["front_eve"]["scene_id"] == "front_eve-old"
    assert len(result["path"]["monitored_hue_group_ids"]) == 2
    assert len(result["backyard"]["monitored_hue_group_ids"]) == 3
