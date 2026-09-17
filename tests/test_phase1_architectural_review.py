"""Adversarial review invariants; no wall-clock scheduling or device dispatch."""

import copy
import itertools
import json
import sys
import types
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant

from custom_components.home_lighting_manager.attribution_correlation import (
    ExternalBurstCorrelator,
    ExternalTopologyEvent,
)
from custom_components.home_lighting_manager.engine import OwnershipEngine
from custom_components.home_lighting_manager.ha_observer import HomeAssistantShadowObserver
from custom_components.home_lighting_manager.intent_policy import IntentEvidence, IntentEvidenceKind
from custom_components.home_lighting_manager.model import Appearance, LayerKind, OwnershipLayer
from custom_components.home_lighting_manager.operations import HomeownerOperation, MemberOutcome
from custom_components.home_lighting_manager.persistence import deserialize_state
from custom_components.home_lighting_manager.promotion_observer import (
    PromotingHomeAssistantShadowObserver,
)
from custom_components.home_lighting_manager.recovery import ManualRecoveryEvidence
from custom_components.home_lighting_manager.shadow import ShadowRuntime

A, B = "light.a", "light.b"
GOOD = IntentEvidence(IntentEvidenceKind.EXPLICIT_HOMEOWNER_COMMAND)


def system(name, *, priority=100, **kwargs):
    return OwnershipLayer(
        name,
        name,
        LayerKind.AUTOMATIC,
        1,
        0,
        appearance=Appearance(on=True, brightness=100),
        precedence=priority,
        **kwargs,
    )


def receipt(n, kind="appearance", members=(A,), group=None, **kwargs):
    return HomeownerOperation(
        f"op-{n}",
        n,
        1,
        kind,
        GOOD,
        tuple(
            MemberOutcome(e, appearance=Appearance(on=True, brightness=n % 255)) for e in members
        ),
        group_id=group,
        **kwargs,
    )


def runtime():
    rt = ShadowRuntime()
    rt.engine.activate_automatic(A, system("daily"), priority=100)
    rt.engine.activate_automatic(B, system("daily"), priority=100)
    return rt


def test_sequence_a_updates_manual_then_two_distinct_offs():
    rt = runtime()
    for n in (1, 2):
        rt.observe_operation(receipt(n))
    assert len(rt.engine.layers(A)) == 2
    assert rt.observe_operation(receipt(3, "off")).reason == "released_manual"
    assert rt.engine.resolve(A).layer.owner == "daily"
    assert rt.observe_operation(receipt(4, "off")).reason == "created_manual_off"


def test_sequence_b_generic_removable_system_not_production_family():
    rt = runtime()
    rt.engine.activate_automatic(A, system("temporary", removable=True), priority=200)
    rt.observe_operation(receipt(1))
    rt.observe_operation(receipt(2, "off"))
    assert rt.engine.resolve(A).layer.owner == "temporary"
    assert rt.observe_operation(receipt(3, "off")).reason == "popped_layer"
    assert rt.engine.resolve(A).layer.owner == "daily"


def test_sequence_c_older_external_promotion_loses_to_latest_manual():
    rt = runtime()
    rt.observe_operation(receipt(10))
    rt.observe_operation(receipt(12))
    assert not rt.observe_operation(receipt(11)).mutated
    assert rt.engine.resolve(A).appearance.brightness == 12


def test_sequence_d_group_then_newer_member_then_delayed_group_receipt():
    rt = runtime()
    rt.observe_operation(receipt(1, members=(A, B), group="room"))
    rt.observe_operation(receipt(3))
    assert not rt.observe_operation(receipt(2, "off", (A, B), "room")).mutated
    assert rt.engine.resolve(A).appearance.brightness == 3
    assert rt.engine.resolve(B).appearance.brightness == 1


def test_sequence_e_independent_intents_never_synthesize_group():
    rt = runtime()
    rt.observe_operation(receipt(1))
    rt.observe_operation(receipt(2, members=(B,)))
    assert len(rt.operations.history) == 2
    assert all(item["group_id"] is None for item in rt.operations.history)


