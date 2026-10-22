"""Real observation/correlation and dispatch feedback without physical services."""

from datetime import timedelta

import pytest
from homeassistant.core import Context
from homeassistant.util import dt as dt_util
from test_cabinet_recovery import assert_isolated, event
from test_unified_intent_pipeline import start

import custom_components.home_lighting_manager.promotion_observer as promotion
from custom_components.home_lighting_manager import register_command_tracking
from custom_components.home_lighting_manager.model import LayerKind
from custom_components.home_lighting_reconciliation.engine import LIQUOR


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_context", [False, True])
async def test_internal_white_feedback_never_promotes_manual(rig, lost_context):
    observer, _, group = await start(rig, "main_area")
    register_command_tracking(rig.hass, observer)
    if not lost_context:
        async def light(call):
            attrs = {k: v for k, v in call.data.items() if k not in ("entity_id", "transition")}
            rig.calls.append(("light.turn_on", dict(call.data)))
            rig.hass.states.async_set(LIQUOR, "on", attrs, context=call.context)

        rig.hass.services.async_register("light", "turn_on", light)
    try:
        await event(rig, 0, True)
        await rig.hass.async_block_till_done()
        old = rig.get(group)
        rig.set(group, "on", {**old.attributes, "brightness": 40})
        await rig.hass.async_block_till_done()
        assert observer.runtime.engine.resolve(LIQUOR).layer.kind is LayerKind.AUTOMATIC
        assert LIQUOR not in observer.runtime.reconciliation_protected_entities()
        assert not observer._pending_single_promotions
        assert LIQUOR not in observer.pending_intent_entities()
        evidence = [e for e in observer._evidence_ledger if e.get("entity_id") == LIQUOR]
        assert evidence and evidence[-1]["normalized_causality"]["renderer_consequence"]
        assert not evidence[-1]["mutated"]
        assert_isolated(rig)
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["off", "on", "color"])
@pytest.mark.parametrize("external", [False, True])
async def test_independent_homeowner_intent_survives_white_and_release(
    rig, monkeypatch, operation, external
):
    observer, _, group = await start(rig, "main_area")
    register_command_tracking(rig.hass, observer)
    callbacks = []

    def later(hass, delay, action):
        callbacks.append((delay, action))
        return lambda: None

    monkeypatch.setattr(promotion, "async_call_later", later)
    try:
        await event(rig, 0, True)
        await rig.hass.async_block_till_done()
        is_off = operation == "off"
        attributes = {"brightness": 77, "color_temp_kelvin": 2700,
                      "color_mode": "color_temp", "effect": "off"}
        if operation == "color":
            attributes = {"brightness": 77, "xy_color": [0.4, 0.2],
                          "color_mode": "xy", "effect": "off"}
        rig.hass.states.async_set(
            LIQUOR, "off" if is_off else "on", attributes,
            context=Context() if external else Context(user_id="independent-homeowner"),
        )
        await rig.hass.async_block_till_done()
        if external:
            old = rig.get(group)
            rig.set(group, "on", {**old.attributes, "brightness": 19})
            await rig.hass.async_block_till_done()
            assert callbacks
            now = dt_util.now() + timedelta(seconds=max(d for d, _ in callbacks) + .01)
            monkeypatch.setattr(dt_util, "now", lambda *args: now)
            for _, action in callbacks:
                action(now)
            await rig.hass.async_block_till_done()
        expected = LayerKind.MANUAL_OFF if is_off else LayerKind.MANUAL
        assert observer.runtime.engine.resolve(LIQUOR).layer.kind is expected
        assert LIQUOR in observer.runtime.reconciliation_protected_entities()
        before = observer.runtime.engine.resolve(LIQUOR).appearance
        result = await event(rig, 0, False)
        await rig.hass.async_block_till_done()
        assert result["status"] == "released_verified"
        assert observer.runtime.engine.resolve(LIQUOR).layer.kind is expected
        assert observer.runtime.engine.resolve(LIQUOR).appearance == before
        assert rig.get(LIQUOR).state == ("off" if is_off else "on")
        if not is_off:
            assert rig.get(LIQUOR).attributes["brightness"] == 77
        assert_isolated(rig)
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()
