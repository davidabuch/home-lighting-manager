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
                # Controlled alternate cause: guard can be ON at a leaf, OFF at aggregate.
                # This is a diagnostic test, NOT an assertion that it happened in the house.
                hass.states.async_set(guard, "on" if filtered_leaf and n == count - 1 else "off")
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
