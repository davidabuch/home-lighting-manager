"""Tests for the observation-only Home Assistant adapter."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from custom_components.home_lighting_manager.ha_observer import (
    _crossed_nightly_boundary,
    _next_generation,
)


def test_generation_advances_from_persisted_payload():
    assert _next_generation({"generation": 7}) == 8
    assert _next_generation({"generation": True}) == 1
    assert _next_generation({"generation": "7"}) == 1
    assert _next_generation(None) == 1


def test_nightly_boundary_detection():
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Los_Angeles")
    assert not _crossed_nightly_boundary(
        datetime(2026, 9, 15, 0, 30, tzinfo=tz),
        datetime(2026, 9, 15, 1, 58, tzinfo=tz),
    )
    assert _crossed_nightly_boundary(
        datetime(2026, 9, 15, 0, 30, tzinfo=tz),
        datetime(2026, 9, 15, 2, 0, tzinfo=tz),
    )
    assert _crossed_nightly_boundary(
        datetime(2026, 9, 15, 22, 0, tzinfo=tz),
        datetime(2026, 9, 16, 2, 0, tzinfo=tz),
    )


def test_observer_source_contains_no_command_service_calls():
    source = Path(
        "custom_components/home_lighting_manager/ha_observer.py"
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
async def test_observer_lifecycle_and_high_confidence_manual_tracking(tmp_path):
    from homeassistant.core import Context, HomeAssistant

    from custom_components.home_lighting_manager.ha_observer import (
        DIAGNOSTIC_ENTITY_ID,
        HomeAssistantShadowObserver,
    )

    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = HomeAssistantShadowObserver(
        hass, ["light.shadow_test"], {"light.shadow_test": 250}
    )
    await observer.async_start()

    hass.states.async_set(
        "light.shadow_test",
        "on",
        {"brightness": 123, "rgb_color": (1, 2, 3)},
        context=Context(user_id="test-user"),
    )
    await hass.async_block_till_done()

    layer = observer.runtime.engine.resolve("light.shadow_test").layer
    assert layer is not None
    assert layer.owner == "manual"
    assert layer.appearance is not None
    assert layer.appearance.brightness == 123
    assert layer.appearance.rgb_color == (1, 2, 3)

    diagnostics = hass.states.get(DIAGNOSTIC_ENTITY_ID)
    assert diagnostics is not None
    assert diagnostics.state == "observing"
    assert diagnostics.attributes["command_authority"] is False
    assert diagnostics.attributes["homeowner_events"] == 1

    await observer.async_shutdown()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_high_confidence_appearance_without_precedence_does_not_create_manual(tmp_path):
    from homeassistant.core import Context, HomeAssistant

    from custom_components.home_lighting_manager.ha_observer import HomeAssistantShadowObserver

    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = HomeAssistantShadowObserver(hass, ["light.shadow_test"])
    await observer.async_start()

    hass.states.async_set(
        "light.shadow_test",
        "on",
        {"brightness": 123},
        context=Context(user_id="test-user"),
    )
    await hass.async_block_till_done()

    assert observer.runtime.engine.resolve("light.shadow_test").layer is None
    diagnostics = observer.runtime.diagnostics()
    assert diagnostics.homeowner_events == 1

    await observer.async_shutdown()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_unattributed_state_change_does_not_create_manual(tmp_path):
    from homeassistant.core import HomeAssistant

    from custom_components.home_lighting_manager.ha_observer import HomeAssistantShadowObserver

    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = HomeAssistantShadowObserver(hass, ["light.shadow_test"])
    await observer.async_start()

    hass.states.async_set("light.shadow_test", "on", {"brightness": 200})
    await hass.async_block_till_done()

    assert observer.runtime.engine.resolve("light.shadow_test").layer is None
    assert observer.runtime.diagnostics().ignored_or_hlm_events == 1

    await observer.async_shutdown()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_unconfigured_light_is_ignored(tmp_path):
    from homeassistant.core import Context, HomeAssistant

    from custom_components.home_lighting_manager.ha_observer import HomeAssistantShadowObserver

    hass = HomeAssistant(str(tmp_path))
    observer = HomeAssistantShadowObserver(hass, ["light.managed"])
    await observer.async_start()

    hass.states.async_set(
        "light.not_managed",
        "on",
        {"brightness": 200},
        context=Context(user_id="test-user"),
    )
    await hass.async_block_till_done()

    assert observer.runtime.diagnostics().observed_events == 0
    await observer.async_shutdown()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_startup_checkpoints_new_generation_immediately(tmp_path):
    from homeassistant.core import HomeAssistant

    from custom_components.home_lighting_manager.ha_observer import HomeAssistantShadowObserver

    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = HomeAssistantShadowObserver(hass, ["light.shadow_test"])
    await observer.async_start()

    stored = await observer.store.async_load()
    assert stored is not None
    assert stored["generation"] == 1

    await observer.async_shutdown()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_restart_drops_persisted_manual_when_current_state_does_not_match(tmp_path):
    from homeassistant.core import HomeAssistant
    from homeassistant.util import dt as dt_util

    from custom_components.home_lighting_manager.ha_observer import HomeAssistantShadowObserver
    from custom_components.home_lighting_manager.model import Appearance, LayerKind, OwnershipLayer
    from custom_components.home_lighting_manager.persistence import serialize_state

    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = HomeAssistantShadowObserver(hass, ["light.shadow_test"])
    layer = OwnershipLayer(
        layer_id="persisted-manual",
        owner="manual",
        kind=LayerKind.MANUAL,
        generation=7,
        order=1,
        appearance=Appearance(on=True, brightness=123),
        precedence=250,
    )
    payload = serialize_state({"light.shadow_test": [layer]}, [])
    payload["generation"] = 7
    payload["saved_at"] = dt_util.now().isoformat()
    await observer.store.async_save(payload)
    hass.states.async_set("light.shadow_test", "on", {"brightness": 200})

    await observer.async_start()

    assert observer.runtime.engine.generation == 8
    assert observer.runtime.engine.resolve("light.shadow_test").layer is None

    await observer.async_shutdown()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_restart_restores_persisted_manual_only_when_current_state_corroborates_it(tmp_path):
    from homeassistant.core import HomeAssistant
    from homeassistant.util import dt as dt_util

    from custom_components.home_lighting_manager.ha_observer import HomeAssistantShadowObserver
    from custom_components.home_lighting_manager.model import Appearance, LayerKind, OwnershipLayer
    from custom_components.home_lighting_manager.persistence import serialize_state

    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = HomeAssistantShadowObserver(hass, ["light.shadow_test"])
    layer = OwnershipLayer(
        layer_id="persisted-manual",
        owner="manual",
        kind=LayerKind.MANUAL,
        generation=7,
        order=1,
        appearance=Appearance(on=True, brightness=123, rgb_color=(1, 2, 3)),
        precedence=250,
    )
    payload = serialize_state({"light.shadow_test": [layer]}, [])
    payload["generation"] = 7
    payload["saved_at"] = dt_util.now().isoformat()
    await observer.store.async_save(payload)
    hass.states.async_set(
        "light.shadow_test",
        "on",
        {"brightness": 123, "rgb_color": (1, 2, 3)},
    )

    await observer.async_start()

    restored = observer.runtime.engine.resolve("light.shadow_test").layer
    assert observer.runtime.engine.generation == 8
    assert restored is not None
    assert restored.owner == "manual"
    assert restored.generation == 8

    await observer.async_shutdown()
    await hass.async_block_till_done()


def test_reserved_intent_platform_filename_is_absent():
    assert not Path("custom_components/home_lighting_manager/intent.py").exists()
    assert Path("custom_components/home_lighting_manager/intent_policy.py").exists()


def test_context_attribution_preserves_external_vs_ha_user_topology():
    from homeassistant.core import Context, State

    from custom_components.home_lighting_manager.ha_observer import observation_from_state_change
    from custom_components.home_lighting_manager.intent_policy import (
        IntentAttributionSource,
        IntentEvidenceKind,
    )

    external = observation_from_state_change(
        "light.shadow_test",
        None,
        State("light.shadow_test", "on", {"brightness": 100}),
        Context(),
        manual_precedence=200,
    )
    assert external is not None
    assert external.evidence.kind is IntentEvidenceKind.UNKNOWN
    assert external.evidence.attribution_source is IntentAttributionSource.UNATTRIBUTED_EXTERNAL
    assert external.evidence.has_user_id is False
    assert external.evidence.has_parent_id is False

    homeowner = observation_from_state_change(
        "light.shadow_test",
        None,
        State("light.shadow_test", "on", {"brightness": 100}),
        Context(user_id="test-user"),
        manual_precedence=200,
    )
    assert homeowner is not None
    assert homeowner.evidence.kind is IntentEvidenceKind.EXPLICIT_HOMEOWNER_COMMAND
    assert homeowner.evidence.attribution_source is IntentAttributionSource.HOME_ASSISTANT_USER


@pytest.mark.asyncio
async def test_diagnostic_evidence_ledger_is_bounded_and_explains_unattributed_events(tmp_path):
    from homeassistant.core import HomeAssistant

    from custom_components.home_lighting_manager.ha_observer import (
        DIAGNOSTIC_ENTITY_ID,
        EVIDENCE_LEDGER_SIZE,
        HomeAssistantShadowObserver,
    )

    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = HomeAssistantShadowObserver(hass, ["light.shadow_test"], {"light.shadow_test": 200})
    await observer.async_start()

    for brightness in range(EVIDENCE_LEDGER_SIZE + 3):
        hass.states.async_set("light.shadow_test", "on", {"brightness": brightness + 1})
        await hass.async_block_till_done()

    diagnostics = hass.states.get(DIAGNOSTIC_ENTITY_ID)
    assert diagnostics is not None
    ledger = diagnostics.attributes["recent_evidence"]
    assert len(ledger) == EVIDENCE_LEDGER_SIZE
    assert ledger[-1]["entity_id"] == "light.shadow_test"
    assert ledger[-1]["attribution_source"] == "unattributed_external"
    assert ledger[-1]["intent"] == "hlm_owned"
    assert ledger[-1]["allows_homeowner_mutation"] is False
    assert ledger[-1]["mutated"] is False

    await observer.async_shutdown()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_topology_membership_prefers_canonical_state_when_event_snapshot_lacks_members(tmp_path):
    from homeassistant.core import HomeAssistant, State

    from custom_components.home_lighting_manager.ha_observer import (
        _member_entity_ids_for_observation,
    )

    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set(
        "light.aggregate",
        "on",
        {
            "entity_id": [
                "light.leaf_one",
                "light.leaf_two",
            ]
        },
    )

    event_snapshot = State("light.aggregate", "on", {"brightness": 100})
    assert _member_entity_ids_for_observation(
        hass, "light.aggregate", event_snapshot
    ) == ("light.leaf_one", "light.leaf_two")


@pytest.mark.asyncio
async def test_topology_membership_falls_back_to_event_snapshot_when_canonical_has_none(tmp_path):
    from homeassistant.core import HomeAssistant, State

    from custom_components.home_lighting_manager.ha_observer import (
        _member_entity_ids_for_observation,
    )

    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set("light.aggregate", "on", {"brightness": 100})

    event_snapshot = State(
        "light.aggregate",
        "on",
        {"group_entities": ["light.leaf_one", "light.leaf_two"]},
    )
    assert _member_entity_ids_for_observation(
        hass, "light.aggregate", event_snapshot
    ) == ("light.leaf_one", "light.leaf_two")


@pytest.mark.asyncio
async def test_observer_topology_cache_captures_group_members_at_startup(tmp_path):
    from homeassistant.core import HomeAssistant, State

    from custom_components.home_lighting_manager.ha_observer import HomeAssistantShadowObserver

    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set(
        "light.aggregate",
        "on",
        {"entity_id": ["light.leaf_one", "light.leaf_two"]},
    )
    observer = HomeAssistantShadowObserver(
        hass, ["light.aggregate", "light.leaf_one", "light.leaf_two"]
    )
    await observer.async_start()

    stripped_event_state = State("light.aggregate", "on", {"brightness": 100})
    assert observer._member_entity_ids_for_event(
        "light.aggregate", stripped_event_state
    ) == ("light.leaf_one", "light.leaf_two")

    await observer.async_shutdown()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_observer_topology_cache_learns_membership_from_event_fallback(tmp_path):
    from homeassistant.core import HomeAssistant, State

    from custom_components.home_lighting_manager.ha_observer import HomeAssistantShadowObserver

    hass = HomeAssistant(str(tmp_path))
    observer = HomeAssistantShadowObserver(hass, ["light.aggregate"])
    await observer.async_start()

    event_state = State(
        "light.aggregate",
        "on",
        {"group_entities": ["light.leaf_one", "light.leaf_two"]},
    )
    assert observer._member_entity_ids_for_event(
        "light.aggregate", event_state
    ) == ("light.leaf_one", "light.leaf_two")

    assert observer._topology_members["light.aggregate"] == (
        "light.leaf_one",
        "light.leaf_two",
    )

    await observer.async_shutdown()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_started_event_refreshes_topology_cache_and_diagnostics(tmp_path):
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import HomeAssistant

    from custom_components.home_lighting_manager.ha_observer import (
        DIAGNOSTIC_ENTITY_ID,
        HomeAssistantShadowObserver,
    )

    hass = HomeAssistant(str(tmp_path))
    observer = HomeAssistantShadowObserver(
        hass,
        ["light.aggregate", "light.leaf_one", "light.leaf_two"],
    )
    await observer.async_start()

    assert observer._topology_members == {}

    hass.states.async_set(
        "light.aggregate",
        "on",
        {
            "entity_id": [
                "light.leaf_one",
                "light.leaf_two",
            ]
        },
    )

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    assert observer._topology_members["light.aggregate"] == (
        "light.leaf_one",
        "light.leaf_two",
    )

    health = hass.states.get(DIAGNOSTIC_ENTITY_ID)
    assert health is not None
    assert health.attributes["topology_aggregate_count"] == 1
    assert health.attributes["topology_member_count"] == 2
    assert health.attributes["topology_aggregate_entities"] == ["light.aggregate"]

    await observer.async_shutdown()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_started_event_refresh_does_not_change_command_authority(tmp_path):
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import HomeAssistant

    from custom_components.home_lighting_manager.ha_observer import (
        DIAGNOSTIC_ENTITY_ID,
        HomeAssistantShadowObserver,
    )

    hass = HomeAssistant(str(tmp_path))
    observer = HomeAssistantShadowObserver(hass, ["light.aggregate"])
    await observer.async_start()

    hass.states.async_set(
        "light.aggregate",
        "on",
        {"entity_id": ["light.leaf_one"]},
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    health = hass.states.get(DIAGNOSTIC_ENTITY_ID)
    assert health is not None
    assert health.attributes["command_authority"] is False

    await observer.async_shutdown()
    await hass.async_block_till_done()
