from types import SimpleNamespace
from unittest.mock import patch

import pytest

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
    assert scenes["scene.expected"]["actions"] == {
        "light.member": {
            "rid": "bulb",
            "action": {"on": {"on": True}},
        }
    }
    assert scenes["scene.expected"]["palette"] is None
    assert scenes["scene.expected"]["speed"] is None
    assert (
        scenes["scene.expected"]["latest"] is False
    )  # Unmonitored alias recall prevents a takeover.


class WriteResponse:
    def __init__(self, payload=None):
        self.payload = payload or {"data": [{"rid": "ok"}], "errors": []}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    async def json(self):
        return self.payload


class WriteSession:
    def __init__(self, payload=None):
        self.calls = []
        self.payload = payload

    def put(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return WriteResponse(self.payload)


@pytest.mark.asyncio
async def test_selective_hue_actions_preserve_full_action_and_skip_manual(rig):
    """Selective execution sends exact Hue actions only to unprotected members."""

    protected = "light.protected"
    automatic = "light.automatic"

    entities = {
        protected: SimpleNamespace(
            platform="hue",
            config_entry_id="bridge",
            unique_id="protected-rid",
        ),
        automatic: SimpleNamespace(
            platform="hue",
            config_entry_id="bridge",
            unique_id="automatic-rid",
        ),
    }
    registry = SimpleNamespace(async_get=entities.get)
    entry = SimpleNamespace(
        domain="hue",
        data={"host": "test.invalid", "api_key": "test-only-placeholder"},
    )
    session = WriteSession()

    automatic_action = {
        "on": {"on": True},
        "dimming": {"brightness": 73.0},
        "gradient": {
            "points": [
                {"color": {"xy": {"x": 0.15, "y": 0.07}}},
                {"color": {"xy": {"x": 0.60, "y": 0.34}}},
            ],
            "mode": "interpolated_palette",
        },
        "effects_v2": {
            "action": {
                "effect": "prism",
                "parameters": {
                    "color": {"xy": {"x": 0.21, "y": 0.08}},
                    "speed": 0.5,
                },
            }
        },
    }

    scene_info = {
        "actions": {
            protected: {
                "rid": "protected-rid",
                "action": {
                    "on": {"on": True},
                    "color": {"xy": {"x": 0.1, "y": 0.2}},
                },
            },
            automatic: {
                "rid": "automatic-rid",
                "action": automatic_action,
            },
        }
    }

    with (
        patch(
            "custom_components.home_lighting_reconciliation.hue.er.async_get",
            return_value=registry,
        ),
        patch(
            "custom_components.home_lighting_reconciliation.hue.async_get_clientsession",
            return_value=session,
        ),
        patch.object(
            rig.hass,
            "config_entries",
            SimpleNamespace(async_get_entry=lambda _: entry),
        ),
    ):
        applied = await HueEvidence(rig.hass).apply_actions(
            scene_info,
            protected={protected},
        )

    assert applied == [automatic]
    assert len(session.calls) == 1

    url, kwargs = session.calls[0]
    assert url.endswith("/clip/v2/resource/light/automatic-rid")
    assert kwargs["json"] == automatic_action
    assert kwargs["headers"]["hue-application-key"] == "test-only-placeholder"


@pytest.mark.asyncio
async def test_selective_hue_actions_fail_closed_before_any_write(rig):
    """Malformed unprotected evidence prevents every Hue write."""

    valid = "light.valid"
    invalid = "light.invalid"

    entities = {
        valid: SimpleNamespace(
            platform="hue",
            config_entry_id="bridge",
            unique_id="valid-rid",
        ),
        invalid: SimpleNamespace(
            platform="hue",
            config_entry_id="bridge",
            unique_id="different-rid",
        ),
    }
    registry = SimpleNamespace(async_get=entities.get)
    session = WriteSession()

    scene_info = {
        "actions": {
            valid: {
                "rid": "valid-rid",
                "action": {"on": {"on": True}},
            },
            invalid: {
                "rid": "wrong-rid",
                "action": {"on": {"on": True}},
            },
        }
    }

    with (
        patch(
            "custom_components.home_lighting_reconciliation.hue.er.async_get",
            return_value=registry,
        ),
        patch(
            "custom_components.home_lighting_reconciliation.hue.async_get_clientsession",
            return_value=session,
        ),
    ):
        with pytest.raises(ValueError, match="Invalid Hue scene action evidence"):
            await HueEvidence(rig.hass).apply_actions(scene_info)

    assert session.calls == []


@pytest.mark.asyncio
async def test_selective_hue_actions_reject_cross_bridge_plan_before_write(rig):
    """One scene plan may never dispatch across inconsistent Hue bridges."""

    entities = {
        "light.one": SimpleNamespace(
            platform="hue",
            config_entry_id="bridge-one",
            unique_id="one-rid",
        ),
        "light.two": SimpleNamespace(
            platform="hue",
            config_entry_id="bridge-two",
            unique_id="two-rid",
        ),
    }
    registry = SimpleNamespace(async_get=entities.get)
    session = WriteSession()

    scene_info = {
        "actions": {
            "light.one": {
                "rid": "one-rid",
                "action": {"on": {"on": True}},
            },
            "light.two": {
                "rid": "two-rid",
                "action": {"on": {"on": True}},
            },
        }
    }

    with (
        patch(
            "custom_components.home_lighting_reconciliation.hue.er.async_get",
            return_value=registry,
        ),
        patch(
            "custom_components.home_lighting_reconciliation.hue.async_get_clientsession",
            return_value=session,
        ),
    ):
        with pytest.raises(ValueError, match="multiple bridges"):
            await HueEvidence(rig.hass).apply_actions(scene_info)

    assert session.calls == []


@pytest.mark.asyncio
async def test_selective_hue_actions_all_protected_is_noop(rig):
    """A fully protected scene produces no bridge traffic."""

    entity = "light.protected"
    scene_info = {
        "actions": {
            entity: {
                "rid": "protected-rid",
                "action": {"on": {"on": True}},
            }
        }
    }
    session = WriteSession()

    with patch(
        "custom_components.home_lighting_reconciliation.hue.async_get_clientsession",
        return_value=session,
    ):
        applied = await HueEvidence(rig.hass).apply_actions(
            scene_info,
            protected={entity},
        )

    assert applied == []
    assert session.calls == []
