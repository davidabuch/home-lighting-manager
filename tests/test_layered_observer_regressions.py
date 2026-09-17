"""Preserve commissioned attribution while integrating operation identity/diagnostics."""

from unittest.mock import patch

import pytest
from homeassistant.core import Context, HomeAssistant

from custom_components.home_lighting_manager.ha_observer import DIAGNOSTIC_ENTITY_ID
from custom_components.home_lighting_manager.model import LayerKind
from custom_components.home_lighting_manager.promotion_observer import (
    PromotingHomeAssistantShadowObserver,
)


async def observer_for(tmp_path, entities):
    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = PromotingHomeAssistantShadowObserver(hass, entities, dict.fromkeys(entities, 250))
    await observer.async_start()
    return hass, observer


@pytest.mark.asyncio
async def test_late_external_qualification_cannot_replace_newer_direct_user_intent(tmp_path):
    leaf, group = "light.path_1", "light.path_group"
    hass, observer = await observer_for(tmp_path, [leaf, group])
    try:
        hass.states.async_set(leaf, "on", {"brightness": 100})
        await hass.async_block_till_done()
        hass.states.async_set(leaf, "on", {"brightness": 200}, context=Context(user_id="user"))
        await hass.async_block_till_done()
        hass.states.async_set(group, "on", {"entity_id": [leaf], "brightness": 100})
        await hass.async_block_till_done()
        assert observer.runtime.engine.resolve(leaf).appearance.brightness == 200
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert "stale successful intent" in attrs["latest_operation_rejection"]["reason"]
        assert attrs["command_authority"] is False
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_six_leaf_hue_group_burst_is_not_six_homeowner_operations(tmp_path):
    leaves = [f"light.path_{i}" for i in range(6)]
    groups = [
        "light.driveway_path",
        "light.front_yard",
        "light.holiday_path",
        "light.holiday_lighting",
    ]
    hass, observer = await observer_for(tmp_path, leaves + groups)
    try:
        for leaf in leaves:
            hass.states.async_set(leaf, "off")
        for group in groups:
            hass.states.async_set(group, "off", {"entity_id": leaves})
        await hass.async_block_till_done()
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert attrs["external_burst"]["topology"] == "multi_leaf_with_aggregate_propagation"
        assert not attrs["external_burst"]["external_intent_candidate"]["qualified"]
        assert not observer.runtime.operations.history
        assert all(observer.runtime.engine.resolve(leaf).layer is None for leaf in leaves)
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_sync_parent_chain_and_isolated_teardown_do_not_create_manual(tmp_path):
    leaves = ["light.left", "light.right"]
    hass, observer = await observer_for(tmp_path, leaves)
    try:
        for leaf in leaves:
            hass.states.async_set(
                leaf, "on", {"brightness": 220}, context=Context(parent_id="sync-stop")
            )
        await hass.async_block_till_done()
        hass.states.async_set(leaves[0], "on", {"brightness": 180})
        await hass.async_block_till_done()
        assert not observer.runtime.operations.history
        assert all(observer.runtime.engine.resolve(leaf).layer is None for leaf in leaves)
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_qualified_external_first_off_still_releases_manual_and_no_service_called(tmp_path):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    leaf, group = "light.leaf", "light.group"
    hass, observer = await observer_for(tmp_path, [leaf, group])
    try:
        with patch.object(
            type(hass.services), "async_call", side_effect=AssertionError("no dispatch")
        ):
            hass.states.async_set(leaf, "on", {"brightness": 150})
            await hass.async_block_till_done()
            hass.states.async_set(group, "on", {"entity_id": [leaf]})
            await hass.async_block_till_done()
            assert observer.runtime.engine.resolve(leaf).layer.kind is LayerKind.MANUAL
            later = dt_util.now() + timedelta(seconds=3)
            with patch(
                "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
                return_value=later,
            ):
                hass.states.async_set(leaf, "off")
                await hass.async_block_till_done()
                hass.states.async_set(group, "off", {"entity_id": [leaf]})
                await hass.async_block_till_done()
            assert observer.runtime.engine.resolve(leaf).layer is None
            attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
            assert attrs["latest_homeowner_operation"]["reason"] == "released_manual"
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()
