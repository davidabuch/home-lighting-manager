"""Regression tests for live Home Assistant topology membership containers."""

from __future__ import annotations

from homeassistant.core import State

from custom_components.home_lighting_manager.ha_observer import _member_entity_ids


def test_membership_accepts_frozenset_from_live_ha_state() -> None:
    state = State(
        "light.aggregate",
        "on",
        {
            "entity_id": frozenset(
                {
                    "light.leaf_one",
                    "light.leaf_two",
                }
            )
        },
    )

    assert set(_member_entity_ids(state)) == {
        "light.leaf_one",
        "light.leaf_two",
    }


def test_membership_rejects_string_and_mapping_containers() -> None:
    string_state = State(
        "light.aggregate",
        "on",
        {"entity_id": "light.leaf_one"},
    )
    mapping_state = State(
        "light.aggregate",
        "on",
        {"entity_id": {"light.leaf_one": True}},
    )

    assert _member_entity_ids(string_state) == ()
    assert _member_entity_ids(mapping_state) == ()


def test_membership_falls_back_to_group_entities_iterable() -> None:
    state = State(
        "light.aggregate",
        "on",
        {
            "entity_id": None,
            "group_entities": frozenset(
                {
                    "light.leaf_one",
                    "light.leaf_two",
                }
            ),
        },
    )

    assert set(_member_entity_ids(state)) == {
        "light.leaf_one",
        "light.leaf_two",
    }