def test_sequence_f_parent_override_blocks_children_new_session_is_independent():
    rt = runtime()
    session = rt.engine.start_family("gauge", "one", sequence=1)
    parent = system("parent", family="gauge", session_id="one", session_sequence=session.sequence)
    rt.engine.activate_automatic(A, parent, priority=200, supersede=True)
    child = replace(
        parent, layer_id="child", owner="blink", kind=LayerKind.OVERLAY, parent_layer_id="parent"
    )
    rt.engine.activate_automatic(A, child, priority=300, supersede=True)
    rt.observe_operation(receipt(1))
    assert not rt.engine.family_eligible("gauge", "one")
    rt.engine.end_family("gauge", "one")
    rt.engine.start_family("gauge", "two", sequence=2)
    assert rt.engine.family_eligible("gauge", "two")
    assert not rt.engine.family_eligible("gauge", "one")


def test_sequence_g_recovery_does_not_replay_skipped_group_member():
    rt = runtime()
    op = receipt(1, members=(A, B), group="room")
    rt.observe_operation(
        replace(op, members=(op.members[0], replace(op.members[1], available=False)))
    )
    rt.engine.activate_automatic(
        B, replace(system("daily"), appearance=Appearance(on=True, brightness=99)), priority=100
    )
    rt.observe_operation(
        replace(
            receipt(2, members=(B,)), evidence=IntentEvidence(IntentEvidenceKind.RECOVERY_TELEMETRY)
        )
    )
    assert rt.engine.resolve(B).appearance.brightness == 99
    assert rt.engine.resolve(B).layer.kind is LayerKind.AUTOMATIC


def test_priority_never_reordered_by_hidden_owner_update_or_low_priority_supersession():
    rt = runtime()
    rt.engine.activate_automatic(A, system("higher"), priority=300)
    for n in range(100):
        rt.engine.activate_automatic(
            A,
            replace(
                system("daily"), layer_id=f"fresh-{n}", appearance=Appearance(on=True, brightness=n)
            ),
            priority=100,
        )
    assert len(rt.engine.layers(A)) == 2
    assert rt.engine.resolve(A).layer.owner == "higher"
    rt.engine.activate_automatic(A, system("lower"), priority=50, supersede=True)
    assert rt.engine.resolve(A).layer.owner == "higher"
    rt.engine.remove_owner(A, "higher")
    assert rt.engine.resolve(A).appearance.brightness == 99


def test_priority_and_manual_exposure_are_independent():
    rt = runtime()
    rt.engine.activate_automatic(A, system("huge"), priority=10000)
    rt.observe_operation(receipt(1))
    assert rt.engine.resolve(A).layer.kind is LayerKind.MANUAL
    rt.engine.activate_automatic(A, system("hidden"), priority=20000)
    assert rt.engine.resolve(A).layer.kind is LayerKind.MANUAL
    rt.observe_operation(receipt(2, "off"))
    assert rt.engine.resolve(A).layer.owner == "hidden"


def test_structural_protection_is_explicit_not_a_magic_numeric_rank():
    rt = runtime()
    rt.engine.activate_automatic(
        A, system("sync"), priority=500, supersede=True, protects_from_manual=True
    )
    rt.observe_operation(receipt(1))
    assert rt.engine.resolve(A).layer.owner == "sync"
    rt.engine.remove_owner(A, "sync")
    assert rt.engine.resolve(A).layer.kind is LayerKind.MANUAL


def test_duplicate_does_not_increment_homeowner_count_or_revision():
    rt = runtime()
    op = receipt(1, "off")
    rt.observe_operation(op)
    token = rt.engine.work_token()
    count = rt.diagnostics().homeowner_events
    for _ in range(100):
        assert not rt.observe_operation(op).mutated
    assert rt.diagnostics().homeowner_events == count
    assert rt.engine.is_current_work(token)
    assert len(rt.operations.history) == 1


def test_evicted_failed_receipt_cannot_replay_on_recovery():
    rt = runtime()
    op = receipt(1, "off")
    rt.observe_operation(replace(op, members=(replace(op.members[0], available=False),)))
    for n in range(2, 140):
        rt.observe_operation(receipt(n, members=(B,)))
    assert not rt.observe_operation(op).mutated
    assert rt.engine.resolve(A).layer.owner == "daily"


