"""Observation-only Home Assistant adapter for Home Lighting Manager.

This module may observe Home Assistant state changes, persist shadow evidence, and expose diagnostic
states. It must never call Home Assistant services or command a device.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import replace
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
from homeassistant.helpers.event import (
    async_call_later,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .attribution_correlation import ExternalBurstCorrelator, ExternalTopologyEvent
from .engine import MAX_ENTITIES, NIGHTLY_BOUNDARY
from .intent_policy import (
    IntentAttributionSource,
    IntentEvidence,
    IntentEvidenceKind,
)
from .model import Appearance, LayerKind, OwnershipLayer
from .persistence import STORE_ENVELOPE_VERSION, deserialize_state
from .projection import effective_ownership
from .recovery import ManualRecoveryEvidence
from .shadow import ShadowDecision, ShadowObservation, ShadowRuntime

DOMAIN = "home_lighting_manager"
CONF_SHADOW_ENTITIES = "shadow_entities"
CONF_MANUAL_PRECEDENCE = "manual_precedence"
CONF_SURFACE_EXCLUSIONS = "surface_exclusions"
MANAGED_SURFACE_GROUPS = {
    "main_area": "light.holiday_main_area",
    "front_eve": "light.front_eve_zone",
    "path": "light.holiday_path",
    "backyard": "light.holiday_backyard",
}
MANAGED_SURFACE_GUARDS = {
    "main_area": "input_boolean.home_lighting_ha_guard_main_area",
    "front_eve": "input_boolean.home_lighting_ha_guard_front_eve",
    "path": "input_boolean.home_lighting_ha_guard_path",
    "backyard": "input_boolean.home_lighting_ha_guard_backyard",
}
SURFACE_MEMBERSHIP_REMOVAL_CONFIRM_SECONDS = 5.0
SURFACE_MANUAL_PRECEDENCE = 250
STORAGE_KEY = f"{DOMAIN}.shadow"
RUNTIME_DATA_KEY = f"{DOMAIN}_shadow_runtime"
DIAGNOSTIC_ENTITY_ID = "sensor.home_lighting_manager_shadow_health"
EVIDENCE_LEDGER_SIZE = 12
EXTERNAL_BURST_WINDOW_SECONDS = 2.0
RENDERER_ATTRIBUTION_TTL_SECONDS = 8.0
NIGHTLY_BOUNDARY_SETTLE_SECONDS = 15.0
RESTART_MANUAL_MAX_DOWNTIME = timedelta(hours=1)
PERSISTENCE_HEARTBEAT_INTERVAL = timedelta(minutes=1)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_SHADOW_ENTITIES): vol.All(cv.entity_ids, vol.Length(max=MAX_ENTITIES)),
                vol.Optional(CONF_MANUAL_PRECEDENCE, default={}): {
                    cv.entity_id: vol.All(vol.Coerce(int), vol.Range(min=0)),
                },
                vol.Optional(CONF_SURFACE_EXCLUSIONS, default={}): {
                    vol.In(tuple(MANAGED_SURFACE_GROUPS)): cv.entity_ids,
                },
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)



def _compact_armed_group_off_attempt(record: dict[str, Any]) -> dict[str, Any]:
    """Return the forensic fields needed for HA diagnostics without huge member maps."""
    fields = (
        "timestamp",
        "aggregate_entity",
        "operation",
        "group_id",
        "sequence",
        "generation",
        "runtime_generation",
        "runtime_revision",
        "stage",
        "result",
        "armed_group_count",
        "aggregate_member_count",
        "pending_leaf_count",
        "burst_topology",
        "candidate_qualified",
        "candidate_basis",
        "candidate_entity",
        "candidate_group",
        "attribution_source",
        "has_user_id",
        "has_parent_id",
        "latest_removal_serial",
    )
    return {field: record.get(field) for field in fields if field in record}


class HomeAssistantShadowObserver:
    """Bridge Home Assistant observations into the non-commanding shadow runtime."""

    def __init__(
        self,
        hass: HomeAssistant,
        entity_ids: list[str],
        manual_precedence: dict[str, int] | None = None,
        surface_exclusions: dict[str, list[str]] | None = None,
    ) -> None:
        if len(set(entity_ids)) > MAX_ENTITIES:
            raise ValueError("shadow entity capacity exceeded")
        self.hass = hass
        self._configured_entity_ids = frozenset(entity_ids)
        self.entity_ids = frozenset(entity_ids)
        self._configured_manual_precedence = dict(manual_precedence or {})
        self.manual_precedence = dict(self._configured_manual_precedence)
        self.surface_exclusions = {
            surface: frozenset(items)
            for surface, items in (surface_exclusions or {}).items()
        }
        self._surface_members_by_group: dict[str, tuple[str, ...]] = {}
        self._surface_seen_members: set[str] = set()
        self._pending_surface_membership: dict[str, tuple[tuple[str, ...], datetime]] = {}
        self._membership_confirmation_cancel: Any | None = None
        self.store: Store[dict[str, Any]] = Store(hass, STORE_ENVELOPE_VERSION, STORAGE_KEY)
        self.runtime = ShadowRuntime(generation=1, managed_entities=self.entity_ids)
        self._unsubscribers: list[Any] = []
        self._last_decision: ShadowDecision | None = None
        self._storage_status = "empty"
        self._evidence_ledger: deque[dict[str, Any]] = deque(maxlen=EVIDENCE_LEDGER_SIZE)
        self._external_correlator = ExternalBurstCorrelator(
            window_seconds=EXTERNAL_BURST_WINDOW_SECONDS
        )
        self._external_burst: dict[str, object] | None = None
        self._topology_members: dict[str, tuple[str, ...]] = {}
        self._nightly_boundary_settle_until: datetime | None = None
        self._post_boundary_off_entities: set[str] = set()
        self._guarded_ha_consequence_entities: dict[str, tuple[str, str, datetime]] = {}

    async def async_start(self) -> None:
        """Load trusted evidence and begin observation without command authority."""
        raw = await self.store.async_load()
        generation = _next_generation(raw)

        # Seed canonical membership before constructing the runtime. This admits
        # dynamically discovered members for persisted ownership recovery without
        # incrementing engine revision and thereby closing the recovery window.
        self._topology_members = self._snapshot_topology_cache()
        persisted_surface_members = _persisted_surface_members(raw, self.surface_exclusions)
        persisted_surface_seen = _persisted_surface_seen_members(raw)
        self._seed_surface_membership(
            self._topology_members,
            persisted_surface_members,
            persisted_surface_seen,
        )
        self.runtime = ShadowRuntime(generation=generation, managed_entities=self.entity_ids)

        if isinstance(raw, dict):
            # Runtime quarantine is not durable ownership. Ignore legacy epoch
            # fields; restart cannot reconstruct causal continuity from storage.
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
            async_track_time_interval(
                self.hass,
                self._async_persistence_heartbeat,
                PERSISTENCE_HEARTBEAT_INTERVAL,
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
        if self._membership_confirmation_cancel is not None:
            self._membership_confirmation_cancel()
            self._membership_confirmation_cancel = None
        while self._unsubscribers:
            unsub = self._unsubscribers.pop()
            unsub()
        self.hass.states.async_remove(DIAGNOSTIC_ENTITY_ID)

    async def async_reset_homeowner_control(self) -> dict[str, int]:
        """Start a fresh homeowner-observation epoch without commanding any lights."""
        result = self.runtime.reset_homeowner_control()
        self._external_correlator.reset()
        self._external_burst = None
        self._post_boundary_off_entities = self._boundary_managed_entities()
        self._last_decision = None
        self._evidence_ledger.clear()
        self._guarded_ha_consequence_entities.clear()
        await self.async_save()
        return result

    @callback
    def _checkpoint_ownership(self) -> None:
        """Publish current authority before any persistence await or deferred task."""
        self._publish_diagnostics()
        self.hass.async_create_task(self.async_save())

    async def _async_persistence_heartbeat(self, _now: datetime) -> None:
        """Refresh restart-age evidence without changing lighting ownership."""
        await self.async_save(publish=False)

    async def async_save(self, *, publish: bool = True) -> None:
        """Persist durable evidence; disk latency must not delay the authority view."""
        if publish:
            self._publish_diagnostics()
        payload = self.runtime.export_persistence()
        payload["generation"] = self.runtime.engine.generation
        payload["saved_at"] = dt_util.now().isoformat()
        payload["managed_surface_members"] = {
            surface: list(self._surface_members_by_group.get(group_id, ()))
            for surface, group_id in MANAGED_SURFACE_GROUPS.items()
        }
        payload["managed_surface_seen_members"] = sorted(self._surface_seen_members)
        await self.store.async_save(payload)
        self._storage_status = "saved"
        if publish:
            self._publish_diagnostics()

    async def _async_state_changed(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        if entity_id in MANAGED_SURFACE_GROUPS.values():
            self._refresh_topology_cache()
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

        observation = self._normalize_observation(observation, new_state)
        if (observation.evidence.renderer_consequence
            and observation.evidence.kind not in (
                IntentEvidenceKind.AVAILABILITY_CHANGE, IntentEvidenceKind.RECOVERY_TELEMETRY,
            )
            and observation.operation == "appearance"
            and not self._member_entity_ids_for_event(entity_id, new_state)):
            # A witnessed command receipt closes shutdown causality; evidence queries
            # and raw ON alone never do. Recovery receipts are excluded above.
            self._clear_post_boundary_off_for_entity(entity_id)
            observation = replace(observation, evidence=replace(
                observation.evidence, boundary_shutdown_pending=False,
            ))

        if self._member_entity_ids_for_event(entity_id, new_state):
            observation = replace(observation, evidence=replace(
                observation.evidence, aggregate_receipt=True
            ))  # Aggregate telemetry is not an explicit group-operation receipt.
        observation = replace(
            observation,
            sequence=self.runtime.reserve_sequence(),
            generation=self.runtime.engine.generation,
        )
        before = self._ownership_snapshot(entity_id)
        decision = self.runtime.observe(observation)
        self._last_decision = decision
        self._record_evidence(observation, decision, new_state, before=before)
        self._record_external_topology(observation, new_state)
        if decision.mutated:
            await self.async_save()
        else:
            self._publish_diagnostics()

    @callback
    def _apply_ha_guard_attribution(
        self, entity_id: str, observation: ShadowObservation
    ) -> ShadowObservation:
        """Collect exact renderer consequence evidence, even when Hue strips context.

        A surface guard alone cannot override an unrelated homeowner operation.
        This collector never changes ownership or closes boundary quarantine.
        """
        marker = self._active_guard_for_ha_consequence(entity_id, observation.operation)
        if marker is None:
            return observation
        return replace(observation, evidence=replace(
            observation.evidence, renderer_consequence=True,
            kind=(observation.evidence.kind
                  if observation.evidence.kind in (
                      IntentEvidenceKind.AVAILABILITY_CHANGE, IntentEvidenceKind.RECOVERY_TELEMETRY,
                  )
                  else IntentEvidenceKind.HLM_COMMAND_CONSEQUENCE),
        ), operation_id=None)

    def _normalize_observation(self, observation: ShadowObservation, new_state: State):
        """Collect causal facts; ownership policy lives in classify_intent."""
        observation = self._apply_ha_guard_attribution(observation.entity_id, observation)
        dynamics = new_state.attributes.get("dynamics")
        structural_states = [
            self.hass.states.get(sensor)
            for group, sensor in (
                (MANAGED_SURFACE_GROUPS["main_area"], "binary_sensor.hue_bridge_living_room"),
                (MANAGED_SURFACE_GROUPS["backyard"], "binary_sensor.hue_bridge_backyard"),
            )
            if observation.entity_id in self._surface_members_by_group.get(group, ())
        ]
        return replace(observation, evidence=replace(
            observation.evidence,
            boundary_settling=self._nightly_boundary_settling(),
            boundary_shutdown_pending=self._in_post_boundary_off_epoch(observation.entity_id),
            structural_activity=any(
                state is not None and state.state == STATE_ON for state in structural_states
            ),
            structural_state_unknown=any(
                state is None or state.state not in (STATE_ON, STATE_OFF)
                for state in structural_states
            ),
            dynamic_telemetry=(observation.operation != "off" and isinstance(dynamics, str)
                               and dynamics not in ("", "none")),
        ))

    @callback
    def register_renderer_command_consequences(
        self, entity_ids: Iterable[str], guard_entity: str, operation: str
    ) -> int:
        """Register exact leaves the legacy renderer is about to command.

        This is attribution metadata only. HLM remains observation-only and never
        dispatches a lighting command. The marker is accepted only for canonical
        members of the surface corresponding to the currently active guard.
        """
        if operation not in ("appearance", "off"):
            return 0
        surface = next(
            (
                name
                for name, guard in MANAGED_SURFACE_GUARDS.items()
                if guard == guard_entity
            ),
            None,
        )
        if surface is None or not self.hass.states.is_state(guard_entity, STATE_ON):
            return 0
        group_id = MANAGED_SURFACE_GROUPS[surface]
        members = set(self._surface_members_by_group.get(group_id, ()))
        accepted = 0
        for entity_id in entity_ids:
            if isinstance(entity_id, str) and entity_id in members:
                self._guarded_ha_consequence_entities[entity_id] = (
                    guard_entity,
                    operation,
                    dt_util.now(),
                )
                accepted += 1
        return accepted

    @callback
    def _active_guard_for_ha_consequence(
        self, entity_id: str, operation: str
    ) -> str | None:
        """Return active guard only when telemetry matches the renderer command."""
        marker = self._guarded_ha_consequence_entities.get(entity_id)
        if marker is None:
            return None
        guard_id, marked_operation, created_at = marker
        age = (dt_util.now() - created_at).total_seconds()
        if age < 0 or age > RENDERER_ATTRIBUTION_TTL_SECONDS:
            self._guarded_ha_consequence_entities.pop(entity_id, None)
            return None
        if not self.hass.states.is_state(guard_id, STATE_ON):
            self._guarded_ha_consequence_entities.pop(entity_id, None)
            return None
        if marked_operation != operation:
            return None
        return guard_id

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
    def _ownership_snapshot(self, entity_id: str) -> dict:
        layer = self.runtime.engine.resolve(entity_id).layer
        return {
            "exposed_kind": layer.kind.value if layer else None,
            "exposed_owner": layer.owner if layer else None,
            "layers": [item.layer_id for item in self.runtime.engine.layers(entity_id)],
            "protected": entity_id in self.runtime.reconciliation_protected_entities(),
        }

    @callback
    def _record_evidence(
        self, observation: ShadowObservation, decision: ShadowDecision, new_state: State,
        *, before: dict | None = None,
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
                "normalized_causality": {
                    name: getattr(evidence, name) for name in (
                        "renderer_consequence", "aggregate_receipt", "boundary_settling",
                        "boundary_shutdown_pending", "scene_rendering", "dynamic_telemetry",
                        "structural_activity", "structural_state_unknown", "scene_guard_active",
                    )
                },
                "canonical_reason": decision.intent.reason,
                "ownership_before": before,
                "ownership_after": self._ownership_snapshot(observation.entity_id),
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
    def _snapshot_topology_cache(self) -> dict[str, tuple[str, ...]]:
        cache: dict[str, tuple[str, ...]] = {}
        for state in self.hass.states.async_all():
            if not state.entity_id.startswith("light."):
                continue
            members = _member_entity_ids(state)
            if members:
                cache[state.entity_id] = members
        return cache

    @callback
    def _seed_surface_membership(
        self,
        cache: dict[str, tuple[str, ...]],
        persisted: dict[str, tuple[str, ...]] | None = None,
        persisted_seen: frozenset[str] | None = None,
    ) -> None:
        """Establish startup membership without mutating engine revision.

        Persisted canonical membership prevents a removed historical static leaf from
        reappearing after restart. Live additions are safe to admit immediately;
        apparent removals wait for the normal stability confirmation.
        """
        persisted = persisted or {}
        persisted_seen = persisted_seen or frozenset()
        for surface, group_id in MANAGED_SURFACE_GROUPS.items():
            exclusions = set(self.surface_exclusions.get(surface, ()))
            previous = set(persisted.get(surface, ())) - exclusions
            raw = cache.get(group_id, ())
            observed = set(raw) - exclusions
            active = tuple(sorted(previous | observed))
            if active:
                self._surface_members_by_group[group_id] = active

        current_surface_members = {
            entity_id
            for members in self._surface_members_by_group.values()
            for entity_id in members
        }
        historical_surface_members = {
            entity_id
            for members in persisted.values()
            for entity_id in members
        }
        self._surface_seen_members.update(
            current_surface_members | historical_surface_members | set(persisted_seen)
        )
        static_entities = set(self._configured_entity_ids) - self._surface_seen_members
        desired = frozenset(static_entities | current_surface_members)
        if len(desired) > MAX_ENTITIES:
            raise ValueError("dynamic surface membership exceeds entity capacity")
        self.entity_ids = desired
        self._refresh_surface_manual_precedence()

    @callback
    def _refresh_topology_cache(self) -> None:
        """Snapshot topology and reconcile canonical surface membership."""
        cache = self._snapshot_topology_cache()
        self._topology_members = cache
        self._reconcile_surface_membership(cache)

    @callback
    def _reconcile_surface_membership(
        self, cache: dict[str, tuple[str, ...]]
    ) -> None:
        """Adopt canonical Hue members without manufacturing ownership.

        Additions are safe to adopt immediately because canonical group topology is
        authoritative membership evidence, not homeowner intent. Removals are
        confirmed across a short delay so transient Hue startup/integration gaps
        cannot discard valid Manual state.
        """
        now = dt_util.now()
        changed = False
        removal_pending = False

        for surface, group_id in MANAGED_SURFACE_GROUPS.items():
            raw = cache.get(group_id)
            if not raw:
                continue
            observed = tuple(
                sorted(set(raw) - set(self.surface_exclusions.get(surface, ())))
            )
            if not observed:
                continue

            current = self._surface_members_by_group.get(group_id)
            if current is None:
                self._surface_members_by_group[group_id] = observed
                self._pending_surface_membership.pop(group_id, None)
                changed = True
                continue
            if observed == current:
                self._pending_surface_membership.pop(group_id, None)
                continue

            # Pure additions cannot invalidate existing ownership and are adopted
            # immediately. Any removal waits for stable second evidence.
            if set(current).issubset(observed):
                self._surface_members_by_group[group_id] = observed
                self._pending_surface_membership.pop(group_id, None)
                changed = True
                continue

            pending = self._pending_surface_membership.get(group_id)
            if (
                pending is not None
                and pending[0] == observed
                and (now - pending[1]).total_seconds()
                >= SURFACE_MEMBERSHIP_REMOVAL_CONFIRM_SECONDS
            ):
                self._surface_members_by_group[group_id] = observed
                self._pending_surface_membership.pop(group_id, None)
                changed = True
            else:
                if pending is None or pending[0] != observed:
                    self._pending_surface_membership[group_id] = (observed, now)
                removal_pending = True

        if removal_pending:
            self._schedule_membership_confirmation()

        if not changed:
            return

        current_surface_members = {
            entity_id
            for members in self._surface_members_by_group.values()
            for entity_id in members
        }
        self._surface_seen_members.update(current_surface_members)

        # Once an entity has been observed as a canonical surface member, surface
        # topology—not the historical shadow_entities list—governs its membership.
        static_entities = set(self._configured_entity_ids) - self._surface_seen_members
        desired = frozenset(static_entities | current_surface_members)
        if len(desired) > MAX_ENTITIES:
            raise ValueError("dynamic surface membership exceeds entity capacity")

        self.entity_ids = desired
        self._refresh_surface_manual_precedence()
        membership = self.runtime.update_managed_entities(desired)
        added = tuple(membership["added"])
        removed = tuple(membership["removed"])
        if not added and not removed:
            return

        self._post_boundary_off_entities.intersection_update(desired)
        self._managed_membership_changed(added, removed)
        self._publish_diagnostics()
        if self._unsubscribers and self.hass.is_running:
            self._checkpoint_ownership()

    @callback
    def _schedule_membership_confirmation(self) -> None:
        if self._membership_confirmation_cancel is not None:
            return

        def confirm(_now: datetime) -> None:
            self._membership_confirmation_cancel = None
            self._refresh_topology_cache()

        cancel = async_call_later(
            self.hass,
            SURFACE_MEMBERSHIP_REMOVAL_CONFIRM_SECONDS,
            confirm,
        )
        self._membership_confirmation_cancel = cancel

    @callback
    def _managed_membership_changed(
        self, added: tuple[str, ...], removed: tuple[str, ...]
    ) -> None:
        """Subclass hook for invalidating correlation evidence after topology changes."""

    def _refresh_surface_manual_precedence(self) -> None:
        """Apply one canonical Manual ingress policy to every current surface leaf."""
        policy = dict(self._configured_manual_precedence)
        for members in self._surface_members_by_group.values():
            for entity_id in members:
                policy.setdefault(entity_id, SURFACE_MANUAL_PRECEDENCE)
        self.manual_precedence = policy

    def _boundary_managed_entities(self) -> set[str]:
        return set(self._configured_manual_precedence) | {
            entity_id
            for members in self._surface_members_by_group.values()
            for entity_id in members
        }

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
    def _handle_nightly_boundary(self, now: datetime) -> None:
        self._nightly_boundary_settle_until = now + timedelta(
            seconds=NIGHTLY_BOUNDARY_SETTLE_SECONDS
        )
        self._post_boundary_off_entities = self._boundary_managed_entities()
        self.runtime.engine.expire_boundary(NIGHTLY_BOUNDARY)
        self._checkpoint_ownership()

    @callback
    def _clear_post_boundary_off_for_entity(self, entity_id: str) -> None:
        """Close shutdown causality after affirmative current evidence."""
        self._post_boundary_off_entities.discard(entity_id)

    @callback
    def _clear_post_boundary_off_for_members(self, entity_ids: tuple[str, ...]) -> None:
        """Close runtime shutdown quarantine for positively established members."""
        self._post_boundary_off_entities.difference_update(entity_ids)

    @callback
    def _in_post_boundary_off_epoch(self, entity_id: str) -> bool:
        """Return whether ambiguous external telemetry must still fail closed."""
        return entity_id in self._post_boundary_off_entities

    @callback
    def _nightly_boundary_settling(self, now: datetime | None = None) -> bool:
        """Return whether late Hue telemetry is still inside the 01:59 settling window."""
        settle_until = self._nightly_boundary_settle_until
        if settle_until is None:
            return False
        current = dt_util.now() if now is None else now
        return current <= settle_until

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

    def pending_intent_entities(self) -> tuple[str, ...]:
        """Provisional evidence is not ownership but must settle before repair."""
        return ()

    def off_attempt_diagnostics(self) -> dict:
        """Optional read-only commissioning evidence supplied by the promotion adapter."""
        return {}

    @callback
    def _publish_diagnostics(self) -> None:
        """Publish a recorder-safe operational contract and bounded diagnostics.

        The observer retains its full bounded evidence ledgers in memory. Home
        Assistant's state machine is intentionally narrower because Recorder
        rejects state attributes larger than 16 KiB.
        """
        diagnostics = self.runtime.diagnostics()

        projection_entities = set(self.manual_precedence)
        projection_groups: dict[str, tuple[str, ...]] = {}
        for group_id in MANAGED_SURFACE_GROUPS.values():
            members = self._surface_members_by_group.get(group_id, ())
            projection_entities.update(members)
            if members:
                projection_groups[group_id] = members

        ownership = self.runtime.ownership_diagnostics()
        published_ownership_entities = {
            entity_id: record
            for entity_id, record in ownership.get("ownership_entities", {}).items()
            if record.get("layers")
            or record.get("manual_appearance")
            or record.get("manual_off")
        }

        off_diagnostics = self.off_attempt_diagnostics()
        attrs: dict[str, Any] = {
            "pending_intent_entities": list(self.pending_intent_entities()),
            "generation": diagnostics.generation,
            "effective_ownership": effective_ownership(
                self.runtime.engine,
                projection_entities,
                projection_groups,
            ),
            "observed_events": diagnostics.observed_events,
            "homeowner_events": diagnostics.homeowner_events,
            "ignored_or_hlm_events": diagnostics.ignored_or_hlm_events,
            "managed_entities": diagnostics.managed_entities,
            "suppressed_sessions": diagnostics.suppressed_sessions,
            "configured_entities": len(self.entity_ids),
            "precedence_configured_entities": sum(
                1 for entity_id in self.entity_ids if entity_id in self.manual_precedence
            ),
            "manual_precedence_entities": sorted(self.manual_precedence),
            "reconciliation_protected_entities": list(
                self.runtime.reconciliation_protected_entities()
            ),
            "storage_status": self._storage_status,
            "command_authority": False,
            "evidence_ledger_size": EVIDENCE_LEDGER_SIZE,
            "recent_evidence": list(self._evidence_ledger)[-1:],
            "external_burst_window_seconds": EXTERNAL_BURST_WINDOW_SECONDS,
            "external_burst": self._external_burst,
            "nightly_boundary_settle_seconds": NIGHTLY_BOUNDARY_SETTLE_SECONDS,
            "nightly_boundary_settling": self._nightly_boundary_settling(),
            "nightly_boundary_settle_until": (
                self._nightly_boundary_settle_until.isoformat()
                if self._nightly_boundary_settle_until is not None
                else None
            ),
            "post_boundary_off_entities": sorted(self._post_boundary_off_entities),
            "topology_aggregate_count": len(self._topology_members),
            "topology_member_count": sum(
                len(members) for members in self._topology_members.values()
            ),
            "topology_aggregate_entities": sorted(self._topology_members)[:8],
            "topology_cache_ready": bool(self._topology_members),
            "managed_surface_groups": dict(MANAGED_SURFACE_GROUPS),
            "managed_surface_members": {
                surface: list(self._surface_members_by_group.get(group_id, ()))
                for surface, group_id in MANAGED_SURFACE_GROUPS.items()
            },
            "managed_surface_exclusions": {
                surface: sorted(items)
                for surface, items in self.surface_exclusions.items()
                if items
            },
            "managed_surface_manual_precedence": SURFACE_MANUAL_PRECEDENCE,
            "runtime_revision": ownership.get("runtime_revision"),
            "ownership_entities": published_ownership_entities,
            "ownership_entities_truncated": ownership.get(
                "ownership_entities_truncated", False
            ),
            "family_sessions": ownership.get("family_sessions", []),
            "ended_family_sessions": ownership.get("ended_family_sessions", []),
            "group_off_sequences": ownership.get("group_off_sequences", {}),
            "group_off_removals": ownership.get("group_off_removals", {}),
            "last_mutation_reason": ownership.get("last_mutation_reason"),
            "latest_homeowner_operation": ownership.get("latest_homeowner_operation"),
            "latest_operation_rejection": ownership.get("latest_operation_rejection"),
            "recent_operations": list(ownership.get("recent_operations", []))[-2:],
            "armed_group_off_attempt_count": len(
                off_diagnostics.get("armed_group_off_attempts", [])
            ),
            "armed_group_off_attempts": [
                _compact_armed_group_off_attempt(item)
                for item in list(
                    off_diagnostics.get("armed_group_off_attempts", [])
                )[-2:]
            ],
            "off_leaf_evidence": list(off_diagnostics.get("off_leaf_evidence", []))[-4:],
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
    if old_state is None and not explicit_user:
        # First discovery/startup reports are availability evidence, not a command.
        # Aggregate fanout cannot turn bootstrap telemetry into homeowner intent.
        return ShadowObservation(entity_id, IntentEvidence(
            IntentEvidenceKind.RECOVERY_TELEMETRY,
            attribution_source=_attribution_source_from_context(context),
            has_user_id=has_user_id, has_parent_id=has_parent_id,
        ))
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

    # A repeated state report with the same direct-user context is the same receipt,
    # not a second OFF. Context-less external receipts retain correlation identity.
    operation_id = f"ha:{context.id}:{entity_id}" if explicit_user else None
    if new_state.state == STATE_OFF:
        return ShadowObservation(
            entity_id=entity_id, evidence=evidence, operation="off", operation_id=operation_id
        )
    if new_state.state != STATE_ON:
        return None

    return ShadowObservation(
        entity_id=entity_id,
        evidence=evidence,
        appearance=_appearance_from_state(new_state),
        operation="appearance",
        operation_id=operation_id,
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
        color_mode=attrs.get("color_mode") if isinstance(attrs.get("color_mode"), str) else None,
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


def _persisted_surface_seen_members(raw: Any) -> frozenset[str]:
    """Remember entities that canonical topology has governed across prior restarts."""
    if not isinstance(raw, dict):
        return frozenset()
    items = raw.get("managed_surface_seen_members")
    if not isinstance(items, list):
        return frozenset()
    seen = {
        item
        for item in items
        if isinstance(item, str)
        and item.startswith("light.")
        and len(item) <= 256
    }
    if len(seen) > MAX_ENTITIES:
        return frozenset()
    return frozenset(seen)


def _persisted_surface_members(
    raw: Any,
    exclusions: dict[str, frozenset[str]],
) -> dict[str, tuple[str, ...]]:
    """Return bounded canonical membership evidence persisted by the prior runtime."""
    if not isinstance(raw, dict):
        return {}
    payload = raw.get("managed_surface_members")
    if not isinstance(payload, dict):
        return {}

    result: dict[str, tuple[str, ...]] = {}
    total = 0
    for surface in MANAGED_SURFACE_GROUPS:
        items = payload.get(surface)
        if not isinstance(items, list):
            continue
        blocked = exclusions.get(surface, frozenset())
        members = tuple(
            sorted(
                {
                    item
                    for item in items
                    if isinstance(item, str)
                    and item.startswith("light.")
                    and len(item) <= 256
                    and item not in blocked
                }
            )
        )
        total += len(members)
        if total > MAX_ENTITIES:
            return {}
        if members:
            result[surface] = members
    return result


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


def _manual_ownership_survives_restart(saved_at: datetime, now: datetime) -> bool:
    """Preserve Manual only across short restarts that do not cross 01:59."""
    if saved_at > now:
        return False
    return (
        now - saved_at < RESTART_MANUAL_MAX_DOWNTIME
        and not _crossed_nightly_boundary(saved_at, now)
    )


def _manual_recovery_evidence(
    layers: tuple[OwnershipLayer, ...],
    saved_at: datetime | None,
    now: datetime,
    hass: HomeAssistant,
) -> dict[str, ManualRecoveryEvidence]:
    """Build conservative restart evidence from persisted intent and current HA truth."""
    evidence: dict[str, ManualRecoveryEvidence] = {}
    if saved_at is None or saved_at > now:
        return evidence

    temporally_valid = _manual_ownership_survives_restart(saved_at, now)
    for layer in layers:
        entity_id = layer.metadata.get("persisted_entity_id")
        if not isinstance(entity_id, str):
            continue
        evidence[entity_id] = ManualRecoveryEvidence(
            temporally_valid=temporally_valid,
            desired_state_trustworthy=layer.appearance is not None,
            # For a short restart, persisted explicit homeowner ownership is
            # authoritative. Startup/recovery telemetry must not veto it.
            ownership_evidence_coherent=temporally_valid,
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
    # A saved ON-only record cannot reconstruct a currently color-capable light's appearance.
    current_mode = attrs.get("color_mode")
    if attrs.get("brightness") is not None and appearance.brightness is None:
        return False
    color_fields = ("color_temp_kelvin", "xy_color", "rgb_color", "hs_color")
    if (any(attrs.get(field) is not None for field in color_fields)
            and not any(getattr(appearance, field) is not None for field in color_fields)):
        return False
    required = {"brightness": "brightness", "color_temp": "color_temp_kelvin",
                "xy": "xy_color", "rgb": "rgb_color", "hs": "hs_color"}
    if current_mode in required and getattr(appearance, required[current_mode]) is None:
        return False
    if appearance.color_mode is not None and current_mode != appearance.color_mode:
        return False
    if appearance.brightness is not None and attrs.get("brightness") != appearance.brightness:
        return False
    if (
        appearance.color_temp_kelvin is not None
        and attrs.get("color_temp_kelvin") != appearance.color_temp_kelvin
    ):
        return False
    if appearance.rgb_color is not None and _optional_int_tuple(attrs.get("rgb_color"), 3) != appearance.rgb_color:
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
    end_local = end.astimezone(local_tz)
    boundary = datetime.combine(end_local.date(), time(hour=1, minute=59), tzinfo=local_tz)
    if boundary > end_local:
        boundary -= timedelta(days=1)
    return start < boundary <= end
