from types import SimpleNamespace

from custom_components.home_lighting_reconciliation.__init__ import (
    Adapter,
    BACKYARD_BRIGHT_WHITE_ENTITIES,
)


class FakeStates:
    def __init__(self, values):
        self._values = values

    def get(self, entity_id):
        value = self._values.get(entity_id)
        return None if value is None else SimpleNamespace(state=value)


class FakeAdapter:
    def __init__(self, values):
        self.hass = SimpleNamespace(states=FakeStates(values))


def base_owners():
    return {
        "main_area": {"owner": "daily"},
        "front_eve": {"owner": "daily"},
        "path": {"owner": "daily"},
        "backyard": {"owner": "daily"},
    }


def inactive_inputs(toggle):
    return {
        "sensor.nfl_san_francisco_49ers": "POST",
        "binary_sensor.hue_bridge_backyard": "off",
        "input_boolean.home_lighting_manual_backyard": "off",
        "input_boolean.spa_gauge_active": "off",
        "input_boolean.backyard_bright_white": toggle,
    }


def test_bright_white_toggle_excludes_only_overlay_hue_leaves():
    adapter = FakeAdapter(inactive_inputs("on"))

    owners = Adapter.with_legacy_overlay_exclusions(adapter, base_owners())

    assert set(owners["backyard"]["excluded_entities"]) == set(
        BACKYARD_BRIGHT_WHITE_ENTITIES
    )
    assert "excluded_entities" not in owners["main_area"]
    assert "excluded_entities" not in owners["front_eve"]
    assert "excluded_entities" not in owners["path"]


def test_bright_white_toggle_off_removes_no_existing_ownership_protection():
    adapter = FakeAdapter(inactive_inputs("off"))
    owners = base_owners()
    owners["backyard"]["excluded_entities"] = ["light.existing_manual"]

    projected = Adapter.with_legacy_overlay_exclusions(adapter, owners)

    assert projected["backyard"]["excluded_entities"] == ["light.existing_manual"]
