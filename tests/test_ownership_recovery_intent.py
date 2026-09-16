from custom_components.home_lighting_manager.intent_policy import (
    IntentDisposition,
    IntentEvidence,
    IntentEvidenceKind,
    classify_intent,
)
from custom_components.home_lighting_manager.model import (
    Appearance,
    FamilySession,
    LayerKind,
    OwnershipLayer,
)
from custom_components.home_lighting_manager.recovery import (
    FamilyRecoveryEvidence,
    ManualRecoveryEvidence,
    RecoveryAction,
    recover_family_session,
    recover_layer,
)


def manual_layer(kind=LayerKind.MANUAL, generation=4):
    return OwnershipLayer(
        layer_id="manual:kitchen",
        owner="manual",
        kind=kind,
        generation=generation,
        order=22,
        appearance=Appearance(on=kind is not LayerKind.MANUAL_OFF, brightness=88),
        expires_at_boundary="nightly_0159",
        precedence=200,
    )


def test_successful_explicit_homeowner_command_allows_homeowner_mutation():
    decision = classify_intent(
        IntentEvidence(IntentEvidenceKind.EXPLICIT_HOMEOWNER_COMMAND, succeeded=True)
    )

    assert decision.disposition is IntentDisposition.HOMEOWNER_INTENT
    assert decision.allows_homeowner_mutation is True




def test_intent_classifier_does_not_predecide_manual_creation_for_off_semantics():
    decision = classify_intent(
        IntentEvidence(IntentEvidenceKind.EXPLICIT_HOMEOWNER_COMMAND, succeeded=True)
    )

    # The classifier only authorizes homeowner mutation. The ownership engine decides whether an
    # OFF releases Manual, suppresses a family, or creates Manual-OFF.
    assert decision.disposition is IntentDisposition.HOMEOWNER_INTENT
    assert decision.allows_homeowner_mutation is True

def test_failed_homeowner_command_creates_no_ownership_change():
    decision = classify_intent(
        IntentEvidence(IntentEvidenceKind.EXPLICIT_HOMEOWNER_COMMAND, succeeded=False)
    )

    assert decision.disposition is IntentDisposition.NO_OWNERSHIP_CHANGE
    assert decision.allows_homeowner_mutation is False


def test_ambiguous_homeowner_attribution_defaults_to_hlm():
    decision = classify_intent(
        IntentEvidence(
            IntentEvidenceKind.EXPLICIT_HOMEOWNER_COMMAND,
            succeeded=True,
            attribution_coherent=False,
        )
    )

    assert decision.disposition is IntentDisposition.HLM_OWNED
    assert decision.allows_homeowner_mutation is False


def test_hlm_command_consequence_never_creates_manual():
    decision = classify_intent(IntentEvidence(IntentEvidenceKind.HLM_COMMAND_CONSEQUENCE))

    assert decision.disposition is IntentDisposition.HLM_OWNED
    assert decision.allows_homeowner_mutation is False


def test_recovery_telemetry_never_creates_manual():
    decision = classify_intent(IntentEvidence(IntentEvidenceKind.RECOVERY_TELEMETRY))

    assert decision.disposition is IntentDisposition.HLM_OWNED
    assert decision.allows_homeowner_mutation is False


def test_unavailable_transition_never_creates_manual():
    decision = classify_intent(IntentEvidence(IntentEvidenceKind.AVAILABILITY_CHANGE))

    assert decision.disposition is IntentDisposition.HLM_OWNED
    assert decision.allows_homeowner_mutation is False


def test_physical_drift_alone_never_creates_manual():
    decision = classify_intent(IntentEvidence(IntentEvidenceKind.PHYSICAL_DRIFT))

    assert decision.disposition is IntentDisposition.HLM_OWNED
    assert decision.allows_homeowner_mutation is False


