"""Regression coverage for shadow promotion of correlated external homeowner intent."""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.home_lighting_manager.intent_policy import (
    IntentAttributionSource,
    IntentDisposition,
    IntentEvidence,
    IntentEvidenceKind,
    classify_intent,
)


def test_correlated_external_homeowner_evidence_is_narrowly_accepted():
    decision = classify_intent(
        IntentEvidence(
            kind=IntentEvidenceKind.CORRELATED_EXTERNAL_HOMEOWNER_COMMAND,
            attribution_coherent=True,
            attribution_source=IntentAttributionSource.UNATTRIBUTED_EXTERNAL,
            has_user_id=False,
            has_parent_id=False,
        )
    )

    assert decision.disposition is IntentDisposition.HOMEOWNER_INTENT
    assert decision.allows_homeowner_mutation is True
    assert decision.reason == "qualified external topology correlation"


def test_correlated_external_homeowner_evidence_fails_closed_when_context_is_incoherent():
    decision = classify_intent(
        IntentEvidence(
            kind=IntentEvidenceKind.CORRELATED_EXTERNAL_HOMEOWNER_COMMAND,
            attribution_coherent=True,
            attribution_source=IntentAttributionSource.HOME_ASSISTANT_CHAIN,
            has_user_id=False,
            has_parent_id=True,
        )
    )

    assert decision.disposition is IntentDisposition.HLM_OWNED
    assert decision.allows_homeowner_mutation is False


def test_promotion_observer_source_contains_no_command_service_calls():
    source = Path(
        "custom_components/home_lighting_manager/promotion_observer.py"
    ).read_text()
    forbidden = (
        "services.async_call",
        "hass.services.async_call",
        "light.turn_on",
        "light.turn_off",
        "scene.turn_on",
    )
    for token in forbidden:
        assert token not in source


