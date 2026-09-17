"""Settled family eligibility and transient group interaction contract regressions."""

from dataclasses import replace

import pytest
from homeassistant.core import HomeAssistant

from custom_components.home_lighting_manager.engine import NIGHTLY_BOUNDARY
from custom_components.home_lighting_manager.ha_observer import HomeAssistantShadowObserver
from custom_components.home_lighting_manager.intent_policy import IntentEvidence, IntentEvidenceKind
from custom_components.home_lighting_manager.model import Appearance, LayerKind, OwnershipLayer
from custom_components.home_lighting_manager.operations import HomeownerOperation, MemberOutcome
from custom_components.home_lighting_manager.persistence import (
    STORAGE_VERSION,
    STORE_ENVELOPE_VERSION,
    deserialize_state,
)
from custom_components.home_lighting_manager.recovery import (
    FamilyRecoveryEvidence,
    ManualRecoveryEvidence,
)
from custom_components.home_lighting_manager.shadow import ShadowRuntime

A, B = "light.member_a", "light.member_b"
TRUST = ManualRecoveryEvidence(True, True, True)


def layer(runtime, name, **kw):
    return OwnershipLayer(
        name,
        name,
        LayerKind.AUTOMATIC,
        runtime.engine.generation,
        0,
        appearance=Appearance(on=True, brightness=100),
        **kw,
    )


def base(runtime):
    for entity in (A, B):
        runtime.engine.activate_automatic(entity, layer(runtime, "evening"), priority=100)


def operation(runtime, n, *, kind="appearance", group=None, members=None, **kw):
    return HomeownerOperation(
        f"receipt-{n}",
        n,
        runtime.engine.generation,
        kind,
        IntentEvidence(IntentEvidenceKind.EXPLICIT_HOMEOWNER_COMMAND),
        members or (MemberOutcome(A, appearance=Appearance(on=True, brightness=200)),),
        group_id=group,
        **kw,
    )


def family(runtime, session="session-a", sequence=1):
    runtime.engine.start_family("effect", session, sequence=sequence)
    parent = layer(
        runtime, "parent", family="effect", session_id=session, session_sequence=sequence
    )
    child = replace(
        parent, layer_id="child", owner="child", kind=LayerKind.OVERLAY, parent_layer_id="parent"
    )
    for entity in (A, B):
        runtime.engine.activate_automatic(entity, parent, priority=200)
        runtime.engine.activate_automatic(entity, child, priority=300)
    return parent, child


def test_family_scope_hidden_updates_release_and_new_independent_session():
    rt = ShadowRuntime()
    base(rt)
    parent, child = family(rt)
    rt.engine.start_family("unrelated", "other", sequence=1)
    rt.engine.activate_automatic(
        A,
        layer(rt, "other", family="unrelated", session_id="other", session_sequence=1),
        priority=50,
    )
    assert rt.observe_operation(operation(rt, 1)).mutated
    assert not rt.engine.family_eligible("effect", "session-a")
    assert rt.engine.family_eligible("unrelated", "other")
    before = [item.layer_id for item in rt.engine.layers(A)]
    # Retain useful hidden desired state and provenance without changing placement or eligibility.
    rt.engine.activate_automatic(
        A,
        replace(parent, appearance=Appearance(on=True, brightness=77)),
        priority=200,
        supersede=True,
    )
    rt.engine.activate_automatic(
        A,
        replace(child, appearance=Appearance(on=True, brightness=88)),
        priority=300,
        supersede=True,
    )
    rt.engine.start_family("effect", "session-a", sequence=1)
    assert [item.layer_id for item in rt.engine.layers(A)] == before
    assert rt.engine.resolve(A).layer.kind is LayerKind.MANUAL
    assert rt.engine.resolve(B).layer.owner == "evening"  # family-wide, not just entity A
    assert not rt.engine.layer_eligible(A, rt.engine.layers(A)[2])
    assert rt.observe_operation(operation(rt, 2, kind="off")).reason == "released_manual"
    assert rt.engine.resolve(A).layer.owner == "evening"
    rt.engine.push(A, child)  # late child within suppressed session stays ineligible
    assert rt.engine.resolve(A).layer.owner == "evening"
    assert not rt.engine.layer_eligible(A, child)
    rt.engine.end_family("effect", "session-a")
    assert not any(item.family == "effect" for item in rt.engine.layers(A))
    family(rt, "session-b", 2)
    assert rt.engine.family_eligible("effect", "session-b")
    assert rt.engine.resolve(A).layer.owner == "child"
    token = rt.engine.work_token()
    for stale in (parent, child, replace(child, session_id="session-b")):
        with pytest.raises(ValueError):
            rt.engine.push(A, stale)
        assert rt.engine.is_current_work(token)
    assert rt.engine.family_eligible("unrelated", "other")
    assert rt.engine.family_eligible("effect", "session-b")


