"""Diagnostic fidelity tests: controlled counterexamples are not claimed as live root cause."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.home_lighting_manager.engine import OwnershipEngine
from custom_components.home_lighting_manager.ha_observer import DIAGNOSTIC_ENTITY_ID
from custom_components.home_lighting_manager.model import LayerKind
from custom_components.home_lighting_manager.promotion_observer import (
    PromotingHomeAssistantShadowObserver,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [3, 7])
@pytest.mark.parametrize("filtered_leaf", [False, True])
async def test_parent_first_receipt_reports_success_or_exact_missing_pending_gate(
    tmp_path, count, filtered_leaf
):
    leaves = tuple(f"light.leaf_{n}" for n in range(count))
    group, subgroup = "light.parent", "light.child_group"
    guard, sync = "input_boolean.test_guard", "binary_sensor.test_sync"
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set(guard, "off")
    hass.states.async_set(sync, "off")
    observer = PromotingHomeAssistantShadowObserver(
        hass, [*leaves, group, subgroup], dict.fromkeys(leaves, 250)
    )
    await observer.async_start()
    try:
        # Canonical topology exists before the physical operation. Context models automatic
        # restoration; event/canonical membership uses HA's actual State container handling.
        for leaf in leaves:
            hass.states.async_set(
                leaf, "on", {"brightness": 183}, context=Context(parent_id="daily")
            )
        for aggregate, members in ((group, leaves), (subgroup, leaves[:2])):
            hass.states.async_set(
                aggregate, "on", {"entity_id": list(members)}, context=Context(parent_id="daily")
            )
        await hass.async_block_till_done()
        observer._refresh_topology_cache()
        observer.runtime.engine.apply_group_off(group, leaves)
        start = dt_util.now() + timedelta(seconds=3)
        with (
            patch(
                "custom_components.home_lighting_manager.promotion_observer._SURFACE_GUARDS",
                ((group, guard),),
            ),
            patch(
                "custom_components.home_lighting_manager.promotion_observer._SCENE_RECALL_SOURCES",
                {"sensor.test": (group, guard, sync)},
            ),
        ):
            for n, leaf in enumerate(leaves):
                # Guard state alone is no longer negative evidence for every sibling
                # leaf. A filtered leaf must have an actual HA-command consequence.
                is_filtered = filtered_leaf and n == count - 1
                hass.states.async_set(guard, "on" if is_filtered else "off")
                if is_filtered:
                    observer._guarded_ha_consequence_entities[leaf] = (
                            guard,
                            "off",
                            start + timedelta(milliseconds=10 * n),
                        )
                with patch(
                    "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
                    return_value=start + timedelta(milliseconds=10 * n),
                ):
                    hass.states.async_set(leaf, "off", {"dynamics": "none"})
                    await hass.async_block_till_done()
            hass.states.async_set(guard, "off")
            with patch(
                "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
                return_value=start + timedelta(seconds=1.212756),
            ):
                hass.states.async_set(group, "off", {"entity_id": list(leaves)})
                await hass.async_block_till_done()
            attempts = [
                x
                for x in observer._armed_off_attempts
                if x["group_id"] == group and x["operation"] == "off"
            ]
            if filtered_leaf:
                assert attempts[-1]["result"] == "rejected_pending_member_missing"
                assert attempts[-1]["guard_state"] == "off"
                assert attempts[-1]["pending_members"][leaves[-1]] is None
                assert attempts[-1]["armed_members"] == tuple(sorted(leaves))
                assert any(
                    "guard is active" in str(x["result"]) for x in observer._off_leaf_evidence
                )
            else:
                assert attempts[-1]["result"] == "created_group_manual_off"
                assert set(observer.runtime.reconciliation_protected_entities(set(leaves))) == set(
                    leaves
                )
                assert all(
                    observer.runtime.engine.resolve(e).layer.kind is LayerKind.MANUAL_OFF
                    for e in leaves
                )
            with patch(
                "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
                return_value=start + timedelta(seconds=1.242192),
            ):
                hass.states.async_set(subgroup, "off", {"entity_id": list(leaves[:2])})
                await hass.async_block_till_done()
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert attrs["command_authority"] is False
        assert attrs["armed_group_off_attempts"]
        assert set(observer.runtime.export_persistence()) == {
            "version",
            "layers",
            "suppressed_sessions",
        }
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.parametrize(
    "cause", ["overlap", "member", "reset", "boundary", "generation", "capacity"]
)
def test_removal_ledger_identifies_actual_engine_site_without_changing_semantics(cause):
    engine = OwnershipEngine()
    engine.apply_group_off("parent", ["light.a", "light.b"])
    token = engine.work_token()
    if cause == "overlap":
        engine.apply_group_off("child", ["light.a"])
        reason = "apply_group_off:overlap"
    elif cause == "member":
        engine.reset_group_off_for_members(frozenset({"light.a"}))
        reason = "reset_group_off_for_members"
    elif cause == "reset":
        engine.reset_group_off_sequence()
        reason = "reset_group_off_sequence"
    elif cause == "boundary":
        engine.expire_boundary("nightly_0159")
        reason = "expire_boundary:nightly_0159"
    elif cause == "generation":
        engine.next_generation()
        reason = "next_generation"
    else:
        for n in range(64):
            engine.apply_group_off(f"other-{n}", [f"light.other_{n}"])
        reason = "apply_group_off:capacity"
    record = engine.group_off_changes()[0]
    assert record["group_id"] == "parent" and record["reason"] == reason
    assert record["members"] == ("light.a", "light.b")
    assert "parent" not in engine.group_off_sequences()
    assert not engine.is_current_work(token)
    current = engine.work_token()
    engine.group_off_changes()[0]["reason"] = "external mutation"
    assert engine.group_off_changes()[0]["reason"] == reason
    assert engine.is_current_work(current)


def test_removal_evidence_is_bounded_unrelated_groups_do_not_remove_parent():
    engine = OwnershipEngine()
    engine.apply_group_off("parent", ["light.a"])
    for _n in range(100):
        engine.apply_group_off("other", ["light.b"])
        engine.reset_group_off_sequence("other")
    assert len(engine.group_off_changes()) == 32
    assert engine.group_off_sequences() == {"parent": ("light.a",)}
    assert all(item["group_id"] == "other" for item in engine.group_off_changes())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("appearance", "rejected_not_off"),
        ("no_members", "rejected_no_aggregate_members"),
        ("not_armed", "rejected_group_not_armed"),
        ("membership", "rejected_aggregate_members_mismatch"),
        ("burst", "rejected_burst_leaves_mismatch"),
        ("missing", "rejected_pending_member_missing"),
        ("pending_on", "rejected_pending_operation_not_off"),
        ("mixed_generation", "rejected_pending_generations_mixed"),
        ("invalid_generation", "rejected_pending_generation_invalid"),
        ("stale_generation", "stale configuration generation"),
    ],
)
async def test_each_armed_gate_reports_its_reason(tmp_path, case, expected):
    from dataclasses import replace

    from homeassistant.core import State

    from custom_components.home_lighting_manager.intent_policy import (
        IntentAttributionSource,
        IntentEvidence,
        IntentEvidenceKind,
    )
    from custom_components.home_lighting_manager.promotion_observer import _PendingExternalLeaf
    from custom_components.home_lighting_manager.shadow import ShadowObservation

    hass = HomeAssistant(str(tmp_path))
    group, leaves = "light.group", ("light.a", "light.b")
    observer = PromotingHomeAssistantShadowObserver(hass, [group, *leaves])
    observer.runtime.engine.apply_group_off(group, leaves)
    evidence = IntentEvidence(
        IntentEvidenceKind.UNKNOWN, attribution_source=IntentAttributionSource.UNATTRIBUTED_EXTERNAL
    )
    obs = ShadowObservation(group, evidence, operation="off", sequence=3, generation=1)
    now = dt_util.now()
    for n, leaf in enumerate(leaves, 1):
        pending = replace(obs, entity_id=leaf, sequence=n)
        observer._pending_external_leaves[leaf] = _PendingExternalLeaf(
            now, pending, State(leaf, "off")
        )
    members = leaves
    burst = {"leaf_entities": list(leaves)}
    if case == "appearance":
        obs = replace(obs, operation="appearance")
    elif case == "no_members":
        members = ()
    elif case == "not_armed":
        observer.runtime.engine.reset_group_off_sequence()
    elif case == "membership":
        members = leaves[:1]
    elif case == "burst":
        burst["leaf_entities"] = [leaves[0]]
    elif case == "missing":
        observer._pending_external_leaves.pop(leaves[0])
    elif case == "pending_on":
        item = observer._pending_external_leaves[leaves[0]]
        observer._pending_external_leaves[leaves[0]] = replace(
            item, observation=replace(item.observation, operation="appearance")
        )
    else:
        for index, leaf in enumerate(leaves):
            if case == "mixed_generation" and index:
                continue
            item = observer._pending_external_leaves[leaf]
            value = None if case == "invalid_generation" else 99
            observer._pending_external_leaves[leaf] = replace(
                item, observation=replace(item.observation, generation=value)
            )
    token = observer.runtime.engine.work_token()
    observer._promote_armed_group_off(
        burst, {}, observation=obs, members=members, current_entity_id=group
    )
    assert observer._armed_off_attempts[-1]["result"] == expected
    assert observer.runtime.engine.is_current_work(token)
    for _ in range(40):
        observer._trace_armed_off(burst, {}, obs, members, "test", "bounded")
    assert len(observer._armed_off_attempts) == 32
    copied = observer.off_attempt_diagnostics()
    copied["armed_group_off_attempts"][-1]["result"] = "modified"
    assert observer._armed_off_attempts[-1]["result"] == "bounded"
    assert observer.runtime.engine.is_current_work(token)



@pytest.mark.asyncio
async def test_armed_parent_defers_exact_child_until_parent_second_off_completes(tmp_path):
    """A nested aggregate from the same physical OFF burst cannot steal an armed parent."""

    from dataclasses import replace

    from homeassistant.core import State

    from custom_components.home_lighting_manager.attribution_correlation import (
        ExternalBurstTopology,
    )
    from custom_components.home_lighting_manager.intent_policy import (
        IntentAttributionSource,
        IntentEvidence,
        IntentEvidenceKind,
    )
    from custom_components.home_lighting_manager.promotion_observer import _PendingExternalLeaf
    from custom_components.home_lighting_manager.shadow import ShadowObservation

    hass = HomeAssistant(str(tmp_path))
    leaves = ("light.a", "light.b", "light.c")
    child_members = leaves[:2]
    parent, child = "light.parent", "light.child"
    observer = PromotingHomeAssistantShadowObserver(hass, [parent, child, *leaves])
    observer._topology_members = {parent: leaves, child: child_members}
    observer._external_group_burst_topology = dict(observer._topology_members)
    observer.runtime.engine.apply_group_off(parent, leaves)

    evidence = IntentEvidence(
        IntentEvidenceKind.UNKNOWN,
        attribution_source=IntentAttributionSource.UNATTRIBUTED_EXTERNAL,
    )
    now = dt_util.now()
    for sequence, entity_id in enumerate(child_members, 10):
        observation = ShadowObservation(
            entity_id,
            evidence,
            operation="off",
            sequence=sequence,
            generation=observer.runtime.engine.generation,
        )
        state = State(entity_id, "off")
        observer._pending_external_leaves[entity_id] = _PendingExternalLeaf(
            now, observation, state
        )
        hass.states.async_set(entity_id, "off")

    child_burst = {
        "topology": ExternalBurstTopology.MULTI_LEAF_WITH_AGGREGATE_PROPAGATION.value,
        "leaf_entities": list(child_members),
        "aggregate_entities": [child],
    }
    child_candidate = {}
    callbacks = []

    def fake_call_later(_hass, _delay, callback):
        callbacks.append(callback)
        return lambda: None

    with patch(
        "custom_components.home_lighting_manager.promotion_observer.async_call_later",
        side_effect=fake_call_later,
    ):
        observer._promote_exact_group_candidate(
            child_burst,
            child_candidate,
            current_entity_id=child,
            burst_topology=observer._external_group_burst_topology,
        )

    assert child_candidate["promotion_reason"] == f"awaiting armed overlapping group {parent}"
    assert parent in observer.runtime.engine.group_off_sequences()
    assert child not in observer.runtime.engine.group_off_sequences()
    assert len(observer._pending_overlapping_group_promotions) == 1
    assert len(callbacks) == 1

    entity_id = leaves[-1]
    observation = ShadowObservation(
        entity_id,
        evidence,
        operation="off",
        sequence=12,
        generation=observer.runtime.engine.generation,
    )
    observer._pending_external_leaves[entity_id] = _PendingExternalLeaf(
        now, observation, State(entity_id, "off")
    )
    hass.states.async_set(entity_id, "off")

    parent_observation = replace(
        observation,
        entity_id=parent,
        sequence=13,
    )
    parent_burst = {
        "leaf_entities": list(leaves),
        "aggregate_entities": [parent, child],
    }
    parent_candidate = {}

    with patch.object(observer.hass, "async_create_task"):
        assert observer._promote_armed_group_off(
            parent_burst,
            parent_candidate,
            observation=parent_observation,
            members=leaves,
            current_entity_id=parent,
        )

    assert parent_candidate["promotion_reason"] == "created_group_manual_off"
    assert not observer._pending_overlapping_group_promotions
    assert all(
        observer.runtime.engine.resolve(entity_id).layer.kind is LayerKind.MANUAL_OFF
        for entity_id in leaves
    )
    assert all(
        observer.runtime.engine.resolve(entity_id).layer.group_id == parent
        for entity_id in leaves
    )
    assert not observer.runtime.engine.group_off_changes()

    # Even if a cancelled HA timer races and invokes its callback, the consumed
    # deferred child no longer has permission to mutate ownership.
    with patch.object(observer.hass, "async_create_task"):
        callbacks[0](dt_util.now())
    assert observer.runtime.operations.latest_homeowner["group_id"] == parent
    assert observer.runtime.operations.latest_homeowner["reason"] == "created_group_manual_off"


@pytest.mark.asyncio
async def test_genuine_child_off_resolves_after_armed_parent_correlation_window(tmp_path):
    """If the parent never completes, the deferred child remains valid later intent."""

    from homeassistant.core import State

    from custom_components.home_lighting_manager.attribution_correlation import (
        ExternalBurstTopology,
    )
    from custom_components.home_lighting_manager.intent_policy import (
        IntentAttributionSource,
        IntentEvidence,
        IntentEvidenceKind,
    )
    from custom_components.home_lighting_manager.promotion_observer import _PendingExternalLeaf
    from custom_components.home_lighting_manager.shadow import ShadowObservation

    hass = HomeAssistant(str(tmp_path))
    leaves = ("light.a", "light.b", "light.c")
    child_members = leaves[:2]
    parent, child = "light.parent", "light.child"
    observer = PromotingHomeAssistantShadowObserver(hass, [parent, child, *leaves])
    observer._topology_members = {parent: leaves, child: child_members}
    observer._external_group_burst_topology = dict(observer._topology_members)
    observer.runtime.engine.apply_group_off(parent, leaves)

    evidence = IntentEvidence(
        IntentEvidenceKind.UNKNOWN,
        attribution_source=IntentAttributionSource.UNATTRIBUTED_EXTERNAL,
    )
    now = dt_util.now()
    for sequence, entity_id in enumerate(child_members, 20):
        observation = ShadowObservation(
            entity_id,
            evidence,
            operation="off",
            sequence=sequence,
            generation=observer.runtime.engine.generation,
        )
        observer._pending_external_leaves[entity_id] = _PendingExternalLeaf(
            now, observation, State(entity_id, "off")
        )
        hass.states.async_set(entity_id, "off")

    burst = {
        "topology": ExternalBurstTopology.MULTI_LEAF_WITH_AGGREGATE_PROPAGATION.value,
        "leaf_entities": list(child_members),
        "aggregate_entities": [child],
    }
    candidate = {}
    callbacks = []

    def fake_call_later(_hass, _delay, callback):
        callbacks.append(callback)
        return lambda: None

    with patch(
        "custom_components.home_lighting_manager.promotion_observer.async_call_later",
        side_effect=fake_call_later,
    ):
        observer._promote_exact_group_candidate(
            burst,
            candidate,
            current_entity_id=child,
            burst_topology=observer._external_group_burst_topology,
        )

    assert candidate["promotion_reason"] == f"awaiting armed overlapping group {parent}"
    assert parent in observer.runtime.engine.group_off_sequences()
    assert child not in observer.runtime.engine.group_off_sequences()

    with patch.object(observer.hass, "async_create_task"):
        callbacks[0](dt_util.now())

    latest = observer.runtime.operations.latest_homeowner
    assert latest["group_id"] == child
    assert latest["reason"] == "released_to_hlm"
    assert child in observer.runtime.engine.group_off_sequences()
    assert parent not in observer.runtime.engine.group_off_sequences()
    removals = observer.runtime.engine.group_off_changes()
    assert removals[-1]["group_id"] == parent
    assert removals[-1]["reason"] == "apply_group_off:overlap"
    assert removals[-1]["trigger_group_id"] == child