def test_trustworthy_manual_state_survives_restart_in_new_generation():
    original = manual_layer()
    result = recover_layer(
        original,
        new_generation=5,
        evidence=ManualRecoveryEvidence(
            temporally_valid=True,
            desired_state_trustworthy=True,
            ownership_evidence_coherent=True,
        ),
    )

    assert result.action is RecoveryAction.RESTORE
    assert result.layer is not None
    assert result.layer.generation == 5
    assert result.layer.order == 0
    assert result.layer.appearance == original.appearance
    assert result.replay_commands is False


def test_manual_off_can_survive_restart_when_still_coherent_and_valid():
    result = recover_layer(
        manual_layer(LayerKind.MANUAL_OFF),
        new_generation=9,
        evidence=ManualRecoveryEvidence(True, True, True),
    )

    assert result.action is RecoveryAction.RESTORE
    assert result.layer is not None
    assert result.layer.kind is LayerKind.MANUAL_OFF
    assert result.layer.generation == 9


def test_manual_state_expires_when_offline_crosses_its_boundary():
    result = recover_layer(
        manual_layer(),
        new_generation=5,
        evidence=ManualRecoveryEvidence(
            temporally_valid=False,
            desired_state_trustworthy=True,
            ownership_evidence_coherent=True,
        ),
    )

    assert result.action is RecoveryAction.DROP_RECOMPUTE
    assert result.layer is None
    assert result.replay_commands is False


def test_ambiguous_manual_reconstruction_defaults_to_hlm():
    result = recover_layer(
        manual_layer(),
        new_generation=5,
        evidence=ManualRecoveryEvidence(
            temporally_valid=True,
            desired_state_trustworthy=False,
            ownership_evidence_coherent=False,
        ),
    )

    assert result.action is RecoveryAction.DROP_RECOMPUTE
    assert result.layer is None


def test_automatic_layer_is_never_restored_from_persistence():
    automatic = OwnershipLayer(
        layer_id="holiday",
        owner="holiday",
        kind=LayerKind.AUTOMATIC,
        generation=4,
        order=10,
        appearance=Appearance(on=True, scene_id="halloween"),
        precedence=100,
    )

    result = recover_layer(automatic, new_generation=5)

    assert result.action is RecoveryAction.DROP_RECOMPUTE
    assert result.layer is None
    assert result.replay_commands is False


def test_suppressed_family_session_survives_only_same_confirmed_session():
    session = FamilySession(
        family="49ers",
        session_id="game-1",
        generation=4,
        suppressed=True,
        suppression_reason="homeowner_override",
    )
    result = recover_family_session(
        session,
        new_generation=5,
        evidence=FamilyRecoveryEvidence(
            same_session_active=True,
            session_evidence_coherent=True,
        ),
    )

    assert result.action is RecoveryAction.RESTORE
    assert result.session is not None
    assert result.session.generation == 5
    assert result.session.suppressed is True
    assert result.replay_commands is False


def test_family_suppression_does_not_cross_into_new_or_unknown_session():
    session = FamilySession(
        family="spa_gauge",
        session_id="spa-1",
        generation=4,
        suppressed=True,
        suppression_reason="homeowner_off",
    )
    result = recover_family_session(
        session,
        new_generation=5,
        evidence=FamilyRecoveryEvidence(
            same_session_active=False,
            session_evidence_coherent=True,
        ),
    )

    assert result.action is RecoveryAction.DROP_RECOMPUTE
    assert result.session is None
    assert result.replay_commands is False


def test_unsuppressed_family_session_is_recomputed_not_restored():
    session = FamilySession(
        family="49ers",
        session_id="game-1",
        generation=4,
        suppressed=False,
    )
    result = recover_family_session(
        session,
        new_generation=5,
        evidence=FamilyRecoveryEvidence(
            same_session_active=True,
            session_evidence_coherent=True,
        ),
    )

    assert result.action is RecoveryAction.DROP_RECOMPUTE
    assert result.session is None
    assert result.replay_commands is False
