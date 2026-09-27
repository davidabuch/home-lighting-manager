from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.home_lighting_manager.engine import OwnershipEngine
from custom_components.home_lighting_manager.ha_observer import (
    DIAGNOSTIC_ENTITY_ID,
    HomeAssistantShadowObserver,
)
from custom_components.home_lighting_manager.model import LayerKind


def test_managed_membership_update_preserves_retained_state_and_invalidates_group_off():
    a = "light.a"
    b = "light.b"
    c = "light.c"
    engine = OwnershipEngine(managed_entities=frozenset((a, b)))
    engine.apply_group_off("light.group", (a, b))
    assert engine.group_off_sequences()
    engine.push_manual_off(a)

    result = engine.update_managed_entities(frozenset((a, b, c)))

    assert result == {"added": (c,), "removed": ()}
    assert engine.resolve(a).layer is not None
    assert engine.resolve(a).layer.kind is LayerKind.MANUAL_OFF
    assert engine.resolve(c).layer is None
    assert not engine.group_off_sequences()

    result = engine.update_managed_entities(frozenset((b, c)))

    assert result == {"added": (), "removed": (a,)}
    assert engine.resolve(a).layer is None
    assert engine.accepts_entity(c)
    assert not engine.accepts_entity(a)


@pytest.mark.asyncio
async def test_canonical_backyard_group_adopts_new_leaf_without_inventing_manual(tmp_path):
    group = "light.holiday_backyard"
    original = "light.backyard_original"
    festavia = "light.festavia_permanent_1"
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set(original, "off")
    hass.states.async_set(festavia, "on")
    hass.states.async_set(
        group,
        "on",
        {"entity_id": [original, festavia], "is_hue_group": True},
    )

    observer = HomeAssistantShadowObserver(hass, [group, original], {})
    await observer.async_start()
    try:
        assert festavia in observer.entity_ids
        assert observer.runtime.engine.accepts_entity(festavia)
        assert observer.runtime.engine.resolve(festavia).layer is None

        diagnostic = hass.states.get(DIAGNOSTIC_ENTITY_ID)
        projection = diagnostic.attributes["effective_ownership"]
        assert festavia in projection["entities"]
        assert projection["entities"][festavia]["owner"] is None
        assert projection["entities"][festavia]["protected"] is False
        assert projection["groups"][group] == sorted((original, festavia))
        assert diagnostic.attributes["managed_surface_members"]["backyard"] == sorted(
            (original, festavia)
        )
    finally:
        await observer.async_shutdown()


@pytest.mark.asyncio
async def test_canonical_membership_addition_is_live_and_exclusion_is_explicit(tmp_path):
    group = "light.holiday_backyard"
    original = "light.backyard_original"
    new_leaf = "light.backyard_new"
    excluded = "light.backyard_intentionally_unmanaged"
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set(original, "on")
    hass.states.async_set(new_leaf, "on")
    hass.states.async_set(excluded, "on")
    hass.states.async_set(
        group,
        "on",
        {"entity_id": [original], "is_hue_group": True},
    )

    observer = HomeAssistantShadowObserver(
        hass,
        [group, original],
        {},
        {"backyard": [excluded]},
    )
    await observer.async_start()
    try:
        assert new_leaf not in observer.entity_ids

        hass.states.async_set(
            group,
            "on",
            {"entity_id": [original, new_leaf, excluded], "is_hue_group": True},
        )
        observer._refresh_topology_cache()

        assert new_leaf in observer.entity_ids
        assert excluded not in observer.entity_ids
        assert observer.runtime.engine.resolve(new_leaf).layer is None
        diagnostic = hass.states.get(DIAGNOSTIC_ENTITY_ID)
        assert diagnostic.attributes["managed_surface_members"]["backyard"] == sorted(
            (original, new_leaf)
        )
        assert diagnostic.attributes["managed_surface_exclusions"]["backyard"] == [excluded]
    finally:
        await observer.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("surface", "group"),
    (
        ("main_area", "light.holiday_main_area"),
        ("front_eve", "light.front_eve_zone"),
        ("path", "light.holiday_path"),
        ("backyard", "light.holiday_backyard"),
    ),
)
async def test_every_canonical_surface_auto_adopts_new_members(tmp_path, surface, group):
    original = f"light.{surface}_original"
    added = f"light.{surface}_added"
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set(original, "on")
    hass.states.async_set(added, "off")
    hass.states.async_set(group, "on", {"entity_id": [original]})

    observer = HomeAssistantShadowObserver(hass, [group, original], {})
    await observer.async_start()
    try:
        assert added not in observer.entity_ids
        hass.states.async_set(group, "on", {"entity_id": [original, added]})
        observer._refresh_topology_cache()

        assert added in observer.entity_ids
        assert observer.runtime.engine.resolve(added).layer is None
        diagnostic = hass.states.get(DIAGNOSTIC_ENTITY_ID)
        assert diagnostic.attributes["managed_surface_members"][surface] == sorted(
            (original, added)
        )
    finally:
        await observer.async_shutdown()


@pytest.mark.asyncio
async def test_confirmed_surface_removal_retires_historical_static_member(tmp_path):
    group = "light.holiday_backyard"
    removed = "light.backyard_historical"
    retained = "light.backyard_retained"
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set(removed, "on")
    hass.states.async_set(retained, "on")
    hass.states.async_set(group, "on", {"entity_id": [removed, retained]})

    observer = HomeAssistantShadowObserver(hass, [group, removed], {})
    await observer.async_start()
    try:
        assert removed in observer.entity_ids
        assert retained in observer.entity_ids

        hass.states.async_set(group, "on", {"entity_id": [retained]})
        observer._pending_surface_membership[group] = (
            (retained,),
            dt_util.now() - timedelta(seconds=10),
        )
        observer._refresh_topology_cache()

        assert removed not in observer.entity_ids
        assert retained in observer.entity_ids
        assert not observer.runtime.engine.accepts_entity(removed)
    finally:
        await observer.async_shutdown()
