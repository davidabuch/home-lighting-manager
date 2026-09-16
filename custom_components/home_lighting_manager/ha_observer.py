"""Observation-only Home Assistant adapter for Home Lighting Manager.

This module may observe Home Assistant state changes, persist shadow evidence, and expose diagnostic
states. It must never call Home Assistant services or command a device.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from datetime import datetime, time, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_HOMEASSISTANT_STOP,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Context, Event, HomeAssistant, State, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .attribution_correlation import ExternalBurstCorrelator, ExternalTopologyEvent
from .engine import NIGHTLY_BOUNDARY
from .intent_policy import (
    IntentAttributionSource,
    IntentEvidence,
    IntentEvidenceKind,
)
from .model import Appearance, LayerKind, OwnershipLayer
from .persistence import STORAGE_VERSION, deserialize_state
from .recovery import ManualRecoveryEvidence
from .shadow import ShadowDecision, ShadowObservation, ShadowRuntime

DOMAIN = "home_lighting_manager"
CONF_SHADOW_ENTITIES = "shadow_entities"
CONF_MANUAL_PRECEDENCE = "manual_precedence"
STORAGE_KEY = f"{DOMAIN}.shadow"
RUNTIME_DATA_KEY = f"{DOMAIN}_shadow_runtime"
DIAGNOSTIC_ENTITY_ID = "sensor.home_lighting_manager_shadow_health"
EVIDENCE_LEDGER_SIZE = 12
EXTERNAL_BURST_WINDOW_SECONDS = 2.0

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_SHADOW_ENTITIES): cv.entity_ids,
                vol.Optional(CONF_MANUAL_PRECEDENCE, default={}): {
                    cv.entity_id: vol.All(vol.Coerce(int), vol.Range(min=0)),
                },
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


class HomeAssistantShadowObserver:
    """Bridge Home Assistant observations into the non-commanding shadow runtime."""

    def __init__(
        self,
        hass: HomeAssistant,
        entity_ids: list[str],
        manual_precedence: dict[str, int] | None = None,
    ) -> None:
        self.hass = hass
        self.entity_ids = frozenset(entity_ids)
        self.manual_precedence = dict(manual_precedence or {})
        self.store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.runtime = ShadowRuntime(generation=1)
        self._unsubscribers: list[Any] = []
        self._last_decision: ShadowDecision | None = None
        self._storage_status = "empty"
        self._evidence_ledger: deque[dict[str, Any]] = deque(maxlen=EVIDENCE_LEDGER_SIZE)
        self._external_correlator = ExternalBurstCorrelator(
            window_seconds=EXTERNAL_BURST_WINDOW_SECONDS
        )
        self._external_burst: dict[str, object] | None = None
        self._topology_members: dict[str, tuple[str, ...]] = {}

    async def async_start(self) -> None:
        """Load trusted evidence and begin observation without command authority."""
        raw = await self.store.async_load()
        generation = _next_generation(raw)
        self.runtime = ShadowRuntime(generation=generation)

        if isinstance(raw, dict):
            persisted = deserialize_state(raw)
            saved_at = _parse_saved_at(raw.get("saved_at"))
            now = dt_util.now()
            manual_evidence = _manual_recovery_evidence(
                persisted.layers, saved_at, now, self.hass
            )
            self.runtime.restore(
                persisted,
                manual_evidence=manual_evidence,
                family_evidence={},
            )
            self._storage_status = "loaded"

        self._refresh_topology_cache()

        # Checkpoint the new authority generation immediately. If Home Assistant crashes before
        # another mutation or clean shutdown, the next process must still advance beyond this one.
        await self.async_save()

        self._unsubscribers.append(
            self.hass.bus.async_listen("state_changed", self._async_state_changed)
        )
        self._unsubscribers.append(
            async_track_time_change(
                self.hass,
                self._handle_nightly_boundary,
                hour=1,
                minute=59,
                second=0,
            )
        )
        self._unsubscribers.append(
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._async_started_event
            )
        )
        self._unsubscribers.append(
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._async_stop_event)
        )
        self._schedule_delayed_topology_refreshes()
        self._publish_diagnostics()

    async def async_shutdown(self) -> None:
        """Persist evidence and unregister observers."""
        await self.async_save()
        while self._unsubscribers:
            unsub = self._unsubscribers.pop()
            unsub()
        self.hass.states.async_remove(DIAGNOSTIC_ENTITY_ID)

    async def async_save(self) -> None:
        """Persist only contractually durable shadow evidence."""
        payload = self.runtime.export_persistence()
        payload["generation"] = self.runtime.engine.generation
        payload["saved_at"] = dt_util.now().isoformat()
        await self.store.async_save(payload)
        self._storage_status = "saved"
        self._publish_diagnostics()

    async def _async_state_changed(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        if entity_id not in self.entity_ids:
            return

        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if not isinstance(new_state, State):
            return

        observation = observation_from_state_change(
            entity_id,
            old_state if isinstance(old_state, State) else None,
            new_state,
            event.context,
            manual_precedence=self.manual_precedence.get(entity_id),
        )
        if observation is None:
            return

        self._last_decision = self.runtime.observe(observation)
        self._record_external_topology(observation, new_state)
        self._record_evidence(observation, self._last_decision, new_state)
        if self._last_decision.mutated:
            await self.async_save()
        else:
            self._publish_diagnostics()

    @callback
    def _record_external_topology(
        self, observation: ShadowObservation, new_state: State
    ) -> None:
        """Correlate unattributed external events against current HA topology."""
        if (
            observation.evidence.attribution_source
            is not IntentAttributionSource.UNATTRIBUTED_EXTERNAL
        ):
            return

        # Attribution correctness must not depend on startup ordering or a stale
        # warm cache. Refresh from current Home Assistant truth at the moment an
        # unattributed external lighting event is being correlated.
        self._refresh_topology_cache()

        members = self._member_entity_ids_for_event(
            observation.entity_id, new_state
        )
        summary = self._external_correlator.observe(
            ExternalTopologyEvent(
                timestamp=dt_util.now(),
                entity_id=observation.entity_id,
                member_entity_ids=members,
            )
        )
        self._external_burst = summary.as_dict()

    @callback
    def _record_evidence(
        self, observation: ShadowObservation, decision: ShadowDecision, new_state: State
    ) -> None:
        """Record a bounded, non-commanding attribution ledger for commissioning."""
        evidence = observation.evidence
        members = self._member_entity_ids_for_event(
            observation.entity_id, new_state
        )
        self._evidence_ledger.append(
            {
                "timestamp": dt_util.now().isoformat(),
                "entity_id": observation.entity_id,
                "operation": observation.operation,
                "evidence_kind": evidence.kind.value,
                "attribution_source": evidence.attribution_source.value,
                "user_context": evidence.has_user_id,
                "parent_context": evidence.has_parent_id,
                "entity_role": "aggregate" if members else "leaf",
                "member_count": len(members),
                "intent": decision.intent.disposition.value,
                "allows_homeowner_mutation": decision.intent.allows_homeowner_mutation,
                "mutated": decision.mutated,
                "reason": decision.reason,
            }
        )

    @callback
    def _refresh_topology_cache(self) -> None:
        """Snapshot aggregate membership from all live HA light states."""
        cache: dict[str, tuple[str, ...]] = {}

        for state in self.hass.states.async_all():
            if not state.entity_id.startswith("light."):
                continue

            members = _member_entity_ids(state)
            if members:
                cache[state.entity_id] = members

        self._topology_members = cache

    @callback
    def _member_entity_ids_for_event(
        self, entity_id: str, event_state: State
    ) -> tuple[str, ...]:
        """Resolve event topology from the startup cache, then safe fallbacks."""
        cached = self._topology_members.get(entity_id)
        if cached:
            return cached

        members = _member_entity_ids(event_state)
        if members:
            self._topology_members[entity_id] = members
            return members

        current = self.hass.states.get(entity_id)
        if current is not None:
            members = _member_entity_ids(current)
            if members:
                self._topology_members[entity_id] = members
                return members

        return ()

    @callback
    def _handle_nightly_boundary(self, _now: datetime) -> None:
        self.runtime.engine.expire_boundary(NIGHTLY_BOUNDARY)
        self.hass.async_create_task(self.async_save())

    async def _async_started_event(self, _event: Event) -> None:
        """Refresh topology when HA startup completion is observed."""
        self._refresh_topology_cache()
        self._publish_diagnostics()

    @callback
    def _schedule_delayed_topology_refreshes(self) -> None:
        """Schedule bounded retries relative to HLM observer startup."""
        for delay in (5, 15, 30):
            self._unsubscribers.append(
                async_call_later(
                    self.hass,
                    delay,
                    self._delayed_topology_refresh,
                )
            )

    @callback
    def _delayed_topology_refresh(self, _now: datetime) -> None:
        """Refresh topology after integrations finish late startup work."""
        self._refresh_topology_cache()
        self._publish_diagnostics()

    async def _async_stop_event(self, _event: Event) -> None:
        await self.async_save()

    @callback
    def _publish_diagnostics(self) -> None:
        diagnostics = self.runtime.diagnostics()
        attrs: dict[str, Any] = {
            "generation": diagnostics.generation,
            "observed_events": diagnostics.observed_events,
            "homeowner_events": diagnostics.homeowner_events,
            "ignored_or_hlm_events": diagnostics.ignored_or_hlm_events,
            "managed_entities": diagnostics.managed_entities,
            "suppressed_sessions": diagnostics.suppressed_sessions,
            "configured_entities": len(self.entity_ids),
            "precedence_configured_entities": sum(
                1 for entity_id in self.entity_ids if entity_id in self.manual_precedence
            ),
            "storage_status": self._storage_status,
            "command_authority": False,
            "evidence_ledger_size": EVIDENCE_LEDGER_SIZE,
            "recent_evidence": list(self._evidence_ledger),
            "external_burst_window_seconds": EXTERNAL_BURST_WINDOW_SECONDS,
            "external_burst": self._external_burst,
            "topology_aggregate_count": len(self._topology_members),
            "topology_member_count": sum(
                len(members) for members in self._topology_members.values()
            ),
            "topology_aggregate_entities": sorted(self._topology_members)[:32],
            "topology_cache_ready": bool(self._topology_members),
        }
        if self._last_decision is not None:
            attrs.update(
                {
                    "last_entity": self._last_decision.entity_id,
                    "last_reason": self._last_decision.reason,
                    "last_intent": self._last_decision.intent.disposition.value,
                    "last_mutated": self._last_decision.mutated,
                }
            )
        self.hass.states.async_set(DIAGNOSTIC_ENTITY_ID, "observing", attrs)


def observation_from_state_change(
    entity_id: str,
    old_state: State | None,
    new_state: State,
    context: Context,
    *,
    manual_precedence: int | None = None,
) -> ShadowObservation | None:
    """Convert one HA state transition into conservative shadow evidence."""
    if not entity_id.startswith("light."):
        return None

    if new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN) or (
        old_state is not None and old_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
    ):
        evidence = IntentEvidence(
            kind=IntentEvidenceKind.AVAILABILITY_CHANGE,
            attribution_source=_attribution_source_from_context(context),
            has_user_id=context.user_id is not None,
            has_parent_id=context.parent_id is not None,
        )
        return ShadowObservation(entity_id=entity_id, evidence=evidence, operation="appearance")

    has_user_id = context.user_id is not None
    has_parent_id = context.parent_id is not None
    explicit_user = has_user_id and not has_parent_id
    evidence = IntentEvidence(
        kind=(
            IntentEvidenceKind.EXPLICIT_HOMEOWNER_COMMAND
            if explicit_user
            else IntentEvidenceKind.UNKNOWN
        ),
        succeeded=True,
        attribution_coherent=explicit_user,
        attribution_source=_attribution_source_from_context(context),
        has_user_id=has_user_id,
        has_parent_id=has_parent_id,
    )

    if new_state.state == STATE_OFF:
        return ShadowObservation(entity_id=entity_id, evidence=evidence, operation="off")
    if new_state.state != STATE_ON:
        return None

    return ShadowObservation(
        entity_id=entity_id,
        evidence=evidence,
        appearance=_appearance_from_state(new_state),
        operation="appearance",
        manual_precedence=manual_precedence,
    )


def _attribution_source_from_context(context: Context) -> IntentAttributionSource:
    """Preserve HA context topology without over-claiming homeowner provenance."""
    if context.user_id is not None and context.parent_id is None:
        return IntentAttributionSource.HOME_ASSISTANT_USER
    if context.parent_id is not None:
        return IntentAttributionSource.HOME_ASSISTANT_CHAIN
    if context.user_id is None:
        return IntentAttributionSource.UNATTRIBUTED_EXTERNAL
    return IntentAttributionSource.UNKNOWN


def _member_entity_ids_for_observation(
    hass: HomeAssistant, entity_id: str, event_state: State
) -> tuple[str, ...]:
    """Resolve aggregate membership from canonical HA state, with event fallback.

    Some integration-generated state_changed payloads do not preserve the
    aggregate membership attributes that are present on the canonical state
    machine entry. Topology diagnostics therefore prefer current HA truth and
    use the event snapshot only when canonical membership is unavailable.
    """
    current = hass.states.get(entity_id)
    if current is not None:
        members = _member_entity_ids(current)
        if members:
            return members
    return _member_entity_ids(event_state)


def _member_entity_ids(state: State) -> tuple[str, ...]:
    """Return direct light-aggregate membership exposed by Home Assistant, if any."""
    raw = state.attributes.get("entity_id")
    if not _is_membership_container(raw):
        raw = state.attributes.get("group_entities")
    if not _is_membership_container(raw):
        return ()
    members = [item for item in raw if isinstance(item, str) and item.startswith("light.")]
    return tuple(dict.fromkeys(members))


def _is_membership_container(value: Any) -> bool:
    """Accept HA membership iterables without treating strings/mappings as member collections."""
    return isinstance(value, Iterable) and not isinstance(
        value, (str, bytes, bytearray, Mapping)
    )


def _appearance_from_state(state: State) -> Appearance:
    attrs = state.attributes
    return Appearance(
        on=True,
        brightness=_optional_int(attrs.get("brightness")),
        color_temp_kelvin=_optional_int(attrs.get("color_temp_kelvin")),
        xy_color=_optional_tuple(attrs.get("xy_color"), 2),
        rgb_color=_optional_int_tuple(attrs.get("rgb_color"), 3),
        hs_color=_optional_tuple(attrs.get("hs_color"), 2),
        effect=attrs.get("effect") if isinstance(attrs.get("effect"), str) else None,
    )


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_tuple(value: Any, length: int) -> tuple[float, ...] | None:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        return None
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
        return None
    return tuple(float(item) for item in value)


def _optional_int_tuple(value: Any, length: int) -> tuple[int, ...] | None:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        return None
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        return None
    return tuple(value)


def _next_generation(raw: Any) -> int:
    if isinstance(raw, dict):
        generation = raw.get("generation")
        if isinstance(generation, int) and not isinstance(generation, bool) and generation >= 0:
            return generation + 1
    return 1


def _parse_saved_at(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _manual_recovery_evidence(
    layers: tuple[OwnershipLayer, ...],
    saved_at: datetime | None,
    now: datetime,
    hass: HomeAssistant,
) -> dict[str, ManualRecoveryEvidence]:
    """Build conservative restart evidence from persisted intent and current HA truth."""
    evidence: dict[str, ManualRecoveryEvidence] = {}
    if saved_at is None:
        return evidence

    crossed_boundary = _crossed_nightly_boundary(saved_at, now)
    for layer in layers:
        entity_id = layer.metadata.get("persisted_entity_id")
        if not isinstance(entity_id, str):
            continue
        current = hass.states.get(entity_id)
        evidence[entity_id] = ManualRecoveryEvidence(
            temporally_valid=not crossed_boundary,
            desired_state_trustworthy=layer.appearance is not None,
            ownership_evidence_coherent=_state_matches_persisted_layer(current, layer),
        )
    return evidence


def _state_matches_persisted_layer(state: State | None, layer: OwnershipLayer) -> bool:
    """Require current HA truth to corroborate persisted homeowner ownership."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return False
    if layer.kind is LayerKind.MANUAL_OFF:
        return state.state == STATE_OFF
    if layer.kind is not LayerKind.MANUAL or layer.appearance is None:
        return False

    appearance = layer.appearance
    if appearance.scene_id is not None:
        # A light state alone cannot prove a scene identity. Future Hue evidence may do so.
        return False
    if appearance.on is True and state.state != STATE_ON:
        return False
    if appearance.on is False and state.state != STATE_OFF:
        return False
    if state.state != STATE_ON:
        return appearance.on is False

    attrs = state.attributes
    if appearance.brightness is not None and attrs.get("brightness") != appearance.brightness:
        return False
    if (
        appearance.color_temp_kelvin is not None
        and attrs.get("color_temp_kelvin") != appearance.color_temp_kelvin
    ):
        return False
    if appearance.rgb_color is not None and tuple(attrs.get("rgb_color", ())) != appearance.rgb_color:
        return False
    if appearance.effect is not None and attrs.get("effect") != appearance.effect:
        return False
    if appearance.xy_color is not None and not _float_tuple_matches(
        attrs.get("xy_color"), appearance.xy_color, tolerance=0.005
    ):
        return False
    if appearance.hs_color is not None and not _float_tuple_matches(
        attrs.get("hs_color"), appearance.hs_color, tolerance=0.5
    ):
        return False
    return True


def _float_tuple_matches(value: Any, expected: tuple[float, ...], *, tolerance: float) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != len(expected):
        return False
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
        return False
    return all(abs(float(actual) - target) <= tolerance for actual, target in zip(value, expected, strict=True))


def _crossed_nightly_boundary(start: datetime, end: datetime) -> bool:
    """Return whether a local 01:59 boundary occurred after start and no later than end."""
    if end <= start:
        return False
    local_tz = end.tzinfo
    if local_tz is None:
        return True
    cursor = start.astimezone(local_tz).date()
    end_local = end.astimezone(local_tz)
    while cursor <= end_local.date():
        boundary = datetime.combine(cursor, time(hour=1, minute=59), tzinfo=local_tz)
        if start < boundary <= end:
            return True
        cursor += timedelta(days=1)
    return False