def test_unsuppressed_underlying_family_remains_available_after_generic_manual_pop():
    rt = ShadowRuntime()
    base(rt)
    family(rt)
    # Construct a generic non-suppressing stack history; not a qualified family override policy.
    rt.engine.admit_manual(A, replace(layer(rt, "manual"), owner="manual", kind=LayerKind.MANUAL))
    assert rt.engine.manual_release(A)
    assert rt.engine.resolve(A).layer.owner == "child"
    assert rt.engine.family_eligible("effect", "session-a")


def test_missing_parent_cannot_be_created_by_suppression_or_child_receipt():
    rt = ShadowRuntime()
    base(rt)
    token = rt.engine.work_token()
    with pytest.raises(ValueError, match="existing parent session"):
        rt.register_suppression("effect", "missing", "late child")
    with pytest.raises(ValueError, match="existing parent session"):
        rt.engine.push(
            A,
            replace(
                layer(rt, "child", family="effect", session_id="missing"),
                kind=LayerKind.OVERLAY,
                parent_layer_id="parent",
            ),
        )
    assert rt.engine.is_current_work(token)
    assert not rt.engine.family_sessions()


def test_stale_session_end_cannot_disarm_new_group_interaction():
    rt = ShadowRuntime()
    family(rt)
    rt.engine.end_family("effect", "session-a")
    family(rt, "session-b", 2)
    rt.observe_operation(operation(rt, 1, kind="off", group="group"))
    token = rt.engine.work_token()
    rt.engine.end_family("effect", "session-a")
    assert rt.engine.is_current_work(token)
    assert rt.engine.group_off_sequences() == {"group": (A,)}
    assert rt.engine.family_eligible("effect", "session-b")


def restart(old, payload=None, manual_evidence=None, family_evidence=None):
    new = ShadowRuntime(generation=old.engine.generation + 1)
    new.restore(
        deserialize_state(old.export_persistence() if payload is None else payload),
        manual_evidence=manual_evidence or {},
        family_evidence=family_evidence or {},
    )
    base(new)  # current truth reconstructed; never restore the old automatic layers
    return new


def test_restart_discards_group_arming_even_with_persisted_provenance_and_fake_history():
    old = ShadowRuntime()
    base(old)
    members = (MemberOutcome(A), MemberOutcome(B))
    old.observe_operation(operation(old, 1))
    assert (
        old.observe_operation(operation(old, 2, kind="off", group="zone", members=members)).reason
        == "released_to_hlm"
    )
    assert all(old.engine.resolve(e).layer.owner == "evening" for e in (A, B))
    payload = old.export_persistence()
    assert set(payload) == {"version", "layers", "suppressed_sessions"}
    # Even extraneous logs/attributes are not recovery authority.
    payload.update(old.ownership_diagnostics())
    payload.update(group_off_armed={"zone": [A, B]}, last_operation="off", off_count=1)
    new = restart(old, payload)
    assert not new.engine.group_off_sequences()
    assert new.operations.latest_homeowner is None and not new.operations.history
    assert (
        new.observe_operation(operation(new, 3, kind="off", group="zone", members=members)).reason
        == "released_to_hlm"
    )
    assert all(new.engine.resolve(e).layer.owner == "evening" for e in (A, B))
    assert len(new.operations.history) == 1
    assert new.engine.group_off_sequences() == {"zone": (A, B)}


def test_durable_manual_off_restores_without_physical_state_then_expires():
    old = ShadowRuntime()
    base(old)
    old.observe_operation(operation(old, 1, kind="off"))
    new = restart(old, manual_evidence={A: TRUST})  # pure model has no physical state input
    assert new.engine.resolve(A).layer.kind is LayerKind.MANUAL_OFF
    assert new.ownership_diagnostics()["startup_recovery"]["restored_manual_off"] == 1
    assert not new.engine.group_off_sequences()
    assert new.diagnostics().homeowner_events == 0
    new.engine.expire_boundary(NIGHTLY_BOUNDARY)
    assert new.engine.resolve(A).layer.owner == "evening"


@pytest.mark.parametrize("invalid", ["missing_desired", "ambiguous", "unavailable", "expired"])
def test_questionable_persisted_manual_off_relinquishes_to_hlm(invalid):
    old = ShadowRuntime()
    old.observe_operation(operation(old, 1, kind="off"))
    payload = old.export_persistence()
    evidence = TRUST
    if invalid == "missing_desired":
        payload["layers"][0]["layer"]["appearance"] = None
    elif invalid == "expired":
        evidence = replace(TRUST, temporally_valid=False)
    else:
        evidence = replace(TRUST, ownership_evidence_coherent=False)
    new = restart(old, payload, {A: evidence})
    assert new.engine.resolve(A).layer.owner == "evening"
    assert new.ownership_diagnostics()["startup_recovery"]["rejected_manual"] == 1
    assert not new.engine.group_off_sequences()


