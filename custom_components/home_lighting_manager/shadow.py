"""Non-commanding shadow runtime for Home Lighting Manager.

The shadow runtime owns no lights. It accepts already-classified evidence, maintains an in-memory
ownership model, and exports persistence evidence/diagnostics. It deliberately has no Home
Assistant service-call dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

from .engine import NIGHTLY_BOUNDARY, OwnershipEngine
from .intent import IntentDecision, IntentDisposition, IntentEvidence, classify_intent
from .model import Appearance, FamilySession, LayerKind, OffAction, OwnershipLayer
from .persistence import PersistedOwnershipState, serialize_state
from .recovery import (
    FamilyRecoveryEvidence,
    ManualRecoveryEvidence,
    RecoveryAction,
    recover_family_session,
    recover_layer,
)


@dataclass(frozen=True)
class ShadowObservation:
    """One high-level observation fed to the shadow runtime by a future HA adapter."""

    entity_id: str
    evidence: IntentEvidence
    appearance: Appearance | None = None
    operation: str = "appearance"
    manual_precedence: int | None = None


@dataclass(frozen=True)
class ShadowDecision:
    """Recorded shadow decision; never a command plan."""

    entity_id: str
    intent: IntentDecision
    mutated: bool
    reason: str


@dataclass(frozen=True)
class ShadowDiagnostics:
    """Compact diagnostic snapshot suitable for later HA sensor exposure."""

    generation: int
    observed_events: int
    homeowner_events: int
    ignored_or_hlm_events: int
    managed_entities: int
    suppressed_sessions: int


class ShadowRuntime:
    """In-memory, non-commanding runtime around the pure ownership engine."""

    def __init__(self, generation: int = 1) -> None:
        self.engine = OwnershipEngine(generation=generation)
        self._observed_events = 0
        self._homeowner_events = 0
        self._ignored_or_hlm_events = 0
        self._suppressed_sessions: dict[tuple[str, str], FamilySession] = {}
        self._known_entities: set[str] = set()

    def observe(self, observation: ShadowObservation) -> ShadowDecision:
        """Classify and shadow-apply a high-confidence homeowner observation.

        This method never commands a device. OFF delegates to the ownership state machine; an
        appearance command creates/replaces a Manual appearance only when intent classification
        explicitly permits homeowner mutation.
        """
        self._observed_events += 1
        decision = classify_intent(observation.evidence)
        if decision.disposition is IntentDisposition.HOMEOWNER_INTENT:
            self._homeowner_events += 1
        else:
            self._ignored_or_hlm_events += 1

        if not decision.allows_homeowner_mutation:
            return ShadowDecision(
                entity_id=observation.entity_id,
                intent=decision,
                mutated=False,
                reason="observation is not permitted to mutate homeowner ownership",
            )

        if observation.operation == "off":
            self._known_entities.add(observation.entity_id)
            result = self.engine.apply_off(observation.entity_id)
            previous = result.previous_layer
            if (
                result.action is OffAction.SUPPRESSED_FAMILY
                and previous is not None
                and previous.family is not None
                and previous.session_id is not None
            ):
                session = self.engine.start_family(previous.family, previous.session_id)
                self._suppressed_sessions[(previous.family, previous.session_id)] = session
            return ShadowDecision(
                entity_id=observation.entity_id,
                intent=decision,
                mutated=True,
                reason=result.action.value,
            )

        if observation.operation != "appearance" or observation.appearance is None:
            return ShadowDecision(
                entity_id=observation.entity_id,
                intent=decision,
                mutated=False,
                reason="homeowner evidence lacked a supported shadow operation",
            )

        if observation.manual_precedence is None:
            return ShadowDecision(
                entity_id=observation.entity_id,
                intent=decision,
                mutated=False,
                reason="Manual precedence policy is required for appearance ownership",
            )

        self._known_entities.add(observation.entity_id)
        current = self.engine.resolve(observation.entity_id).layer
        if current is not None and current.family is not None and current.session_id is not None:
            session = self.engine.suppress_family(
                current.family, current.session_id, "homeowner_override"
            )
            self._suppressed_sessions[(current.family, current.session_id)] = session

        self.engine.remove_homeowner_exceptions(observation.entity_id)
        self.engine.push(
            observation.entity_id,
            OwnershipLayer(
                layer_id=f"shadow-manual:{self.engine.generation}:{observation.entity_id}",
                owner="manual",
                kind=LayerKind.MANUAL,
                generation=self.engine.generation,
                order=0,
                appearance=observation.appearance,
                expires_at_boundary=NIGHTLY_BOUNDARY,
                precedence=observation.manual_precedence,
                metadata={"shadow": True},
            ),
        )
        return ShadowDecision(
            entity_id=observation.entity_id,
            intent=decision,
            mutated=True,
            reason="shadow Manual appearance recorded",
        )

    def export_persistence(self) -> dict:
        """Serialize only contractually durable evidence."""
        layers_by_entity = {
            entity_id: self.engine.layers(entity_id)
            for entity_id in self.managed_entities()
        }
        return serialize_state(
            layers_by_entity,
            list(self._suppressed_sessions.values()),
        )

    def restore(
        self,
        persisted: PersistedOwnershipState,
        *,
        manual_evidence: dict[str, ManualRecoveryEvidence],
        family_evidence: dict[tuple[str, str], FamilyRecoveryEvidence],
    ) -> None:
        """Recover trusted evidence into the current generation without replaying commands."""
        for layer in persisted.layers:
            entity_id = layer.metadata.get("persisted_entity_id")
            if not isinstance(entity_id, str):
                continue
            result = recover_layer(
                layer,
                new_generation=self.engine.generation,
                evidence=manual_evidence.get(entity_id),
            )
            if result.action is RecoveryAction.RESTORE and result.layer is not None:
                self.engine.push(entity_id, result.layer)
                self._known_entities.add(entity_id)

        for session in persisted.suppressed_sessions:
            key = (session.family, session.session_id)
            evidence = family_evidence.get(key)
            if evidence is None:
                continue
            result = recover_family_session(
                session,
                new_generation=self.engine.generation,
                evidence=evidence,
            )
            if result.action is RecoveryAction.RESTORE and result.session is not None:
                restored = self.engine.start_family(
                    result.session.family, result.session.session_id
                )
                restored = self.engine.suppress_family(
                    restored.family,
                    restored.session_id,
                    result.session.suppression_reason or "recovered_suppression",
                )
                self._suppressed_sessions[key] = restored

    def register_suppression(self, family: str, session_id: str, reason: str) -> None:
        """Record family suppression produced by shadow ownership evaluation."""
        self.engine.start_family(family, session_id)
        session = self.engine.suppress_family(family, session_id, reason)
        self._suppressed_sessions[(family, session_id)] = session

    def managed_entities(self) -> tuple[str, ...]:
        """Return entities with any current-generation shadow ownership state."""
        return tuple(
            sorted(
                entity_id
                for entity_id in self._known_entities
                if self.engine.layers(entity_id)
            )
        )

    def diagnostics(self) -> ShadowDiagnostics:
        """Return a compact non-sensitive shadow-runtime summary."""
        return ShadowDiagnostics(
            generation=self.engine.generation,
            observed_events=self._observed_events,
            homeowner_events=self._homeowner_events,
            ignored_or_hlm_events=self._ignored_or_hlm_events,
            managed_entities=len(self.managed_entities()),
            suppressed_sessions=len(self._suppressed_sessions),
        )
