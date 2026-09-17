"""Pure persistence/restart-recovery policy for Home Lighting Manager.

Persisted state is evidence, not authority. Automatic ownership is rebuilt from current truth.
Only trustworthy, still-valid homeowner state and explicitly validated family suppression are
eligible to survive restart. No missed command is ever replayed by this module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from .engine import NIGHTLY_BOUNDARY
from .model import FamilySession, LayerKind, OwnershipLayer


class RecoveryAction(StrEnum):
    """Action taken for a persisted ownership record during reconstruction."""

    RESTORE = "restore"
    DROP_RECOMPUTE = "drop_recompute"


@dataclass(frozen=True)
class ManualRecoveryEvidence:
    """Evidence required to preserve persisted Manual or Manual-OFF state."""

    temporally_valid: bool
    desired_state_trustworthy: bool
    ownership_evidence_coherent: bool


@dataclass(frozen=True)
class LayerRecoveryResult:
    """Recovery result for one persisted ownership layer."""

    action: RecoveryAction
    layer: OwnershipLayer | None
    replay_commands: bool
    reason: str


@dataclass(frozen=True)
class FamilyRecoveryEvidence:
    """Evidence required to preserve one family session's suppression state."""

    same_session_active: bool
    session_evidence_coherent: bool


@dataclass(frozen=True)
class FamilyRecoveryResult:
    """Recovery result for one persisted family session."""

    action: RecoveryAction
    session: FamilySession | None
    replay_commands: bool
    reason: str


def recover_layer(
    layer: OwnershipLayer,
    *,
    new_generation: int,
    evidence: ManualRecoveryEvidence | None = None,
) -> LayerRecoveryResult:
    """Decide whether one persisted layer may survive restart/reload.

    Automatic, functional, and overlay layers are reconstructed from current inputs rather than
    restored from persistence. Manual state survives only when all contractual trust checks pass.
    """
    if layer.kind not in (LayerKind.MANUAL, LayerKind.MANUAL_OFF):
        return LayerRecoveryResult(
            action=RecoveryAction.DROP_RECOMPUTE,
            layer=None,
            replay_commands=False,
            reason="non-manual ownership is recomputed from current truth",
        )

    if (
        layer.appearance is None
        or not layer.appearance.is_valid()
        or layer.appearance.on is not (layer.kind is LayerKind.MANUAL)
    ):
        return LayerRecoveryResult(
            action=RecoveryAction.DROP_RECOMPUTE,
            layer=None,
            replay_commands=False,
            reason="missing or invalid desired Manual state defaults to HLM",
        )

    if evidence is None:
        return LayerRecoveryResult(
            action=RecoveryAction.DROP_RECOMPUTE,
            layer=None,
            replay_commands=False,
            reason="missing Manual recovery evidence defaults to HLM",
        )

    if not evidence.temporally_valid:
        return LayerRecoveryResult(
            action=RecoveryAction.DROP_RECOMPUTE,
            layer=None,
            replay_commands=False,
            reason="Manual lifecycle expired while offline",
        )
    if not evidence.desired_state_trustworthy:
        return LayerRecoveryResult(
            action=RecoveryAction.DROP_RECOMPUTE,
            layer=None,
            replay_commands=False,
            reason="persisted desired state is not trustworthy",
        )
    if not evidence.ownership_evidence_coherent:
        return LayerRecoveryResult(
            action=RecoveryAction.DROP_RECOMPUTE,
            layer=None,
            replay_commands=False,
            reason="ownership evidence is ambiguous",
        )

    restored = replace(
        layer,
        generation=new_generation,
        order=0,
        expires_at_boundary=NIGHTLY_BOUNDARY,
    )
    return LayerRecoveryResult(
        action=RecoveryAction.RESTORE,
        layer=restored,
        replay_commands=False,
        reason="Manual ownership remains coherent and valid",
    )


def recover_family_session(
    session: FamilySession,
    *,
    new_generation: int,
    evidence: FamilyRecoveryEvidence,
) -> FamilyRecoveryResult:
    """Preserve session suppression only when the exact session is independently still valid."""
    if not session.suppressed:
        return FamilyRecoveryResult(
            action=RecoveryAction.DROP_RECOMPUTE,
            session=None,
            replay_commands=False,
            reason="unsuppressed family activity is recomputed from current truth",
        )

    if not evidence.same_session_active or not evidence.session_evidence_coherent:
        return FamilyRecoveryResult(
            action=RecoveryAction.DROP_RECOMPUTE,
            session=None,
            replay_commands=False,
            reason="family suppression cannot be trusted across restart",
        )

    restored = replace(session, generation=new_generation)
    return FamilyRecoveryResult(
        action=RecoveryAction.RESTORE,
        session=restored,
        replay_commands=False,
        reason="same coherent family session remains active",
    )
