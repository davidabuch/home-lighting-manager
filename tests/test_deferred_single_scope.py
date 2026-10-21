"""F02: complete real deferred callbacks after scope qualification changes."""

import asyncio

import pytest
from homeassistant.core import Context
from test_group_leaf_deferral import observer_for, seed_group

import custom_components.home_lighting_manager.promotion_observer as promotion
from custom_components.home_lighting_manager.model import LayerKind


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["off", "appearance"])
@pytest.mark.parametrize("alias_order", [False, True])
@pytest.mark.parametrize("extra_count", [0, 33])
async def test_ambiguous_alias_burst_revokes_deferred_single_through_completion(
    tmp_path, monkeypatch, kind, alias_order, extra_count
):
    a, b = "light.zz_scope_a", "light.scope_b"
    extras = [f"light.scope_extra_{i:02}" for i in range(extra_count)]
    members = [a, b, *extras]
    g1, g2 = "light.scope_alias_1", "light.scope_alias_2"
    hass, observer = await observer_for(tmp_path, [*members, g1, g2])
    callbacks = []
    original = promotion.async_call_later

    def capture(hass, delay, action):
        callbacks.append(action)
        return original(hass, delay, action)

    monkeypatch.setattr(promotion, "async_call_later", capture)
    try:
        await seed_group(hass, g1, members, state="on")
        await seed_group(hass, g2, members, state="on")
        state = "off" if kind == "off" else "on"
        hass.states.async_set(a, state, {"brightness": 99})
        await hass.async_block_till_done()
        hass.states.async_set(g1, "on", {"entity_id": members, "brightness": 99})
        await hass.async_block_till_done()
        assert observer._pending_single_promotions
        assert len(callbacks) == 1
        for leaf in (b, *extras):
            hass.states.async_set(leaf, state, {"brightness": 99})
            await hass.async_block_till_done()
        for group in ((g2, g1) if alias_order else (g1, g2)):
            hass.states.async_set(group, state, {"entity_id": members, "brightness": 90})
            await hass.async_block_till_done()
        assert not observer._external_burst["external_intent_candidate"]["qualified"]
        if extras:
            # Invalidation must use full evidence, not the 32-member diagnostic tail.
            assert a not in observer._external_burst["leaf_entities"]
        assert not observer.runtime.operations.history
        # Wait beyond BOTH the real promotion and evidence-expiry deadlines.
        await asyncio.sleep(2.2)
        await hass.async_block_till_done()
        assert not observer.runtime.operations.history
        assert not observer.runtime.reconciliation_protected_entities()
        assert observer.runtime.engine.resolve(a).layer is None
        assert observer.runtime.engine.resolve(b).layer is None
        assert not observer.pending_intent_entities()
        # A cancelled callback already dequeued by HA cannot regain authority.
        callbacks[0](None)
        await hass.async_block_till_done()
        assert not observer.runtime.operations.history
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("second_group", ["light.holiday_backyard", "light.holiday_path"])
async def test_unrelated_surface_burst_preserves_both_deferred_intents(tmp_path, second_group):
    a, spare_a, b, spare_b = [f"light.independent_{i}" for i in range(4)]
    g1 = "light.holiday_main_area"
    hass, observer = await observer_for(tmp_path, [a, spare_a, b, spare_b, g1, second_group])
    try:
        await seed_group(hass, g1, (a, spare_a), state="on")
        await seed_group(hass, second_group, (b, spare_b), state="on")
        for leaf, group, other in ((a, g1, spare_a), (b, second_group, spare_b)):
            hass.states.async_set(leaf, "off")
            await hass.async_block_till_done()
            hass.states.async_set(group, "on", {"entity_id": [leaf, other], "brightness": 77})
            await hass.async_block_till_done()
        await asyncio.sleep(2.2)
        await hass.async_block_till_done()
        for leaf in (a, b):
            assert observer.runtime.engine.resolve(leaf).layer.kind is LayerKind.MANUAL_OFF
        for leaf in (spare_a, spare_b):
            assert observer.runtime.engine.resolve(leaf).layer is None
        assert len(observer.runtime.operations.history) == 2
        assert set(observer.runtime.reconciliation_protected_entities()) == {a, b}
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_explicit_independent_sibling_does_not_veto_qualified_leaf(tmp_path):
    a, b, group = "light.qualified_a", "light.explicit_b", "light.qualified_room"
    hass, observer = await observer_for(tmp_path, [a, b, group])
    try:
        await seed_group(hass, group, (a, b), state="on")
        hass.states.async_set(a, "off")
        await hass.async_block_till_done()
        hass.states.async_set(group, "on", {"entity_id": [a, b], "brightness": 5})
        await hass.async_block_till_done()
        hass.states.async_set(b, "off", context=Context(user_id="homeowner"))
        await hass.async_block_till_done()
        await asyncio.sleep(2.2)
        await hass.async_block_till_done()
        assert len(observer.runtime.operations.history) == 2
        for leaf in (a, b):
            assert observer.runtime.engine.resolve(leaf).layer.kind is LayerKind.MANUAL_OFF
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()
