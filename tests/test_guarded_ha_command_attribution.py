from datetime import timedelta

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.home_lighting_manager.ha_observer import (
    MANAGED_SURFACE_GROUPS,
    SURFACE_MANUAL_PRECEDENCE,
    HomeAssistantShadowObserver,
    observation_from_state_change,
)
from custom_components.home_lighting_manager.intent_policy import (
    IntentAttributionSource,
    IntentEvidenceKind,
)
from custom_components.home_lighting_manager.model import LayerKind

ENTITY = "light.backyard_test_guarded_leaf"
GROUP = MANAGED_SURFACE_GROUPS["backyard"]
GUARD = "input_boolean.home_lighting_ha_guard_backyard"


def make_observer(hass: HomeAssistant) -> HomeAssistantShadowObserver:
    observer = HomeAssistantShadowObserver(
        hass,
        [ENTITY],
        {ENTITY: SURFACE_MANUAL_PRECEDENCE},
    )
    observer._surface_members_by_group[GROUP] = (ENTITY,)
    observer.entity_ids = frozenset({ENTITY})
    observer.runtime.update_managed_entities(observer.entity_ids)
    return observer


@pytest.mark.asyncio
async def test_guarded_direct_ha_off_is_hlm_consequence_not_manual_off(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set(GUARD, "on")
    hass.states.async_set(ENTITY, "off")
    observer = make_observer(hass)

    state = hass.states.get(ENTITY)
    observation = observation_from_state_change(
        ENTITY,
        None,
        state,
        Context(user_id="commissioning-user"),
        manual_precedence=SURFACE_MANUAL_PRECEDENCE,
    )
    assert observation is not None
    assert observation.evidence.kind is IntentEvidenceKind.EXPLICIT_HOMEOWNER_COMMAND

    guarded = observer._apply_ha_guard_attribution(ENTITY, observation)
    assert guarded.evidence.kind is IntentEvidenceKind.HLM_COMMAND_CONSEQUENCE
    assert guarded.evidence.attribution_source is IntentAttributionSource.HOME_ASSISTANT_USER
    assert guarded.operation_id is None

    decision = observer.runtime.observe(guarded)
    assert not decision.mutated
    assert observer.runtime.engine.resolve(ENTITY).layer is None


@pytest.mark.asyncio
async def test_unguarded_direct_ha_off_remains_homeowner_manual_off(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set(GUARD, "off")
    hass.states.async_set(ENTITY, "off")
    observer = make_observer(hass)

    state = hass.states.get(ENTITY)
    observation = observation_from_state_change(
        ENTITY,
        None,
        state,
        Context(user_id="homeowner-user"),
        manual_precedence=SURFACE_MANUAL_PRECEDENCE,
    )
    assert observation is not None

    unguarded = observer._apply_ha_guard_attribution(ENTITY, observation)
    assert unguarded.evidence.kind is IntentEvidenceKind.EXPLICIT_HOMEOWNER_COMMAND

    decision = observer.runtime.observe(unguarded)
    assert decision.mutated
    assert observer.runtime.engine.resolve(ENTITY).layer.kind is LayerKind.MANUAL_OFF


@pytest.mark.asyncio
async def test_guard_does_not_swallow_unattributed_external_hue_event(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set(GUARD, "on")
    hass.states.async_set(ENTITY, "off")
    observer = make_observer(hass)

    state = hass.states.get(ENTITY)
    observation = observation_from_state_change(
        ENTITY,
        None,
        state,
        Context(),
        manual_precedence=SURFACE_MANUAL_PRECEDENCE,
    )
    assert observation is not None
    assert observation.evidence.attribution_source is IntentAttributionSource.UNATTRIBUTED_EXTERNAL

    guarded = observer._apply_ha_guard_attribution(ENTITY, observation)
    assert guarded.evidence.kind is IntentEvidenceKind.UNKNOWN
    assert guarded.evidence.attribution_source is IntentAttributionSource.UNATTRIBUTED_EXTERNAL


@pytest.mark.asyncio
async def test_renderer_marker_expires_and_cannot_resurrect_across_guard_cycles(tmp_path, monkeypatch):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set(GUARD, "on")
    hass.states.async_set(ENTITY, "on")
    observer = make_observer(hass)

    t0 = dt_util.now()
    monkeypatch.setattr(
        "custom_components.home_lighting_manager.ha_observer.dt_util.now",
        lambda: t0,
    )
    assert observer.register_renderer_command_consequences([ENTITY], GUARD, "off") == 1
    assert observer._active_guard_for_ha_consequence(ENTITY, "off") == GUARD
    assert observer._active_guard_for_ha_consequence(ENTITY, "appearance") is None

    hass.states.async_set(GUARD, "off")
    hass.states.async_set(GUARD, "on")
    monkeypatch.setattr(
        "custom_components.home_lighting_manager.ha_observer.dt_util.now",
        lambda: t0 + timedelta(seconds=2.1),
    )
    assert observer._active_guard_for_ha_consequence(ENTITY, "off") is None
    assert ENTITY not in observer._guarded_ha_consequence_entities