def test_recovery_after_newer_runtime_intent_cannot_win():
    old = ShadowRuntime()
    old.observe_operation(receipt(1))
    persisted = deserialize_state(old.export_persistence())
    rt = ShadowRuntime()
    rt.observe_operation(receipt(2))
    rt.restore(
        persisted, manual_evidence={A: ManualRecoveryEvidence(True, True, True)}, family_evidence={}
    )
    assert rt.engine.resolve(A).appearance.brightness == 2


def test_stale_tokens_reject_operation_and_system_admission():
    rt = runtime()
    token = rt.engine.work_token()
    rt.observe_operation(receipt(1))
    assert not rt.observe_operation(receipt(2, "off", expected=token)).mutated
    with pytest.raises(ValueError, match="stale work"):
        rt.engine.activate_automatic(A, system("late"), priority=200, expected=token)


def test_reconfigure_prunes_removed_entities_and_rejects_old_generation():
    rt = runtime()
    rt.observe_operation(receipt(1))
    rt.engine.reconfigure(frozenset({B}))
    assert rt.engine.resolve(A).layer is None
    assert not rt.observe_operation(receipt(2)).mutated
    assert not rt.observe_operation(replace(receipt(3), generation=2)).mutated


def test_returned_layer_metadata_cannot_mutate_authoritative_state():
    engine = OwnershipEngine()
    source = system("base", metadata={"nested": {"a": 1}})
    stored = engine.push(A, source)
    source.metadata["nested"]["a"] = 2
    stored.metadata["nested"]["a"] = 3
    engine.resolve(A).layer.metadata["nested"]["a"] = 4
    assert engine.layers(A)[0].metadata["nested"]["a"] == 1


def test_exhaustive_short_sequences_are_deterministic_and_one_exposed_owner():
    # 6^4 ordered sequences, each run twice; covers appearance/OFF/group/expiry/telemetry.
    for sequence in itertools.product(range(6), repeat=4):
        snapshots = []
        for _ in range(2):
            rt = runtime()
            for n, event in enumerate(sequence, 1):
                if event == 0:
                    rt.observe_operation(receipt(n))
                elif event == 1:
                    rt.observe_operation(receipt(n, "off"))
                elif event == 2:
                    rt.observe_operation(receipt(n, "off", (A, B), "room"))
                elif event == 3:
                    rt.engine.expire_boundary("nightly_0159")
                elif event == 4:
                    rt.observe_operation(
                        replace(
                            receipt(n),
                            evidence=IntentEvidence(IntentEvidenceKind.AVAILABILITY_CHANGE),
                        )
                    )
                else:
                    rt.observe_operation(receipt(n, members=(B,)))
                for entity in (A, B):
                    layers = rt.engine.layers(entity)
                    assert (
                        sum(
                            item.kind in (LayerKind.MANUAL, LayerKind.MANUAL_OFF) for item in layers
                        )
                        <= 1
                    )
                    exposed = rt.engine.resolve(entity).layer
                    assert exposed is None or rt.engine.layer_eligible(entity, exposed)
            snapshots.append((rt.engine.layers(A), rt.engine.layers(B), rt.engine.revision))
        assert snapshots[0] == snapshots[1]


def test_group_member_permutation_has_identical_state_and_diagnostics():
    results = []
    for members in ((A, B), (B, A)):
        rt = runtime()
        rt.observe_operation(receipt(1, members=members, group="room"))
        rt.observe_operation(receipt(2, "off", members, "room"))
        rt.observe_operation(receipt(3, "off", members, "room"))
        results.append((rt.engine.layers(A), rt.engine.layers(B), rt.ownership_diagnostics()))
    assert results[0] == results[1]


def test_six_month_session_simulation_is_bounded_and_stale_sequence_is_rejected():
    engine = OwnershipEngine()
    for n in range(1, 1001):
        engine.start_family("spa", f"session-{n}", sequence=n)
        engine.end_family("spa", f"session-{n}")
    assert not engine.family_sessions()
    assert len(engine._ended_sessions) <= 128
    assert len(engine._session_sequences) == 1
    with pytest.raises(ValueError, match="stale session"):
        engine.start_family("spa", "session-1", sequence=1)
    engine.start_family("spa", "new", sequence=1001)
    assert engine.family_eligible("spa", "new")