@pytest.mark.asyncio
async def test_qualified_external_leaf_records_manual_when_precedence_is_configured(tmp_path):
    from homeassistant.core import HomeAssistant

    from custom_components.home_lighting_manager.ha_observer import DIAGNOSTIC_ENTITY_ID
    from custom_components.home_lighting_manager.promotion_observer import (
        PromotingHomeAssistantShadowObserver,
    )

    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = PromotingHomeAssistantShadowObserver(
        hass,
        ["light.path_1", "light.path_group"],
        {"light.path_1": 250},
    )
    await observer.async_start()

    hass.states.async_set("light.path_1", "on", {"brightness": 128})
    await hass.async_block_till_done()
    assert observer.runtime.engine.resolve("light.path_1").layer is None

    hass.states.async_set(
        "light.path_group",
        "on",
        {"entity_id": ["light.path_1", "light.path_2"], "brightness": 128},
    )
    await hass.async_block_till_done()

    layer = observer.runtime.engine.resolve("light.path_1").layer
    assert layer is not None
    assert layer.owner == "manual"
    assert layer.appearance is not None
    assert layer.appearance.brightness == 128

    health = hass.states.get(DIAGNOSTIC_ENTITY_ID)
    assert health is not None
    candidate = health.attributes["external_burst"]["external_intent_candidate"]
    assert candidate["qualified"] is True
    assert candidate["entity_id"] == "light.path_1"
    assert candidate["promoted_to_homeowner"] is True
    assert candidate["manual_ownership_recorded"] is True
    assert health.attributes["command_authority"] is False
    assert observer.runtime.diagnostics().homeowner_events == 1

    await observer.async_shutdown()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_qualified_external_leaf_without_precedence_promotes_intent_but_not_manual(tmp_path):
    from homeassistant.core import HomeAssistant

    from custom_components.home_lighting_manager.ha_observer import DIAGNOSTIC_ENTITY_ID
    from custom_components.home_lighting_manager.promotion_observer import (
        PromotingHomeAssistantShadowObserver,
    )

    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = PromotingHomeAssistantShadowObserver(
        hass,
        ["light.path_1", "light.path_group"],
    )
    await observer.async_start()

    hass.states.async_set("light.path_1", "on", {"brightness": 128})
    await hass.async_block_till_done()
    hass.states.async_set(
        "light.path_group",
        "on",
        {"entity_id": ["light.path_1", "light.path_2"], "brightness": 128},
    )
    await hass.async_block_till_done()

    assert observer.runtime.engine.resolve("light.path_1").layer is None
    health = hass.states.get(DIAGNOSTIC_ENTITY_ID)
    assert health is not None
    candidate = health.attributes["external_burst"]["external_intent_candidate"]
    assert candidate["promoted_to_homeowner"] is True
    assert candidate["manual_ownership_recorded"] is False
    assert candidate["promotion_reason"] == "Manual precedence policy is required for appearance ownership"

    await observer.async_shutdown()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_availability_change_is_never_retained_for_homeowner_promotion(tmp_path):
    from homeassistant.core import HomeAssistant

    from custom_components.home_lighting_manager.ha_observer import DIAGNOSTIC_ENTITY_ID
    from custom_components.home_lighting_manager.promotion_observer import (
        PromotingHomeAssistantShadowObserver,
    )

    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = PromotingHomeAssistantShadowObserver(
        hass,
        ["light.path_1", "light.path_group"],
        {"light.path_1": 250},
    )
    await observer.async_start()

    hass.states.async_set("light.path_1", "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set("light.path_1", "on", {"brightness": 128})
    await hass.async_block_till_done()
    hass.states.async_set(
        "light.path_group",
        "on",
        {"entity_id": ["light.path_1", "light.path_2"], "brightness": 128},
    )
    await hass.async_block_till_done()

    assert observer.runtime.engine.resolve("light.path_1").layer is None
    health = hass.states.get(DIAGNOSTIC_ENTITY_ID)
    assert health is not None
    candidate = health.attributes["external_burst"]["external_intent_candidate"]
    assert candidate["qualified"] is True
    assert candidate["promoted_to_homeowner"] is False
    assert candidate["manual_ownership_recorded"] is False
    assert candidate["promotion_reason"] == "qualified burst lacked retained leaf observation"

    await observer.async_shutdown()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_same_external_burst_is_promoted_only_once(tmp_path):
    from homeassistant.core import HomeAssistant

    from custom_components.home_lighting_manager.promotion_observer import (
        PromotingHomeAssistantShadowObserver,
    )

    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = PromotingHomeAssistantShadowObserver(
        hass,
        ["light.path_1", "light.path_group", "light.holiday_path"],
        {"light.path_1": 250},
    )
    await observer.async_start()

    hass.states.async_set("light.path_1", "on", {"brightness": 128})
    await hass.async_block_till_done()
    hass.states.async_set(
        "light.path_group",
        "on",
        {"entity_id": ["light.path_1", "light.path_2"], "brightness": 128},
    )
    await hass.async_block_till_done()
    hass.states.async_set(
        "light.holiday_path",
        "on",
        {"entity_id": ["light.path_1", "light.path_2"], "brightness": 128},
    )
    await hass.async_block_till_done()

    assert observer.runtime.diagnostics().homeowner_events == 1
    layer = observer.runtime.engine.resolve("light.path_1").layer
    assert layer is not None
    assert layer.owner == "manual"

    await observer.async_shutdown()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_single_member_surface_rebound_does_not_promote_manual(tmp_path):
    """A Front Eve-style one-leaf rebound is ambiguous, not homeowner intent."""
    from homeassistant.core import HomeAssistant

    from custom_components.home_lighting_manager.ha_observer import DIAGNOSTIC_ENTITY_ID
    from custom_components.home_lighting_manager.promotion_observer import (
        PromotingHomeAssistantShadowObserver,
    )

    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"

    leaf = "light.front_yard_front_eve_lights"
    group = "light.front_eve_zone"
    hass.states.async_set(leaf, "off")
    hass.states.async_set(group, "off", {"entity_id": [leaf]})

    observer = PromotingHomeAssistantShadowObserver(
        hass,
        [leaf, group],
        {leaf: 250},
    )
    await observer.async_start()

    # Reproduce the physical chronology from 2026-09-22 02:30:
    # one context-less leaf rebounds ON, then its one-member Hue aggregate follows.
    hass.states.async_set(
        leaf,
        "on",
        {
            "brightness": 165,
            "color_temp_kelvin": 2724,
            "effect": "off",
            "dynamics": "none",
        },
    )
    await hass.async_block_till_done()
    hass.states.async_set(
        group,
        "on",
        {
            "entity_id": [leaf],
            "brightness": 165,
            "color_temp_kelvin": 2724,
        },
    )
    await hass.async_block_till_done()

    assert observer.runtime.engine.resolve(leaf).layer is None
    assert observer.runtime.diagnostics().homeowner_events == 0

    health = hass.states.get(DIAGNOSTIC_ENTITY_ID)
    assert health is not None
    candidate = health.attributes["external_burst"]["external_intent_candidate"]
    assert candidate["qualified"] is True
    assert candidate["entity_id"] == leaf
    assert candidate["promoted_to_homeowner"] is False
    assert candidate["manual_ownership_recorded"] is False
    assert candidate["promotion_reason"] == "qualified burst lacked retained leaf observation"
    assert health.attributes["reconciliation_protected_entities"] == []

    await observer.async_shutdown()
    await hass.async_block_till_done()
