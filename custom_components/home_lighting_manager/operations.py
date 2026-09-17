"""Pure successful-operation boundary; attribution stays in the commissioned classifier.

An explicit group operation is one record with exact requested members, never inferred by expanding
aggregate telemetry. Sequence numbers are assigned at observation ingress, before correlation.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass, replace

from .engine import MAX_ENTITIES, NIGHTLY_BOUNDARY, OwnershipEngine
from .intent_policy import IntentDecision, IntentEvidence, classify_intent
from .model import Appearance, LayerKind, OwnershipLayer, WorkToken

HISTORY_LIMIT = 12


@dataclass(frozen=True)
class MemberOutcome:
    """External receipt evidence, not HLM dispatch/delivery/verification status.

    succeeded means the source command was observed to occur successfully. available is observation
    quality at that receipt. Future HLM delivery results must use a different type.
    manual_precedence is a legacy shadow adapter hint, never required by explicit core operations.
    """

    entity_id: str
    available: bool = True
    succeeded: bool = True
    appearance: Appearance | None = None
    manual_precedence: int | None = None


@dataclass(frozen=True)
class HomeownerOperation:
    operation_id: str
    sequence: int
    generation: int
    kind: str
    evidence: IntentEvidence
    members: tuple[MemberOutcome, ...]
    group_id: str | None = None
    expected: WorkToken | None = None
    require_legacy_policy: bool = False


@dataclass(frozen=True)
class OperationResult:
    operation_id: str
    intent: IntentDecision
    mutated: bool
    reason: str
    affected: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()


class OwnershipOperations:
    """Deterministic, synchronous transactions over the layer engine (no device commands)."""

    def __init__(self, engine: OwnershipEngine) -> None:
        self.engine = engine
        self._generation = engine.generation
        self._latest_sequence: dict[str, int] = {}
        self._seen: deque[tuple[str, int]] = deque(maxlen=128)
        self._retired_sequence = 0
        self.history: deque[dict] = deque(maxlen=HISTORY_LIMIT)
        self.latest_homeowner: dict | None = None
        self.latest_rejection: dict | None = None

    def apply(self, operation: HomeownerOperation) -> OperationResult:
        """Commit an ownership transaction atomically, including capacity/admission failures."""
        checkpoint = deepcopy(self.engine.__dict__)
        try:
            return self._apply(operation)
        except (ValueError, TypeError) as error:
            self.engine.__dict__.clear()
            self.engine.__dict__.update(checkpoint)
            reason = f"operation rejected atomically: {error}"
            self.latest_rejection = {
                "operation_id": str(operation.operation_id)[:256],
                "reason": reason[:256],
            }
            return OperationResult(
                operation.operation_id, classify_intent(operation.evidence), False, reason
            )

    def _apply(self, operation: HomeownerOperation) -> OperationResult:
        if self._generation != self.engine.generation:
            self._latest_sequence.clear()
            self._seen.clear()
            self._retired_sequence = 0
            self.history.clear()
            self.latest_homeowner = None
            self._generation = self.engine.generation
        intent = classify_intent(operation.evidence)

        def reject(reason: str) -> OperationResult:
            self.latest_rejection = {
                "operation_id": str(operation.operation_id)[:256],
                "reason": reason,
            }
            return OperationResult(operation.operation_id, intent, False, reason)

        if not intent.allows_homeowner_mutation:
            return reject(intent.reason)
        if type(operation.generation) is not int or operation.generation != self.engine.generation:
            return reject("stale configuration generation")
        if (
            not isinstance(operation.operation_id, str)
            or not operation.operation_id
            or len(operation.operation_id) > 256
            or type(operation.sequence) is not int
            or operation.sequence < 1
        ):
            return reject("invalid operation identity or sequence")
        if any(key == operation.operation_id for key, _ in self._seen):
            return reject("duplicate operation")
        if operation.sequence <= self._retired_sequence:
            return reject("stale receipt outside bounded deduplication window")
        if operation.expected is not None and not self.engine.is_current_work(operation.expected):
            return reject("stale runtime work token")
        ids = tuple(member.entity_id for member in operation.members)
        if (
            not ids
            or len(ids) > MAX_ENTITIES
            or any(not self.engine.accepts_entity(item) for item in ids)
            or len(set(ids)) != len(ids)
            or (operation.group_id is None and len(ids) != 1)
            or (
                operation.group_id is not None
                and (
                    not isinstance(operation.group_id, str)
                    or not 0 < len(operation.group_id) <= 256
                )
            )
        ):
            return reject("explicit exact operation scope is required")
        if operation.kind not in ("appearance", "off"):
            return reject("unsupported operation")
        # Consume the operation once even when some/all members failed. Recovery never replays it.
        if len(self._latest_sequence.keys() | set(ids)) > MAX_ENTITIES:
            return reject("operation entity capacity exceeded")
        if len(self._seen) == self._seen.maxlen:
            self._retired_sequence = max(self._retired_sequence, self._seen[0][1])
        self._seen.append((operation.operation_id, operation.sequence))
        eligible = tuple(
            m
            for m in sorted(operation.members, key=lambda item: item.entity_id)
            if m.available is True and m.succeeded is True
        )
        skipped = tuple(sorted(m.entity_id for m in operation.members if m not in eligible))
        if not eligible:
            return reject("no successful available members; no replay")
        if any(operation.sequence <= self._latest_sequence.get(m.entity_id, 0) for m in eligible):
            return reject("stale successful intent; newer operation already recorded")
        if operation.kind == "appearance":
            if any(
                m.appearance is None or not m.appearance.is_valid() or m.appearance.on is not True
                for m in eligible
            ):
                return reject("Manual appearance is incomplete or invalid")
            if any(
                (m.manual_precedence is not None and type(m.manual_precedence) is not int)
                or (operation.require_legacy_policy and m.manual_precedence is None)
                for m in eligible
            ):
                return reject("Manual precedence policy is required for appearance ownership")

        affected = tuple(m.entity_id for m in eligible)
        # Decide every member against one pre-operation snapshot. Suppression of a shared
        # family on the first member must not expose and dismiss a different underlying family
        # while processing later members of this same group operation.
        exposed = {
            member.entity_id: self.engine.resolve(member.entity_id).layer for member in eligible
        }
        if operation.group_id is not None and operation.kind == "off":
            # Requested membership (including unavailable members) determines phase identity.
            # The engine receives availability separately and never applies to skipped members.
            result = self.engine.apply_group_off(
                operation.group_id, ids, eligible_members=frozenset(affected)
            )
            reason = result.action.value
            for entity_id in affected:
                self._tag_exposed_manual(entity_id, operation)
        else:
            self.engine.reset_group_off_for_members(frozenset(affected))
            for member in eligible:
                if operation.kind == "off":
                    result = self.engine.apply_off(member.entity_id)
                    reason = result.action.value
                    self._tag_exposed_manual(member.entity_id, operation)
                else:
                    current = exposed[member.entity_id]
                    precedence = member.manual_precedence or 0
                    if current and current.family and current.session_id:
                        precedence = max(precedence, current.precedence)
                        self.engine.suppress_family(
                            current.family, current.session_id, "homeowner_override"
                        )
                    self.engine.remove_homeowner_exceptions(member.entity_id)
                    # Explicit model uses stack placement; numeric policy stays at legacy ingress.
                    explicit = member.manual_precedence is None or any(
                        item.system_priority is not None
                        for item in self.engine.layers(member.entity_id)
                    )
                    admit = self.engine.admit_manual if explicit else self.engine.push
                    admit(
                        member.entity_id,
                        OwnershipLayer(
                            layer_id=f"manual:{operation.operation_id}:{member.entity_id}",
                            owner="manual",
                            kind=LayerKind.MANUAL,
                            generation=self.engine.generation,
                            order=0,
                            appearance=member.appearance,
                            precedence=precedence,
                            expires_at_boundary=NIGHTLY_BOUNDARY,
                            operation_id=operation.operation_id,
                            group_id=operation.group_id,
                            metadata={"shadow": True},
                        ),
                    )
                    reason = "shadow Manual appearance recorded"
        # Also consume skipped members' positions: an old group receipt must never replay
        # against a recovered light after the bounded duplicate-ID cache rotates.
        for entity_id in ids:
            self._latest_sequence[entity_id] = max(
                operation.sequence, self._latest_sequence.get(entity_id, 0)
            )
        self.engine.invalidate_work(reason)
        record = {
            "operation_id": operation.operation_id,
            "sequence": operation.sequence,
            "kind": operation.kind,
            "group_id": operation.group_id,
            "generation": self.engine.generation,
            "revision": self.engine.revision,
            "requested": tuple(sorted(ids)),
            "affected": affected,
            "skipped": skipped,
            "reason": reason,
        }
        self.history.append(record)
        self.latest_homeowner = record
        return OperationResult(operation.operation_id, intent, True, reason, affected, skipped)

    def _tag_exposed_manual(self, entity_id: str, operation: HomeownerOperation) -> None:
        layer = self.engine.resolve(entity_id).layer
        if layer is not None and layer.kind is LayerKind.MANUAL_OFF:
            self.engine.push(
                entity_id,
                replace(layer, operation_id=operation.operation_id, group_id=operation.group_id),
            )

    def diagnostics(self, entity_ids: tuple[str, ...]) -> dict:
        """Hard limits on entities, layers, sessions, history, and group member lists."""
        entities = {}
        for entity_id in sorted(entity_ids)[:32]:
            layers = list(reversed(self.engine.layers(entity_id)))
            exposed = self.engine.resolve(entity_id).layer
            entities[entity_id] = {
                "exposed_layer": exposed.layer_id if exposed else None,
                "manual_appearance": bool(exposed and exposed.kind is LayerKind.MANUAL),
                "manual_off": bool(exposed and exposed.kind is LayerKind.MANUAL_OFF),
                "layers": [
                    {
                        **{key: value for key, value in asdict(layer).items() if key != "metadata"},
                        "eligible": self.engine.layer_eligible(entity_id, layer),
                    }
                    for layer in layers[:8]
                ],
                "layer_count": len(layers),
            }

        def bounded(record):
            if record is None:
                return None
            return {
                key: value[:32] if isinstance(value, tuple) else value
                for key, value in record.items()
            }

        return {
            "runtime_revision": self.engine.revision,
            "ownership_entities": entities,
            "ownership_entities_truncated": len(entity_ids) > 32,
            "family_sessions": [
                {**asdict(item), "eligible": self.engine.family_eligible(item.family, item.session_id)}
                for item in self.engine.family_sessions()[:16]
            ],
            "ended_family_sessions": [
                {"family": family, "session_id": session}
                for family, session in self.engine.ended_family_sessions()
            ],
            "group_off_sequences": {
                key: members[:32]
                for key, members in list(self.engine.group_off_sequences().items())[:16]
            },
            "last_mutation_reason": self.engine.last_mutation_reason,
            "latest_homeowner_operation": bounded(self.latest_homeowner),
            "latest_operation_rejection": self.latest_rejection,
            "recent_operations": [bounded(record) for record in self.history],
        }