def test_correlation_is_bounded_and_overflow_cannot_qualify_partial_tail():
    correlator = ExternalBurstCorrelator()
    now = datetime(2026, 9, 17, tzinfo=ZoneInfo("UTC"))
    for n in range(1000):
        summary = correlator.observe(ExternalTopologyEvent(now + timedelta(milliseconds=n), A))
    summary = correlator.observe(
        ExternalTopologyEvent(now + timedelta(seconds=1), "light.group", (A,))
    )
    assert len(correlator._events) == 128
    assert not summary.external_intent_candidate()["qualified"]
    correlator.observe(ExternalTopologyEvent(now + timedelta(seconds=10), A))
    summary = correlator.observe(
        ExternalTopologyEvent(now + timedelta(seconds=10.1), "light.group", (A,))
    )
    assert summary.external_intent_candidate()["qualified"]


def test_group_interaction_is_bounded():
    engine = OwnershipEngine()
    for n in range(1000):
        engine.apply_group_off(f"group-{n}", [f"light.member_{n}"])
    assert len(engine.group_off_sequences()) <= 64


def test_group_first_off_restart_next_off_is_conservative_new_first():
    rt = runtime()
    rt.observe_operation(receipt(1, "off", (A, B), "room"))
    new = ShadowRuntime(generation=2)
    new.restore(deserialize_state(rt.export_persistence()), manual_evidence={}, family_evidence={})
    result = new.observe_operation(replace(receipt(2, "off", (A, B), "room"), generation=2))
    assert result.reason == "released_to_hlm"
    assert all(new.engine.resolve(e).layer is None for e in (A, B))


def test_durable_manual_off_survives_without_restoring_group_interaction():
    rt = ShadowRuntime()
    rt.observe_operation(receipt(1, "off"))
    new = ShadowRuntime(generation=2)
    new.restore(
        deserialize_state(rt.export_persistence()),
        manual_evidence={A: ManualRecoveryEvidence(True, True, True)},
        family_evidence={},
    )
    assert new.engine.resolve(A).layer.kind is LayerKind.MANUAL_OFF
    assert not new.engine.group_off_sequences()


def test_actual_pr22_parser_drops_v2_safely_on_rollback():
    name = "custom_components.home_lighting_manager._pr22_review_fixture"
    module = types.ModuleType(name)
    sys.modules[name] = module
    try:
        source = Path("tests/fixtures/pr22_persistence.py.txt").read_text()
        exec(compile(source, "pr22_persistence.py.txt", "exec"), module.__dict__)
        rt = ShadowRuntime()
        rt.observe_operation(receipt(1))
        parsed = module.deserialize_state(json.loads(json.dumps(rt.export_persistence())))
        assert parsed.layers == () and parsed.suppressed_sessions == ()
    finally:
        sys.modules.pop(name)


