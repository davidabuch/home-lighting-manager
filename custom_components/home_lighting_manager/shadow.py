"""Non-commanding shadow runtime for Home Lighting Manager.

The shadow runtime owns no lights. It accepts already-classified evidence, maintains an in-memory
ownership model, and exports persistence evidence/diagnostics. It deliberately has no Home
Assistant service-call dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

from .engine import OwnershipEngine
from .intent_policy import IntentDecision, IntentDisposition, IntentEvidence
from .model import Appearance, LayerKind
from .operations import HomeownerOperation, MemberOutcome, OperationResult, OwnershipOperations
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
    sequence: int | None = None
    generation: int | None = None
    operation_id: str | None = None


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

    def __init__(self, generation: int = 1, managed_entities: frozenset[str] | None = None) -> None:
        self.engine = OwnershipEngine(generation=generation, managed_entities=managed_entities)
        self._observed_events = 0
        self._homeowner_events = 0
        self._ignored_or_hlm_events = 0
        self.operations = OwnershipOperations(self.engine)
        self._sequence = 0
        self._recovery_open = True
        self._qualified_ids: list[str] = []
        self._recovery_summary = {
            "status": "not_attempted",
            "group_off_history": "empty_new_runtime",
            "restored_manual": 0,
            "restored_manual_off": 0,
            "rejected_manual": 0,
            "restored_suppressed_sessions": 0,
            "rejected_sessions": 0,
            "payload_rejected": False,
        }

    def reserve_sequence(self) -> int:
        """Reserve ingress order before optional delayed external qualification."""
        self._sequence += 1
        return self._sequence

    def observe(self, observation: ShadowObservation) -> ShadowDecision:
        sequence = observation.sequence
        if sequence is None:
            sequence = self.reserve_sequence()
        generation = observation.generation
        if generation is None:
            generation = self.engine.generation
        result = self.observe_operation(
            HomeownerOperation(
                operation_id=observation.operation_id or f"observation:{generation}:{sequence}",
                sequence=sequence,
                generation=generation,
                kind=observation.operation,
                evidence=observation.evidence,
                require_legacy_policy=True,
                members=(
                    MemberOutcome(
                        observation.entity_id,
                        appearance=observation.appearance,
                        manual_precedence=observation.manual_precedence,
                    ),
                ),
            )
        )
        return ShadowDecision(observation.entity_id, result.intent, result.mutated, result.reason)

    def observe_operation(self, operation: HomeownerOperation) -> OperationResult:
        """Core group entry point; adapters must supply explicit scope and member outcomes."""
        if type(operation.sequence) is int:
            self._sequence = max(self._sequence, operation.sequence)
        self._observed_events += 1
        result = self.operations.apply(operation)
        if result.intent.disposition is IntentDisposition.HOMEOWNER_INTENT:
            if operation.operation_id not in self._qualified_ids and not result.reason.startswith(
                ("stale", "duplicate")
            ):
                self._homeowner_events += 1
                self._qualified_ids.append(operation.operation_id)
                self._qualified_ids = self._qualified_ids[-128:]
        else:
            self._ignored_or_hlm_events += 1
        if result.mutated:
            self._recovery_open = False
        return result

    def ownership_diagnostics(self) -> dict:
        return {
            **self.operations.diagnostics(self.engine.entity_ids()),
            "startup_recovery": dict(self._recovery_summary),
        }

    def export_persistence(self) -> dict:
        """Serialize only contractually durable evidence."""
        layers_by_entity = {
            entity_id: self.engine.layers(entity_id) for entity_id in self.managed_entities()
        }
        return serialize_state(
            layers_by_entity,
            list(self.engine.family_sessions()),
        )

    def restore(
        self,
        persisted: PersistedOwnershipState,
        *,
        manual_evidence: dict[str, ManualRecoveryEvidence],
        family_evidence: dict[tuple[str, str], FamilyRecoveryEvidence],
    ) -> None:
        """Recover trusted evidence into the current generation without replaying commands."""
        if not self._recovery_open or self.engine.revision:
            return
        self._recovery_open = False
        self._recovery_summary.update(
            status="evaluated",
            group_off_history="cleared_on_recovery",
            rejected_manual=persisted.rejected_layers,
            rejected_sessions=persisted.rejected_sessions,
            payload_rejected=persisted.payload_rejected,
        )
        # No receipt, provenance, diagnostic or physical state can reconstruct group arming.
        self.engine.reset_group_off_sequence()
        for layer in persisted.layers:
            entity_id = layer.metadata.get("persisted_entity_id")
            if not self.engine.accepts_entity(entity_id):
                self._recovery_summary["rejected_manual"] += 1
                continue
            result = recover_layer(
                layer,
                new_generation=self.engine.generation,
                evidence=manual_evidence.get(entity_id),
            )
            if result.action is RecoveryAction.RESTORE and result.layer is not None:
                self.engine.push(entity_id, result.layer)
                key = "restored_manual_off" if result.layer.kind is LayerKind.MANUAL_OFF else "restored_manual"
                self._recovery_summary[key] += 1
            else:
                self._recovery_summary["rejected_manual"] += 1

        for session in persisted.suppressed_sessions:
            key = (session.family, session.session_id)
            evidence = family_evidence.get(key)
            if evidence is None:
                self._recovery_summary["rejected_sessions"] += 1
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
                self._recovery_summary["restored_suppressed_sessions"] += 1
            else:
                self._recovery_summary["rejected_sessions"] += 1

    def register_suppression(self, family: str, session_id: str, reason: str) -> None:
        """Record family suppression produced by shadow ownership evaluation."""
        self.engine.suppress_family(family, session_id, reason)

    def managed_entities(self) -> tuple[str, ...]:
        """Return entities with any current-generation shadow ownership state."""
        return self.engine.entity_ids()

    def diagnostics(self) -> ShadowDiagnostics:
        """Return a compact non-sensitive shadow-runtime summary."""
        return ShadowDiagnostics(
            generation=self.engine.generation,
            observed_events=self._observed_events,
            homeowner_events=self._homeowner_events,
            ignored_or_hlm_events=self._ignored_or_hlm_events,
            managed_entities=len(self.managed_entities()),
            suppressed_sessions=sum(item.suppressed for item in self.engine.family_sessions()),
        )
