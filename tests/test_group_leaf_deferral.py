"""Regressions for provisional leaf promotion during exact-group correlation."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.home_lighting_manager.ha_observer import DIAGNOSTIC_ENTITY_ID
from custom_components.home_lighting_manager.model import LayerKind
from custom_components.home_lighting_manager.promotion_observer import (
    PromotingHomeAssistantShadowObserver,
)


async def observer_for(tmp_path, entities):
    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = PromotingHomeAssistantShadowObserver(
        hass, entities, dict.fromkeys(entities, 250)
    )
    await observer.async_start()
    return hass, observer


async def seed_group(hass, entity_id, members, *, state="off"):
    hass.states.async_set(
        entity_id,
        state,
        {"entity_id": list(members)},
        context=Context(parent_id="topology-seed"),
    )
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_exact_group_off_cancels_provisional_leaf_promotion(tmp_path):
    """A first leaf OFF must not become newer intent than its own group OFF."""
    left, right, extra = "light.left", "light.right", "light.extra"
    exact, containing = "light.cabinets", "light.kitchen"
    entities = [left, right, extra, exact, containing]
    hass, observer = await observer_for(tmp_path, entities)
    try:
        await seed_group(hass, exact, (left, right))
        await seed_group(hass, containing, (left, right, extra))

        # Establish one exact-group Manual appearance operation.
        hass.states.async_set(left, "on", {"brightness": 100})
        await hass.async_block_till_done()
        hass.states.async_set(right, "on", {"brightness": 180})
        await hass.async_block_till_done()
        hass.states.async_set(exact, "on", {"entity_id": [left, right]})
        await hass.async_block_till_done()
        assert observer.runtime.operations.latest_homeowner["group_id"] == exact
        assert all(
            observer.runtime.engine.resolve(entity).layer.kind is LayerKind.MANUAL
            for entity in (left, right)
        )

        later = dt_util.now() + timedelta(seconds=3)
        with patch(
            "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
            return_value=later,
        ):
            # The first leaf plus a containing aggregate is enough to qualify the
            # legacy single-leaf path, but it must stay provisional because the
            # aggregate has multiple members and an exact group may still resolve.
            hass.states.async_set(left, "off")
            await hass.async_block_till_done()
            hass.states.async_set(
                containing,
                "on",
                {"entity_id": [left, right, extra], "brightness": 1},
            )
            await hass.async_block_till_done()
            assert observer._pending_single_promotions
            assert observer.runtime.engine.resolve(left).layer.kind is LayerKind.MANUAL

            # Completion of the exact group must consume/cancel the provisional
            # leaf promotion before applying the group OFF transaction.
            hass.states.async_set(right, "off")
            await hass.async_block_till_done()
            hass.states.async_set(exact, "off", {"entity_id": [left, right]})
            await hass.async_block_till_done()

        assert not observer._pending_single_promotions
        assert observer.runtime.operations.latest_homeowner["group_id"] == exact
        assert observer.runtime.operations.latest_homeowner["reason"] == "released_to_hlm"
        assert all(
            observer.runtime.engine.resolve(entity).layer is None
            for entity in (left, right)
        )
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        rejection = attrs.get("latest_operation_rejection")
        assert not rejection or "stale successful intent" not in rejection.get("reason", "")
        assert attrs["command_authority"] is False
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_deferred_leaf_promotes_after_window_when_no_exact_group_resolves(tmp_path):
    """Deferral must not lose a legitimate single-leaf homeowner command."""
    leaf, other, group = "light.leaf", "light.other", "light.room"
    hass, observer = await observer_for(tmp_path, [leaf, other, group])
    scheduled = []

    def fake_call_later(_hass, _delay, action):
        cancelled = False

        def cancel():
            nonlocal cancelled
            cancelled = True

        scheduled.append((action, lambda: cancelled))
        return cancel

    try:
        await seed_group(hass, group, (leaf, other))
        with patch(
            "custom_components.home_lighting_manager.promotion_observer.async_call_later",
            side_effect=fake_call_later,
        ):
            hass.states.async_set(leaf, "on", {"brightness": 140})
            await hass.async_block_till_done()
            hass.states.async_set(
                group,
                "on",
                {"entity_id": [leaf, other], "brightness": 70},
            )
            await hass.async_block_till_done()

            assert len(scheduled) == 1
            assert not observer.runtime.operations.history
            assert observer.runtime.engine.resolve(leaf).layer is None

            callback, was_cancelled = scheduled[0]
            assert not was_cancelled()
            callback(dt_util.now() + timedelta(seconds=3))
            await hass.async_block_till_done()

        layer = observer.runtime.engine.resolve(leaf).layer
        assert layer is not None and layer.kind is LayerKind.MANUAL
        assert observer.runtime.engine.resolve(leaf).appearance.brightness == 140
        assert observer.runtime.operations.latest_homeowner["group_id"] is None
        assert observer.runtime.operations.latest_homeowner["reason"] == (
            "shadow Manual appearance recorded"
        )
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()