@pytest.mark.asyncio
async def test_actual_pr22_storage_migration_uses_current_configuration_and_evidence(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    ids = ["light.safe", "light.off", "light.partial", "light.unavailable"]
    observer = HomeAssistantShadowObserver(hass, ids, dict.fromkeys(ids, 250))
    fixture = json.loads(Path("tests/fixtures/pr22_shadow_store.json").read_text())
    await observer.store.async_save(fixture["data"])
    hass.states.async_set("light.safe", "on", {"brightness": 123, "rgb_color": (1, 2, 3)})
    hass.states.async_set("light.off", "off")
    hass.states.async_set("light.partial", "on", {"color_mode": "xy", "xy_color": (0.2, 0.3)})
    hass.states.async_set("light.unavailable", "unavailable")
    hass.states.async_set("light.removed", "on", {"brightness": 123})
    now = datetime.fromisoformat("2026-09-17T12:30:00-07:00")
    with patch("custom_components.home_lighting_manager.ha_observer.dt_util.now", return_value=now):
        await observer.async_start()
    try:
        assert observer.runtime.engine.resolve("light.safe").layer.kind is LayerKind.MANUAL
        assert observer.runtime.engine.resolve("light.off").layer.kind is LayerKind.MANUAL_OFF
        for entity in ("light.partial", "light.unavailable", "light.removed"):
            assert observer.runtime.engine.resolve(entity).layer is None
        assert observer.runtime.diagnostics().homeowner_events == 0
        stored = await observer.store.async_load()
        assert stored["version"] == 2
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.parametrize("change", ["future", "expired", "missing", "bad-rgb"])
@pytest.mark.asyncio
async def test_pr22_recovery_edge_cases_fail_closed(tmp_path, change):
    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = HomeAssistantShadowObserver(hass, ["light.safe"])
    raw = json.loads(Path("tests/fixtures/pr22_shadow_store.json").read_text())["data"]
    if change == "future":
        raw["saved_at"] = "2099-09-17T12:00:00-07:00"
    if change == "expired":
        raw["saved_at"] = "2020-09-17T12:00:00-07:00"
    await observer.store.async_save(raw)
    if change != "missing":
        hass.states.async_set(
            "light.safe",
            "on",
            {"brightness": 123, "rgb_color": None if change == "bad-rgb" else (1, 2, 3)},
        )
    now = datetime.fromisoformat("2026-09-17T12:30:00-07:00")
    with patch("custom_components.home_lighting_manager.ha_observer.dt_util.now", return_value=now):
        await observer.async_start()
    assert observer.runtime.engine.resolve("light.safe").layer is None
    await observer.async_shutdown()
    await hass.async_block_till_done()


def test_conflicting_persisted_manual_records_drop_instead_of_incidental_last_wins():
    raw = json.loads(Path("tests/fixtures/pr22_shadow_store.json").read_text())["data"]
    raw["layers"].append(
        copy.deepcopy(next(item for item in raw["layers"] if item["entity_id"] == "light.safe"))
    )
    parsed = deserialize_state(raw)
    assert not any(item.metadata["persisted_entity_id"] == "light.safe" for item in parsed.layers)


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [("on", "off"), ("off", "on"), ("on", "on")])
async def test_rapid_external_operations_each_require_fresh_aggregate_and_latest_wins(
    tmp_path, values
):
    hass = HomeAssistant(str(tmp_path))
    observer = PromotingHomeAssistantShadowObserver(hass, [A, "light.group"], {A: 250})
    await observer.async_start()
    try:
        for n, value in enumerate(values, 1):
            hass.states.async_set(A, value, {"brightness": n * 50})
            await hass.async_block_till_done()
            hass.states.async_set("light.group", value, {"entity_id": [A], "brightness": n * 50})
            await hass.async_block_till_done()
        layer = observer.runtime.engine.resolve(A).layer
        if values == ("on", "off"):
            assert layer is None
        else:
            assert layer.kind is LayerKind.MANUAL and layer.appearance.brightness == 100
        assert len(observer.runtime.operations.history) == 2
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_availability_change_invalidates_previously_retained_external_leaf(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    observer = PromotingHomeAssistantShadowObserver(hass, [A, "light.group"], {A: 250})
    await observer.async_start()
    try:
        hass.states.async_set(A, "on", {"brightness": 100})
        await hass.async_block_till_done()
        hass.states.async_set(A, "unavailable")
        await hass.async_block_till_done()
        hass.states.async_set("light.group", "on", {"entity_id": [A]})
        await hass.async_block_till_done()
        assert observer.runtime.engine.resolve(A).layer is None
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


def test_fresh_runtime_rejects_old_token_even_when_generation_revision_match():
    before, after = runtime(), runtime()
    assert before.engine.generation == after.engine.generation
    assert before.engine.revision == after.engine.revision
    assert not after.engine.is_current_work(before.engine.work_token())


def test_capacity_failure_is_atomic_across_group_members():
    rt = runtime()
    for n in range(31):
        rt.engine.activate_automatic(B, system(f"owner-{n}"), priority=n)
    token = rt.engine.work_token()
    result = rt.observe_operation(receipt(1, members=(A, B), group="room"))
    assert not result.mutated
    assert rt.engine.is_current_work(token)
    assert rt.engine.resolve(A).layer.owner == "daily"
    assert not rt.operations.history


def test_family_child_without_parent_identity_cannot_be_exposed():
    engine = OwnershipEngine()
    engine.start_family("spa", "s", sequence=1)
    engine.push(A, replace(system("orphan", family="spa", session_id="s"), kind=LayerKind.OVERLAY))
    assert engine.resolve(A).layer is None


@pytest.mark.asyncio
async def test_aggregate_user_context_does_not_create_fake_canonical_group_layer(tmp_path):
    from homeassistant.core import Context

    hass = HomeAssistant(str(tmp_path))
    observer = PromotingHomeAssistantShadowObserver(hass, ["light.group", A], {"light.group": 250})
    await observer.async_start()
    try:
        hass.states.async_set(
            "light.group", "off", {"entity_id": (A,)}, context=Context(user_id="user")
        )
        await hass.async_block_till_done()
        assert observer.runtime.engine.resolve("light.group").layer is None
        assert observer.runtime.engine.resolve(A).layer is None
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


def test_legacy_on_only_is_not_complete_for_brightness_capable_current_light():
    from homeassistant.core import State

    from custom_components.home_lighting_manager.ha_observer import _state_matches_persisted_layer

    layer = replace(system("manual"), owner="manual", kind=LayerKind.MANUAL, appearance=Appearance(on=True))
    assert not _state_matches_persisted_layer(State(A, "on", {"brightness": 123}), layer)


def test_logical_owner_cannot_replace_another_owner_by_reusing_layer_id():
    engine = runtime().engine
    with pytest.raises(ValueError):
        engine.push(A, replace(system("different"), layer_id="daily"))
    assert engine.resolve(A).layer.owner == "daily"


def test_skipped_group_diagnostics_are_member_order_independent():
    members = (MemberOutcome(A, available=False), MemberOutcome(B, available=False),
               MemberOutcome("light.c", appearance=Appearance(on=True)))
    records = []
    for ordered in (members, tuple(reversed(members))):
        rt = ShadowRuntime()
        result = rt.observe_operation(replace(receipt(1, group="room"), members=ordered))
        assert result.mutated
        records.append(rt.operations.latest_homeowner)
    assert records[0] == records[1]


@pytest.mark.asyncio
async def test_duplicate_direct_ha_off_context_cannot_become_second_off(tmp_path):
    from homeassistant.core import Context

    hass = HomeAssistant(str(tmp_path))
    observer = HomeAssistantShadowObserver(hass, [A], {A: 250})
    await observer.async_start()
    try:
        hass.states.async_set(A, "on", {"brightness": 90}, context=Context(user_id="user"))
        await hass.async_block_till_done()
        off_context = Context(user_id="user")
        hass.states.async_set(A, "off", {}, context=off_context)
        await hass.async_block_till_done()
        hass.states.async_set(A, "off", {"brightness": 90}, context=off_context)
        await hass.async_block_till_done()
        assert observer.runtime.engine.resolve(A).layer is None
        assert observer.runtime.diagnostics().homeowner_events == 2
        assert len(observer.runtime.operations.history) == 2
        hass.states.async_set(A, "off", {"brightness": 91}, context=Context(user_id="user"))
        await hass.async_block_till_done()
        assert observer.runtime.engine.resolve(A).layer.kind is LayerKind.MANUAL_OFF
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


def test_ineligible_structural_barrier_cannot_keep_manual_below_ordinary_owner():
    rt = runtime()
    rt.engine.start_family("structural", "s", sequence=1)
    rt.engine.activate_automatic(
        A, system("barrier", family="structural", session_id="s", session_sequence=1),
        priority=200, protects_from_manual=True,
    )
    rt.engine.suppress_family("structural", "s", "completed")
    rt.engine.activate_automatic(A, system("ordinary"), priority=300)
    assert rt.observe_operation(receipt(1)).mutated
    assert rt.engine.resolve(A).layer.kind is LayerKind.MANUAL
