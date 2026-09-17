"""Phase 1 executable contract: explicit operations, lifecycles, and stale work."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from custom_components.home_lighting_manager.engine import NIGHTLY_BOUNDARY, OwnershipEngine
from custom_components.home_lighting_manager.intent_policy import IntentEvidence, IntentEvidenceKind
from custom_components.home_lighting_manager.model import Appearance, LayerKind, OwnershipLayer
from custom_components.home_lighting_manager.operations import HomeownerOperation, MemberOutcome
from custom_components.home_lighting_manager.persistence import deserialize_state, serialize_state
from custom_components.home_lighting_manager.recovery import ManualRecoveryEvidence
from custom_components.home_lighting_manager.shadow import ShadowRuntime

A, B, C = "light.a", "light.b", "light.c"
GOOD = IntentEvidence(IntentEvidenceKind.EXPLICIT_HOMEOWNER_COMMAND)
PURPLE = Appearance(on=True, brightness=110, xy_color=(0.3, 0.15), color_mode="xy")


def layer(name="daily", *, kind=LayerKind.AUTOMATIC, precedence=100, **kwargs):
    return OwnershipLayer(name, name, kind, 1, 0, precedence=precedence, **kwargs)


def operation(n, *, members=(A,), kind="appearance", group=None, appearance=PURPLE, **kwargs):
    return HomeownerOperation(
        f"op-{n}",
        n,
        1,
        kind,
        GOOD,
        tuple(MemberOutcome(e, appearance=appearance, manual_precedence=200) for e in members),
        group_id=group,
        **kwargs,
    )


def setup():
    runtime = ShadowRuntime()
    for entity in (A, B, C):
        runtime.engine.push(entity, layer(appearance=Appearance(on=True, brightness=50)))
    return runtime


def test_manual_retains_native_desired_state_for_exact_entity():
    runtime = setup()
    assert runtime.observe_operation(operation(1)).mutated
    assert runtime.engine.resolve(A).appearance == PURPLE
    assert runtime.engine.resolve(B).layer.owner == "daily"


@pytest.mark.parametrize(
    "kind",
    [
        IntentEvidenceKind.UNKNOWN,
        IntentEvidenceKind.PHYSICAL_DRIFT,
        IntentEvidenceKind.RECOVERY_TELEMETRY,
        IntentEvidenceKind.AVAILABILITY_CHANGE,
        IntentEvidenceKind.HLM_COMMAND_CONSEQUENCE,
    ],
)
def test_non_intent_does_not_create_manual_or_change_valid_state(kind):
    runtime = setup()
    result = runtime.observe_operation(replace(operation(1), evidence=IntentEvidence(kind)))
    assert not result.mutated
    assert runtime.engine.resolve(A).layer.owner == "daily"
    assert runtime.ownership_diagnostics()["latest_operation_rejection"]["reason"]


def test_failed_operation_does_not_win_over_successful_intent():
    runtime = setup()
    runtime.observe_operation(operation(1))
    failed = replace(
        operation(3, appearance=Appearance(on=True, brightness=255)),
        evidence=replace(GOOD, succeeded=False),
    )
    assert not runtime.observe_operation(failed).mutated
    assert runtime.observe_operation(
        operation(2, appearance=Appearance(on=True, brightness=80))
    ).mutated
    assert runtime.engine.resolve(A).appearance.brightness == 80


def test_automatic_off_and_manual_first_then_second_off():
    runtime = setup()
    assert runtime.observe_operation(operation(1, kind="off")).reason == "created_manual_off"
    assert runtime.engine.resolve(A).layer.kind is LayerKind.MANUAL_OFF
    runtime.observe_operation(operation(2))
    assert runtime.observe_operation(operation(3, kind="off")).reason == "released_manual"
    assert runtime.engine.resolve(A).layer.owner == "daily"
    assert runtime.observe_operation(operation(4, kind="off")).reason == "created_manual_off"
    assert runtime.engine.resolve(A).appearance.on is False


def test_newest_successful_intent_wins_and_duplicate_off_does_not_peel_twice():
    runtime = setup()
    assert runtime.observe_operation(operation(2)).mutated
    assert not runtime.observe_operation(
        operation(1, appearance=Appearance(on=True, brightness=1))
    ).mutated
    assert runtime.engine.resolve(A).appearance == PURPLE
    off = operation(3, kind="off")
    assert runtime.observe_operation(off).reason == "released_manual"
    assert runtime.observe_operation(off).reason == "duplicate operation"
    assert runtime.engine.resolve(A).layer.owner == "daily"


def test_group_appearance_is_one_operation_with_shared_provenance():
    runtime = setup()
    result = runtime.observe_operation(operation(1, members=(A, B), group="kitchen"))
    assert result.affected == (A, B)
    assert len(runtime.operations.history) == 1
    assert runtime.diagnostics().homeowner_events == 1
    for entity in (A, B):
        item = runtime.engine.resolve(entity).layer
        assert (item.operation_id, item.group_id) == ("op-1", "kitchen")
    assert runtime.engine.resolve(C).layer.owner == "daily"


def test_mixed_group_off_releases_then_stays_off_on_further_offs():
    runtime = setup()
    runtime.observe_operation(operation(1))
    runtime.observe_operation(operation(2, members=(B,), kind="off"))
    first = runtime.observe_operation(operation(3, members=(A, B, C), kind="off", group="main"))
    assert first.reason == "released_to_hlm"
    assert all(runtime.engine.resolve(e).layer.owner == "daily" for e in (A, B, C))
    for n in (4, 5):
        assert (
            runtime.observe_operation(
                operation(n, members=(C, B, A), kind="off", group="main")
            ).reason
            == "created_group_manual_off"
        )
        assert all(runtime.engine.resolve(e).layer.kind is LayerKind.MANUAL_OFF for e in (A, B, C))


@pytest.mark.parametrize("unavailable", [True, False])
def test_group_skips_unavailable_or_failed_members_and_never_replays(unavailable):
    runtime = setup()
    op = operation(1, members=(A, B), group="main")
    skipped = replace(op.members[1], available=not unavailable, succeeded=unavailable)
    op = replace(op, members=(op.members[0], skipped))
    result = runtime.observe_operation(op)
    assert result.affected == (A,) and result.skipped == (B,)
    assert runtime.engine.resolve(B).layer.owner == "daily"
    assert not runtime.observe_operation(
        replace(op, members=(op.members[0], replace(skipped, available=True, succeeded=True)))
    ).mutated
    assert runtime.engine.resolve(B).layer.owner == "daily"


def test_unavailable_group_off_does_not_create_fake_off():
    runtime = setup()
    for n in (1, 2):
        op = operation(n, kind="off", group="main", members=(A, B))
        op = replace(op, members=(op.members[0], replace(op.members[1], available=False)))
        runtime.observe_operation(op)
    assert runtime.engine.resolve(A).layer.kind is LayerKind.MANUAL_OFF
    assert runtime.engine.resolve(B).layer.owner == "daily"


def test_all_unavailable_does_not_arm_group_off():
    runtime = setup()
    op = operation(1, kind="off", group="main")
    assert not runtime.observe_operation(
        replace(op, members=(replace(op.members[0], available=False),))
    ).mutated
    assert (
        runtime.observe_operation(operation(2, kind="off", group="main")).reason
        == "released_to_hlm"
    )


def test_intervening_member_intent_disarms_only_overlapping_group():
    runtime = setup()
    runtime.observe_operation(operation(1, kind="off", group="main", members=(A, B)))
    runtime.observe_operation(operation(2, members=(C,)))
    assert (
        runtime.observe_operation(operation(3, kind="off", group="main", members=(A, B))).reason
        == "created_group_manual_off"
    )
    runtime.observe_operation(operation(4))
    assert (
        runtime.observe_operation(operation(5, kind="off", group="main", members=(A, B))).reason
        == "released_to_hlm"
    )


def test_changed_group_membership_restarts_first_off():
    runtime = setup()
    runtime.observe_operation(operation(1, kind="off", group="main", members=(A, B)))
    assert (
        runtime.observe_operation(operation(2, kind="off", group="main", members=(A, C))).reason
        == "released_to_hlm"
    )


def test_stale_group_is_rejected_atomically_if_one_member_has_newer_intent():
    runtime = setup()
    runtime.observe_operation(operation(3))
    assert not runtime.observe_operation(
        operation(2, kind="off", group="main", members=(A, B))
    ).mutated
    assert runtime.engine.resolve(A).appearance == PURPLE
    assert runtime.engine.resolve(B).layer.owner == "daily"


def test_evening_activation_preserves_daytime_manual_but_reclaims_manual_off():
    runtime = setup()
    runtime.observe_operation(operation(1))
    runtime.observe_operation(operation(2, kind="off", members=(B,)))
    runtime.engine.evening_activation()
    runtime.engine.activate_automatic(A, layer("holiday", precedence=900))
    assert runtime.engine.resolve(A).appearance == PURPLE
    assert runtime.engine.resolve(B).layer.owner == "daily"
    runtime.engine.expire_boundary(NIGHTLY_BOUNDARY)
    assert runtime.engine.resolve(A).layer.owner == "holiday"


def test_structural_session_push_preserves_manual_beneath_and_expiry_prevents_return():
    runtime = setup()
    runtime.observe_operation(operation(1))
    runtime.engine.activate_automatic(A, layer("sync", precedence=1), supersede=True, priority=500)
    assert runtime.engine.resolve(A).layer.owner == "sync"
    assert any(item.appearance == PURPLE for item in runtime.engine.layers(A))
    runtime.engine.expire_boundary(NIGHTLY_BOUNDARY)
    runtime.engine.remove_owner(A, "sync")
    assert runtime.engine.resolve(A).layer.owner == "daily"


def family(runtime):
    runtime.engine.start_family("spa", "session-1")
    parent = layer("spa", family="spa", session_id="session-1", precedence=300)
    runtime.engine.activate_automatic(A, parent, supersede=True)
    runtime.engine.push(
        A,
        layer(
            "blink",
            kind=LayerKind.OVERLAY,
            family="spa",
            session_id="session-1",
            precedence=400,
            parent_layer_id="spa",
        ),
    )


def test_family_override_suppresses_parent_and_child_release_cannot_resurrect():
    runtime = setup()
    family(runtime)
    runtime.observe_operation(operation(1))
    assert not runtime.engine.family_eligible("spa", "session-1")
    runtime.observe_operation(operation(2, kind="off"))
    assert runtime.engine.resolve(A).layer.owner == "daily"
    runtime.engine.end_family("spa", "session-1")
    with pytest.raises(ValueError, match="ended session"):
        runtime.engine.start_family("spa", "session-1")
    runtime.engine.start_family("spa", "session-2")
    assert runtime.engine.family_eligible("spa", "session-2")


def test_child_requires_matching_existing_parent_not_just_session():
    runtime = setup()
    family(runtime)
    assert runtime.engine.resolve(A).layer.owner == "blink"
    runtime.engine.remove_layer(A, "spa")
    assert runtime.engine.resolve(A).layer.owner == "daily"


@pytest.mark.parametrize("change", ["intent", "boundary", "generation", "family", "remove"])
def test_stale_work_invalidated_by_new_truth(change):
    runtime = setup()
    token = runtime.engine.work_token()
    if change == "intent":
        runtime.observe_operation(operation(1))
    elif change == "boundary":
        runtime.engine.expire_boundary(NIGHTLY_BOUNDARY)
    elif change == "generation":
        runtime.engine.next_generation()
    elif change == "family":
        runtime.engine.start_family("spa", "session-1")
    else:
        runtime.engine.remove_layer(A, "daily")
    assert not runtime.engine.is_current_work(token)
    assert runtime.engine.is_current_work(runtime.engine.work_token())


def test_new_generation_rejects_old_operation_and_discards_old_layers():
    runtime = setup()
    runtime.observe_operation(operation(1))
    runtime.engine.next_generation()
    assert not runtime.observe_operation(operation(2, kind="off")).mutated
    assert runtime.engine.resolve(A).layer is None


def test_reassert_stable_layer_identity_does_not_grow_stack():
    engine = OwnershipEngine()
    for _ in range(100):
        engine.push(A, layer())
    assert len(engine.layers(A)) == 1


def test_persistence_roundtrip_provenance_but_not_transient_group_arming():
    runtime = setup()
    runtime.observe_operation(operation(1, group="main", members=(A, B)))
    payload = json.loads(json.dumps(runtime.export_persistence()))
    restored = ShadowRuntime(generation=2)
    restored.restore(
        deserialize_state(payload),
        manual_evidence={e: ManualRecoveryEvidence(True, True, True) for e in (A, B)},
        family_evidence={},
    )
    assert restored.engine.resolve(A).appearance == PURPLE
    assert restored.engine.resolve(A).layer.group_id == "main"
    assert restored.engine.resolve(B).layer.operation_id == "op-1"
    first = replace(operation(2, kind="off", group="main", members=(A, B)), generation=2)
    assert restored.observe_operation(first).reason == "released_to_hlm"


@pytest.mark.parametrize(
    "appearance",
    [
        None,
        {"on": "off"},
        {"on": True, "brightness": True},
        {"on": True, "xy_color": [float("nan"), 0.3]},
        {"on": True, "rgb_color": [1, 2, 999]},
        {"on": False},
        {"on": True, "color_temp_kelvin": -1},
    ],
)
def test_malformed_persisted_manual_drops_even_with_external_trust(appearance):
    payload = serialize_state({A: [layer("manual", kind=LayerKind.MANUAL, appearance=PURPLE)]}, [])
    payload["layers"][0]["layer"]["appearance"] = appearance
    parsed = deserialize_state(payload)
    assert parsed.layers == ()


@pytest.mark.parametrize("value", [None, 4, {}, "bad"])
def test_malformed_persistence_containers_fail_closed(value):
    assert not deserialize_state({"version": 2, "layers": value}).layers


def test_version_one_payload_remains_readable_without_fabricating_provenance():
    payload = serialize_state({A: [layer("manual", kind=LayerKind.MANUAL, appearance=PURPLE)]}, [])
    payload["version"] = 1
    for key in ("operation_id", "group_id", "parent_layer_id"):
        payload["layers"][0]["layer"].pop(key)
    parsed = deserialize_state(payload)
    assert parsed.layers[0].operation_id is None
    assert parsed.layers[0].appearance == PURPLE


def test_diagnostics_bound_history_and_expose_stack_and_rejection():
    runtime = setup()
    for n in range(1, 40):
        runtime.observe_operation(operation(n))
    runtime.observe_operation(operation(1))
    diagnostics = runtime.ownership_diagnostics()
    assert len(diagnostics["recent_operations"]) == 12
    assert diagnostics["latest_homeowner_operation"]["sequence"] == 39
    assert diagnostics["latest_operation_rejection"]
    assert diagnostics["ownership_entities"][A]["manual_appearance"]
    assert len(diagnostics["ownership_entities"][A]["layers"]) == 2


def test_entire_hlm_component_has_no_service_dispatch_or_command_tokens():
    forbidden = (
        "services.async_call",
        "hass.services.async_call",
        "light.turn_on",
        "light.turn_off",
        "scene.turn_on",
        "services.call(",
    )
    for source in Path("custom_components/home_lighting_manager").glob("*.py"):
        text = source.read_text()
        assert not any(token in text for token in forbidden), source


def test_group_override_evaluates_all_members_against_same_exposed_family():
    runtime = setup()
    for name, priority in (("underlying", 250), ("exposed", 300)):
        runtime.engine.start_family(name, "session")
        for entity in (A, B):
            runtime.engine.push(
                entity, layer(name, precedence=priority, family=name, session_id="session")
            )
    runtime.observe_operation(operation(1, group="main", members=(A, B)))
    assert not runtime.engine.family_eligible("exposed", "session")
    assert runtime.engine.family_eligible("underlying", "session")
    assert all(runtime.engine.resolve(e).layer.kind is LayerKind.MANUAL for e in (A, B))


def test_group_second_off_suppresses_exposed_family_and_its_children():
    runtime = setup()
    family(runtime)
    runtime.observe_operation(operation(1, kind="off", group="spa", members=(A,)))
    assert runtime.engine.family_eligible("spa", "session-1")
    runtime.observe_operation(operation(2, kind="off", group="spa", members=(A,)))
    assert not runtime.engine.family_eligible("spa", "session-1")
    assert runtime.engine.resolve(A).layer.kind is LayerKind.MANUAL_OFF


def test_incomplete_native_color_mode_persistence_defaults_to_hlm():
    payload = serialize_state(
        {
            A: [
                layer(
                    "manual", kind=LayerKind.MANUAL, appearance=Appearance(on=True, color_mode="xy")
                )
            ]
        },
        [],
    )
    assert not deserialize_state(payload).layers


def test_group_invalid_admission_is_rejected_before_any_mutation():
    runtime = setup()
    op = operation(1, members=(A, B), group="main")
    op = replace(op, members=(op.members[0], replace(op.members[1], manual_precedence="bad")))
    assert not runtime.observe_operation(op).mutated
    assert all(runtime.engine.resolve(e).layer.owner == "daily" for e in (A, B))


def test_recovered_legacy_manual_without_boundary_expires_normally():
    persisted = deserialize_state(
        serialize_state({A: [layer("manual", kind=LayerKind.MANUAL, appearance=PURPLE)]}, [])
    )
    runtime = ShadowRuntime()
    runtime.restore(
        persisted, manual_evidence={A: ManualRecoveryEvidence(True, True, True)}, family_evidence={}
    )
    runtime.engine.expire_boundary(NIGHTLY_BOUNDARY)
    assert runtime.engine.resolve(A).layer is None


def test_diagnostics_cap_entities_layers_sessions_and_members():
    runtime = ShadowRuntime()
    members = tuple(f"light.member_{n}" for n in range(40))
    runtime.observe_operation(operation(1, members=members, group="large"))
    for n in range(20):
        runtime.engine.start_family(f"family-{n}", "session")
        runtime.engine.push(members[0], layer(f"layer-{n}"))
    diagnostics = runtime.ownership_diagnostics()
    assert len(diagnostics["ownership_entities"]) == 32
    assert diagnostics["ownership_entities_truncated"]
    assert len(diagnostics["ownership_entities"][members[0]]["layers"]) == 8
    assert len(diagnostics["family_sessions"]) == 16
    assert len(diagnostics["latest_homeowner_operation"]["requested"]) == 32
