"""Shadow homeowner promotion for qualified external topology evidence.

This adapter extends the commissioned observation-only HA observer. It may promote qualified
context-less external leaf or exact-group evidence into shadow homeowner intent, but it never
calls Home Assistant services or commands a light.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .attribution_correlation import (
    ExternalBurstTopology,
    resolve_unique_exact_group,
)
from .ha_observer import EXTERNAL_BURST_WINDOW_SECONDS, HomeAssistantShadowObserver
from .intent_policy import (
    IntentAttributionSource,
    IntentDisposition,
    IntentEvidence,
    IntentEvidenceKind,
)
from .operations import HomeownerOperation, MemberOutcome, OperationResult
from .shadow import ShadowDecision, ShadowObservation

_SURFACE_GUARDS: tuple[tuple[str, str], ...] = (
    ("light.holiday_main_area", "input_boolean.home_lighting_ha_guard_main_area"),
    ("light.front_eve_zone", "input_boolean.home_lighting_ha_guard_front_eve"),
    ("light.holiday_path", "input_boolean.home_lighting_ha_guard_path"),
    ("light.holiday_backyard", "input_boolean.home_lighting_ha_guard_backyard"),
)


@dataclass(frozen=True)
class _PendingExternalLeaf:
    """Original context-less leaf observation retained for one short correlation window."""

    observed_at: datetime
    observation: ShadowObservation
    state: State


class PromotingHomeAssistantShadowObserver(HomeAssistantShadowObserver):
    """Promote only qualified external leaf or exact-group correlation into shadow ownership."""

    def __init__(
        self,
        hass: HomeAssistant,
        entity_ids: list[str],
        manual_precedence: dict[str, int] | None = None,
    ) -> None:
        super().__init__(hass, entity_ids, manual_precedence)
        self._pending_external_leaves: dict[str, _PendingExternalLeaf] = {}
        self._pending_single_promotions: dict[
            tuple[int | None, str], Callable[[], None]
        ] = {}
        self._last_external_promotion_key: tuple[int | None, str] | None = None
        self._last_external_promotion_outcome: dict[str, object] | None = None
        self._last_external_group_promotion_key: tuple[
            str, str, tuple[tuple[str, int], ...]
        ] | None = None
        self._last_external_group_promotion_outcome: dict[str, object] | None = None
        self._external_group_burst_topology: dict[str, tuple[str, ...]] = {}
        self._external_group_last_observed_at: datetime | None = None

    async def async_shutdown(self) -> None:
        """Cancel provisional promotions before unregistering the observer."""
        for cancel in tuple(self._pending_single_promotions.values()):
            cancel()
        self._pending_single_promotions.clear()
        await super().async_shutdown()

    @callback
    def _record_external_topology(
        self, observation: ShadowObservation, new_state: State
    ) -> None:
        """Correlate external topology, then promote only qualified exact intent."""
        if (
            observation.evidence.kind is IntentEvidenceKind.AVAILABILITY_CHANGE
            or observation.evidence.has_parent_id
        ):
            self._pending_external_leaves.pop(observation.entity_id, None)
            self._cancel_pending_single_for_entities((observation.entity_id,))
        if (
            observation.evidence.attribution_source
            is not IntentAttributionSource.UNATTRIBUTED_EXTERNAL
        ):
            super()._record_external_topology(observation, new_state)
            return

        observed_at = dt_util.now()
        members = self._member_entity_ids_for_event(observation.entity_id, new_state)
        if not members:
            self._reset_external_burst_for_cross_surface_leaf(observation.entity_id)
        self._snapshot_group_topology_for_burst(observed_at)
        automatic_reason = (
            self._automatic_external_evidence_reason(observation.entity_id, new_state)
            if not members
            else None
        )
        if automatic_reason is not None:
            self._pending_external_leaves.pop(observation.entity_id, None)
            self._cancel_pending_single_for_entities((observation.entity_id,))
        elif (
            not members
            and observation.evidence.kind is IntentEvidenceKind.UNKNOWN
            and observation.operation in ("appearance", "off")
            and (observation.operation == "off" or observation.appearance is not None)
        ):
            previous = self._pending_external_leaves.get(observation.entity_id)
            if (
                previous is not None
                and previous.observation.sequence != observation.sequence
            ):
                self._cancel_pending_single_for_entities((observation.entity_id,))
            self._pending_external_leaves[observation.entity_id] = _PendingExternalLeaf(
                observed_at=observed_at,
                observation=observation,
                state=new_state,
            )
        self._prune_pending_external_leaves(observed_at)

        super()._record_external_topology(observation, new_state)
        burst = self._external_burst
        if not isinstance(burst, dict):
            return

        candidate = burst.get("external_intent_candidate")
        if not isinstance(candidate, dict):
            return
        if candidate.get("qualified") is True:
            self._promote_single_candidate(
                candidate,
                members,
                current_entity_id=observation.entity_id,
            )
            return
        self._promote_exact_group_candidate(
            burst,
            candidate,
            current_entity_id=observation.entity_id,
            burst_topology=self._external_group_burst_topology,
        )

    @callback
    def _reset_external_burst_for_cross_surface_leaf(self, entity_id: str) -> None:
        """Do not let unrelated surface churn poison one homeowner correlation window."""
        burst = self._external_burst
        if not isinstance(burst, dict):
            return
        prior_leaves = _string_tuple(burst.get("leaf_entities"))
        if not prior_leaves or entity_id in prior_leaves:
            return

        current_surfaces = self._commissioned_surfaces_for_entity(entity_id)
        if not current_surfaces:
            return
        prior_surfaces = set()
        for prior in prior_leaves:
            prior_surfaces.update(self._commissioned_surfaces_for_entity(prior))
        if not prior_surfaces or current_surfaces & prior_surfaces:
            return

        self._external_correlator.reset()
        self._external_burst = None
        self._external_group_burst_topology = {}
        self._external_group_last_observed_at = None

    @callback
    def _commissioned_surfaces_for_entity(self, entity_id: str) -> set[str]:
        """Return the narrow commissioned ownership surfaces containing one leaf."""
        return {
            aggregate_id
            for aggregate_id, _guard_id in _SURFACE_GUARDS
            if entity_id in self._topology_members.get(aggregate_id, ())
        }

    @callback
    def _automatic_external_evidence_reason(
        self, entity_id: str, new_state: State
    ) -> str | None:
        """Return why context-less leaf telemetry must not be promoted as homeowner intent."""
        dynamics = new_state.attributes.get("dynamics")
        if isinstance(dynamics, str) and dynamics not in ("", "none"):
            return "active Hue dynamics are automatic scene telemetry"

        matching_guards = [
            guard_id
            for aggregate_id, guard_id in _SURFACE_GUARDS
            if entity_id in self._topology_members.get(aggregate_id, ())
        ]
        if not matching_guards and entity_id in self.manual_precedence:
            # Startup or sparse Hue telemetry can precede aggregate membership.
            # A short active manager guard is then sufficient negative evidence:
            # ambiguity must fail closed rather than manufacture Manual ownership.
            matching_guards = [guard_id for _, guard_id in _SURFACE_GUARDS]

        for guard_id in matching_guards:
            guard = self.hass.states.get(guard_id)
            if guard is not None and guard.state == "on":
                return f"HA command guard is active: {guard_id}"
        return None

    @callback
    def _snapshot_group_topology_for_burst(self, observed_at: datetime) -> None:
        """Freeze exact-group authority at burst start so new telemetry cannot self-qualify."""
        previous = self._external_group_last_observed_at
        if previous is None:
            new_burst = True
        else:
            delta = (observed_at - previous).total_seconds()
            new_burst = delta < 0 or delta > EXTERNAL_BURST_WINDOW_SECONDS
        if new_burst:
            self._external_group_burst_topology = dict(self._topology_members)
        self._external_group_last_observed_at = observed_at

    @callback
    def _promote_single_candidate(
        self,
        candidate: dict[str, object],
        members: tuple[str, ...],
        *,
        current_entity_id: str,
    ) -> None:
        """Promote a leaf immediately unless a pre-known group burst may still resolve."""
        entity_id = candidate.get("entity_id")
        if not isinstance(entity_id, str):
            return

        # A later leaf command needs fresh aggregate corroboration. Reusing the burst start
        # as identity lost rapid ON/OFF or appearance updates within the same window.
        if not members or entity_id not in members:
            return
        pending = self._pending_external_leaves.get(entity_id)
        promotion_key = (pending.observation.sequence if pending else None, entity_id)
        if (
            promotion_key == self._last_external_promotion_key
            and self._last_external_promotion_outcome is not None
        ):
            candidate.update(self._last_external_promotion_outcome)
            return

        if pending is None:
            candidate.update(
                {
                    "promoted_to_homeowner": False,
                    "manual_ownership_recorded": False,
                    "promotion_reason": "qualified burst lacked retained leaf observation",
                }
            )
            return

        # Only a group that was known before this burst can later become exact-group
        # authority. Newly learned aggregate telemetry cannot self-qualify, so it must not
        # delay the already commissioned single-leaf promotion path.
        preknown_members = self._external_group_burst_topology.get(current_entity_id)
        if (
            len(members) > 1
            and preknown_members is not None
            and frozenset(preknown_members) == frozenset(members)
        ):
            self._schedule_single_promotion(candidate, promotion_key, pending)
            return

        self._apply_single_promotion(candidate, promotion_key, pending)

    @callback
    def _schedule_single_promotion(
        self,
        candidate: dict[str, object],
        promotion_key: tuple[int | None, str],
        pending: _PendingExternalLeaf,
    ) -> None:
        """Hold a leaf promotion until the current group-correlation window closes."""
        if promotion_key in self._pending_single_promotions:
            candidate.update(
                {
                    "promoted_to_homeowner": False,
                    "manual_ownership_recorded": False,
                    "promotion_reason": "awaiting exact-group correlation window",
                }
            )
            return

        entity_id = pending.observation.entity_id

        @callback
        def _finalize(_now: datetime) -> None:
            self._pending_single_promotions.pop(promotion_key, None)
            current = self._pending_external_leaves.get(entity_id)
            if (
                current is None
                or current.observation.sequence != pending.observation.sequence
            ):
                return
            self._apply_single_promotion(candidate, promotion_key, pending)

        self._pending_single_promotions[promotion_key] = async_call_later(
            self.hass,
            EXTERNAL_BURST_WINDOW_SECONDS,
            _finalize,
        )
        candidate.update(
            {
                "promoted_to_homeowner": False,
                "manual_ownership_recorded": False,
                "promotion_reason": "awaiting exact-group correlation window",
            }
        )

    @callback
    def _apply_single_promotion(
        self,
        candidate: dict[str, object],
        promotion_key: tuple[int | None, str],
        pending: _PendingExternalLeaf,
    ) -> None:
        """Apply one retained leaf observation through the existing core path."""
        promoted_observation = ShadowObservation(
            entity_id=pending.observation.entity_id,
            evidence=_correlated_homeowner_evidence(),
            appearance=pending.observation.appearance,
            operation=pending.observation.operation,
            manual_precedence=pending.observation.manual_precedence,
            sequence=pending.observation.sequence,
            generation=pending.observation.generation,
            operation_id=pending.observation.operation_id,
        )
        decision = self.runtime.observe(promoted_observation)
        self._record_evidence(promoted_observation, decision, pending.state)

        outcome = _promotion_outcome(decision)
        candidate.update(outcome)
        self._last_external_promotion_key = promotion_key
        self._last_external_promotion_outcome = outcome

        if decision.mutated:
            self.hass.async_create_task(self.async_save())

    @callback
    def _cancel_pending_single_for_entities(self, entity_ids: tuple[str, ...]) -> None:
        """Cancel provisional leaf promotions consumed by newer or exact-group evidence."""
        wanted = frozenset(entity_ids)
        stale_keys = [
            key for key in self._pending_single_promotions if key[1] in wanted
        ]
        for key in stale_keys:
            cancel = self._pending_single_promotions.pop(key)
            cancel()

    @callback
    def _promote_exact_group_candidate(
        self,
        burst: dict[str, object],
        candidate: dict[str, object],
        *,
        current_entity_id: str,
        burst_topology: dict[str, tuple[str, ...]],
    ) -> None:
        """Promote only one pre-known, uniquely identifiable exact aggregate operation."""
        if (
            burst.get("topology")
            != ExternalBurstTopology.MULTI_LEAF_WITH_AGGREGATE_PROPAGATION.value
        ):
            return
        leaf_entities = _string_tuple(burst.get("leaf_entities"))
        aggregate_entities = _string_tuple(burst.get("aggregate_entities"))
        if len(leaf_entities) < 2:
            return

        group_id = resolve_unique_exact_group(
            leaf_entities=leaf_entities,
            observed_aggregate_entities=aggregate_entities,
            topology_members=burst_topology,
        )
        # The uniquely exact group must itself be the corroborating event. A
        # containing/nested aggregate arriving later cannot retroactively choose it.
        if group_id is None or group_id != current_entity_id:
            return

        # Once a pre-known exact group is proven, any provisional leaf promotions
        # from this same burst are consumed by the group transaction. They must not
        # race the exact operation or create a newer leaf sequence that rejects it.
        self._cancel_pending_single_for_entities(leaf_entities)

        candidate.update(
            {
                "qualified": True,
                "entity_id": None,
                "group_id": group_id,
                "basis": "unique_exact_group_with_aggregate_propagation",
            }
        )
        pending = [self._pending_external_leaves.get(entity_id) for entity_id in leaf_entities]
        if any(item is None for item in pending):
            candidate.update(
                {
                    "promoted_to_homeowner": False,
                    "manual_ownership_recorded": False,
                    "promotion_reason": "qualified exact group lacked retained member observation",
                }
            )
            return
        retained = tuple(item for item in pending if item is not None)

        kinds = {item.observation.operation for item in retained}
        if len(kinds) != 1 or not kinds <= {"appearance", "off"}:
            candidate.update(
                {
                    "promoted_to_homeowner": False,
                    "manual_ownership_recorded": False,
                    "promotion_reason": "qualified exact group had mixed member operations",
                }
            )
            return
        kind = next(iter(kinds))
        if kind == "appearance" and any(
            item.observation.appearance is None for item in retained
        ):
            candidate.update(
                {
                    "promoted_to_homeowner": False,
                    "manual_ownership_recorded": False,
                    "promotion_reason": "qualified exact group had incomplete member appearance",
                }
            )
            return

        sequences = [item.observation.sequence for item in retained]
        generations = {item.observation.generation for item in retained}
        if any(type(sequence) is not int for sequence in sequences) or len(generations) != 1:
            candidate.update(
                {
                    "promoted_to_homeowner": False,
                    "manual_ownership_recorded": False,
                    "promotion_reason": "qualified exact group lacked coherent ingress identity",
                }
            )
            return
        generation = next(iter(generations))
        if type(generation) is not int:
            return

        oldest = min(item.observed_at for item in retained)
        newest = max(item.observed_at for item in retained)
        age = (newest - oldest).total_seconds()
        if age < 0 or age > EXTERNAL_BURST_WINDOW_SECONDS:
            return

        sequence_pairs = tuple(
            sorted(
                (item.observation.entity_id, int(item.observation.sequence))
                for item in retained
            )
        )
        promotion_key = (group_id, kind, sequence_pairs)
        if (
            promotion_key == self._last_external_group_promotion_key
            and self._last_external_group_promotion_outcome is not None
        ):
            candidate.update(self._last_external_group_promotion_outcome)
            return

        # Use the earliest member ingress sequence. If a newer direct homeowner
        # intent arrived on any member while this group was still correlating, the
        # core stale-intent guard rejects this inferred operation atomically.
        sequence = min(int(item.observation.sequence) for item in retained)
        operation = HomeownerOperation(
            operation_id=f"external-group:{generation}:{sequence}",
            sequence=sequence,
            generation=generation,
            kind=kind,
            evidence=_correlated_homeowner_evidence(),
            members=tuple(
                MemberOutcome(
                    item.observation.entity_id,
                    appearance=item.observation.appearance,
                    manual_precedence=item.observation.manual_precedence,
                )
                for item in retained
            ),
            group_id=group_id,
            require_legacy_policy=True,
        )
        result = self.runtime.observe_operation(operation)
        outcome = _promotion_outcome(result)
        candidate.update(outcome)
        self._last_external_group_promotion_key = promotion_key
        self._last_external_group_promotion_outcome = outcome

        if result.mutated:
            self.hass.async_create_task(self.async_save())

    @callback
    def _prune_pending_external_leaves(self, now: datetime) -> None:
        """Bound retained leaf evidence to the same short window as burst correlation."""
        stale = [
            entity_id
            for entity_id, pending in self._pending_external_leaves.items()
            if (now - pending.observed_at).total_seconds() > EXTERNAL_BURST_WINDOW_SECONDS
            or (now - pending.observed_at).total_seconds() < 0
        ]
        for entity_id in stale:
            self._pending_external_leaves.pop(entity_id, None)
            self._cancel_pending_single_for_entities((entity_id,))


def _correlated_homeowner_evidence() -> IntentEvidence:
    return IntentEvidence(
        kind=IntentEvidenceKind.CORRELATED_EXTERNAL_HOMEOWNER_COMMAND,
        succeeded=True,
        attribution_coherent=True,
        attribution_source=IntentAttributionSource.UNATTRIBUTED_EXTERNAL,
        has_user_id=False,
        has_parent_id=False,
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return ()
    return tuple(sorted(set(value)))


def _promotion_outcome(decision: ShadowDecision | OperationResult) -> dict[str, object]:
    """Expose intent promotion separately from actual Manual-layer mutation."""
    return {
        "promoted_to_homeowner": (
            decision.intent.disposition is IntentDisposition.HOMEOWNER_INTENT
        ),
        "manual_ownership_recorded": decision.mutated,
        "promotion_reason": decision.reason,
    }
