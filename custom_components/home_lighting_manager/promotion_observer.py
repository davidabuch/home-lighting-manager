"""Shadow homeowner promotion for qualified external topology evidence.

This adapter extends the commissioned observation-only HA observer. It may promote a qualified
context-less external leaf event into shadow homeowner intent, but it never calls Home Assistant
services or commands a light.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant, State, callback
from homeassistant.util import dt as dt_util

from .ha_observer import EXTERNAL_BURST_WINDOW_SECONDS, HomeAssistantShadowObserver
from .intent_policy import (
    IntentAttributionSource,
    IntentDisposition,
    IntentEvidence,
    IntentEvidenceKind,
)
from .shadow import ShadowDecision, ShadowObservation


@dataclass(frozen=True)
class _PendingExternalLeaf:
    """Original context-less leaf observation retained for one short correlation window."""

    observed_at: datetime
    observation: ShadowObservation
    state: State


class PromotingHomeAssistantShadowObserver(HomeAssistantShadowObserver):
    """Promote only qualified external leaf correlation into shadow Manual ownership."""

    def __init__(
        self,
        hass: HomeAssistant,
        entity_ids: list[str],
        manual_precedence: dict[str, int] | None = None,
    ) -> None:
        super().__init__(hass, entity_ids, manual_precedence)
        self._pending_external_leaves: dict[str, _PendingExternalLeaf] = {}
        self._last_external_promotion_key: tuple[str, str] | None = None
        self._last_external_promotion_outcome: dict[str, object] | None = None

    @callback
    def _record_external_topology(
        self, observation: ShadowObservation, new_state: State
    ) -> None:
        """Correlate external topology, then promote a qualified leaf at most once per burst."""
        if (
            observation.evidence.attribution_source
            is not IntentAttributionSource.UNATTRIBUTED_EXTERNAL
        ):
            super()._record_external_topology(observation, new_state)
            return

        observed_at = dt_util.now()
        members = self._member_entity_ids_for_event(observation.entity_id, new_state)
        if not members:
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
        if not isinstance(candidate, dict) or candidate.get("qualified") is not True:
            return

        entity_id = candidate.get("entity_id")
        started_at = burst.get("started_at")
        if not isinstance(entity_id, str) or not isinstance(started_at, str):
            return

        promotion_key = (started_at, entity_id)
        if (
            promotion_key == self._last_external_promotion_key
            and self._last_external_promotion_outcome is not None
        ):
            candidate.update(self._last_external_promotion_outcome)
            return

        pending = self._pending_external_leaves.get(entity_id)
        if pending is None:
            candidate.update(
                {
                    "promoted_to_homeowner": False,
                    "manual_ownership_recorded": False,
                    "promotion_reason": "qualified burst lacked retained leaf observation",
                }
            )
            return

        promoted_observation = ShadowObservation(
            entity_id=pending.observation.entity_id,
            evidence=IntentEvidence(
                kind=IntentEvidenceKind.CORRELATED_EXTERNAL_HOMEOWNER_COMMAND,
                succeeded=True,
                attribution_coherent=True,
                attribution_source=IntentAttributionSource.UNATTRIBUTED_EXTERNAL,
                has_user_id=False,
                has_parent_id=False,
            ),
            appearance=pending.observation.appearance,
            operation=pending.observation.operation,
            manual_precedence=pending.observation.manual_precedence,
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
    def _prune_pending_external_leaves(self, now: datetime) -> None:
        """Bound retained leaf evidence to the same short window as burst correlation."""
        stale = [
            entity_id
            for entity_id, pending in self._pending_external_leaves.items()
            if (now - pending.observed_at).total_seconds() > EXTERNAL_BURST_WINDOW_SECONDS
        ]
        for entity_id in stale:
            self._pending_external_leaves.pop(entity_id, None)


def _promotion_outcome(decision: ShadowDecision) -> dict[str, object]:
    """Expose intent promotion separately from actual Manual-layer mutation."""
    return {
        "promoted_to_homeowner": (
            decision.intent.disposition is IntentDisposition.HOMEOWNER_INTENT
        ),
        "manual_ownership_recorded": decision.mutated,
        "promotion_reason": decision.reason,
    }
