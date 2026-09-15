"""Pure intent-classification policy for Home Lighting Manager.

Adapters provide already-observed evidence. This module decides whether that evidence is allowed to
mutate homeowner ownership. It has no Home Assistant dependencies and performs no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IntentEvidenceKind(StrEnum):
    """High-level evidence classes produced by future runtime adapters."""

    EXPLICIT_HOMEOWNER_COMMAND = "explicit_homeowner_command"
    HLM_COMMAND_CONSEQUENCE = "hlm_command_consequence"
    RECOVERY_TELEMETRY = "recovery_telemetry"
    AVAILABILITY_CHANGE = "availability_change"
    PHYSICAL_DRIFT = "physical_drift"
    UNKNOWN = "unknown"


class IntentDisposition(StrEnum):
    """Ownership consequence permitted by classified evidence."""

    HOMEOWNER_INTENT = "homeowner_intent"
    HLM_OWNED = "hlm_owned"
    NO_OWNERSHIP_CHANGE = "no_ownership_change"


@dataclass(frozen=True)
class IntentEvidence:
    """Minimal evidence required by the pure classifier."""

    kind: IntentEvidenceKind
    succeeded: bool = True
    attribution_coherent: bool = True


@dataclass(frozen=True)
class IntentDecision:
    """Result of classifying one observed event or command outcome."""

    disposition: IntentDisposition
    allows_homeowner_mutation: bool
    reason: str


def classify_intent(evidence: IntentEvidence) -> IntentDecision:
    """Classify evidence under the contract's homeowner-intent/ambiguity rules."""
    if evidence.kind is IntentEvidenceKind.EXPLICIT_HOMEOWNER_COMMAND:
        if not evidence.succeeded:
            return IntentDecision(
                disposition=IntentDisposition.NO_OWNERSHIP_CHANGE,
                allows_homeowner_mutation=False,
                reason="failed homeowner command cannot create ownership",
            )
        if evidence.attribution_coherent:
            return IntentDecision(
                disposition=IntentDisposition.HOMEOWNER_INTENT,
                allows_homeowner_mutation=True,
                reason="successful explicit homeowner command",
            )
        return IntentDecision(
            disposition=IntentDisposition.HLM_OWNED,
            allows_homeowner_mutation=False,
            reason="ambiguous homeowner attribution defaults to HLM",
        )

    if evidence.kind is IntentEvidenceKind.HLM_COMMAND_CONSEQUENCE:
        return IntentDecision(
            disposition=IntentDisposition.HLM_OWNED,
            allows_homeowner_mutation=False,
            reason="telemetry attributed to HLM command",
        )

    if evidence.kind in (
        IntentEvidenceKind.RECOVERY_TELEMETRY,
        IntentEvidenceKind.AVAILABILITY_CHANGE,
        IntentEvidenceKind.PHYSICAL_DRIFT,
        IntentEvidenceKind.UNKNOWN,
    ):
        return IntentDecision(
            disposition=IntentDisposition.HLM_OWNED,
            allows_homeowner_mutation=False,
            reason="non-homeowner or ambiguous evidence defaults to HLM",
        )

    raise ValueError(f"unsupported intent evidence kind: {evidence.kind}")