def test_restored_group_manual_off_provenance_cannot_arm_future_off():
    old = ShadowRuntime()
    base(old)
    members = (MemberOutcome(A), MemberOutcome(B))
    for n in (1, 2):
        old.observe_operation(operation(old, n, kind="off", group="zone", members=members))
    new = restart(old, manual_evidence={A: TRUST, B: TRUST})
    assert all(new.engine.resolve(e).layer.kind is LayerKind.MANUAL_OFF for e in (A, B))
    assert not new.engine.group_off_sequences()
    # Existing §4 first-OFF release applies even to trustworthy restored exceptions.
    assert (
        new.observe_operation(operation(new, 3, kind="off", group="zone", members=members)).reason
        == "released_to_hlm"
    )
    assert all(new.engine.resolve(e).layer.owner == "evening" for e in (A, B))


def test_restored_suppression_neither_arms_group_nor_creates_homeowner_intent():
    old = ShadowRuntime()
    family(old)
    old.engine.suppress_family("effect", "session-a", "homeowner_override")
    new = restart(
        old, family_evidence={("effect", "session-a"): FamilyRecoveryEvidence(True, True)}
    )
    assert not new.engine.family_eligible("effect", "session-a")
    assert new.diagnostics().homeowner_events == 0
    assert not new.engine.group_off_sequences()
    assert new.ownership_diagnostics()["startup_recovery"]["restored_suppressed_sessions"] == 1
    new.engine.end_family("effect", "session-a")
    family(new, "session-b", 2)
    assert new.engine.family_eligible("effect", "session-b")


def test_pre_restart_group_receipt_and_family_work_have_no_authority():
    old = ShadowRuntime()
    _, child = family(old)
    stale = operation(old, 1, kind="off", group="zone", expected=old.engine.work_token())
    new = restart(old)
    token = new.engine.work_token()
    assert not new.observe_operation(stale).mutated
    assert not new.observe_operation(replace(stale, generation=new.engine.generation)).mutated
    with pytest.raises(ValueError):
        new.engine.push(A, child, expected=old.engine.work_token())
    assert new.engine.is_current_work(token)
    assert not new.engine.group_off_sequences()


def test_unavailable_group_member_and_newer_individual_intent_remain_separate():
    rt = ShadowRuntime()
    base(rt)
    members = (MemberOutcome(A), MemberOutcome(B, available=False))
    for n in (1, 2):
        result = rt.observe_operation(operation(rt, n, kind="off", group="zone", members=members))
        assert result.affected == (A,) and result.skipped == (B,)
    assert rt.engine.resolve(A).layer.kind is LayerKind.MANUAL_OFF
    assert rt.engine.resolve(B).layer.owner == "evening"
    rt.observe_operation(operation(rt, 4))
    assert not rt.engine.group_off_sequences()
    assert not rt.observe_operation(
        operation(rt, 3, kind="off", group="zone", members=members)
    ).mutated
    assert rt.observe_operation(operation(rt, 5, kind="off")).reason == "released_manual"
    assert rt.engine.resolve(A).layer.owner == "evening"
    assert rt.observe_operation(operation(rt, 6, kind="off")).reason == "created_manual_off"


def test_bounded_diagnostics_distinguish_family_end_and_recovery_from_live_arming():
    rt = ShadowRuntime()
    family(rt)
    assert rt.ownership_diagnostics()["family_sessions"][0]["eligible"]
    rt.engine.suppress_family("effect", "session-a", "homeowner")
    record = rt.ownership_diagnostics()["family_sessions"][0]
    assert record["suppressed"] and not record["eligible"]
    rt.engine.end_family("effect", "session-a")
    diag = rt.ownership_diagnostics()
    assert not diag["family_sessions"]
    assert diag["ended_family_sessions"] == [{"family": "effect", "session_id": "session-a"}]
    new = restart(rt)
    assert (
        new.ownership_diagnostics()["startup_recovery"]["group_off_history"]
        == "cleared_on_recovery"
    )
    assert not new.ownership_diagnostics()["ended_family_sessions"]
    for n in range(20):
        new.engine.start_family("effect", f"new-{n}", sequence=n + 1)
        new.engine.end_family("effect", f"new-{n}")
    assert len(new.ownership_diagnostics()["ended_family_sessions"]) == 16
    assert (STORE_ENVELOPE_VERSION, STORAGE_VERSION) == (1, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("physical", ["off", "unavailable"])
async def test_physical_state_without_persisted_intent_cannot_restore_manual_off(
    tmp_path, physical
):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set(A, physical)
    observer = HomeAssistantShadowObserver(hass, [A], {A: 250})
    await observer.async_start()
    try:
        assert observer.runtime.engine.resolve(A).layer is None
        assert not observer.runtime.engine.group_off_sequences()
        assert observer.runtime.diagnostics().homeowner_events == 0
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()
