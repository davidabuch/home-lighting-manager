"""Tests for diagnostic external-event topology correlation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.home_lighting_manager.attribution_correlation import (
    ExternalBurstCorrelator,
    ExternalBurstTopology,
    ExternalTopologyEvent,
)


def _at(second: float) -> datetime:
    return datetime(2026, 9, 15, 22, 0, tzinfo=timezone.utc) + timedelta(seconds=second)


def test_single_leaf_starts_as_isolated_leaf():
    correlator = ExternalBurstCorrelator()
    result = correlator.observe(ExternalTopologyEvent(_at(0), "light.path_1"))
    assert result.topology is ExternalBurstTopology.ISOLATED_LEAF
    assert result.leaf_entities == ("light.path_1",)


def test_leaf_followed_by_parent_aggregate_is_propagation():
    correlator = ExternalBurstCorrelator()
    correlator.observe(ExternalTopologyEvent(_at(0), "light.path_1"))
    result = correlator.observe(
        ExternalTopologyEvent(
            _at(0.2),
            "light.path_group",
            ("light.path_1", "light.path_2"),
        )
    )
    assert result.topology is ExternalBurstTopology.LEAF_WITH_AGGREGATE_PROPAGATION
    assert result.propagated_leaf_entities == ("light.path_1",)


def test_single_leaf_propagation_qualifies_shadow_external_intent_candidate():
    correlator = ExternalBurstCorrelator()
    correlator.observe(ExternalTopologyEvent(_at(0), "light.path_1"))
    result = correlator.observe(
        ExternalTopologyEvent(
            _at(0.2),
            "light.path_group",
            ("light.path_1", "light.path_2"),
        )
    )

    candidate = result.as_dict()["external_intent_candidate"]
    assert candidate == {
        "qualified": True,
        "entity_id": "light.path_1",
        "basis": "single_leaf_with_aggregate_propagation",
        "evidence_topology": "leaf_with_aggregate_propagation",
        "promoted_to_homeowner": False,
    }


def test_isolated_leaf_does_not_qualify_shadow_external_intent_candidate():
    correlator = ExternalBurstCorrelator()
    result = correlator.observe(ExternalTopologyEvent(_at(0), "light.path_1"))

    candidate = result.as_dict()["external_intent_candidate"]
    assert candidate["qualified"] is False
    assert candidate["entity_id"] is None
    assert candidate["promoted_to_homeowner"] is False


def test_multiple_leafs_contained_by_aggregate_are_multi_leaf_propagation():
    correlator = ExternalBurstCorrelator()
    correlator.observe(ExternalTopologyEvent(_at(0), "light.path_1"))
    correlator.observe(ExternalTopologyEvent(_at(0.1), "light.path_2"))
    result = correlator.observe(
        ExternalTopologyEvent(
            _at(0.2),
            "light.path_group",
            ("light.path_1", "light.path_2"),
        )
    )
    assert result.topology is ExternalBurstTopology.MULTI_LEAF_WITH_AGGREGATE_PROPAGATION
    assert result.as_dict()["external_intent_candidate"]["qualified"] is False


def test_multiple_leafs_without_aggregate_are_churn():
    correlator = ExternalBurstCorrelator()
    correlator.observe(ExternalTopologyEvent(_at(0), "light.path_1"))
    result = correlator.observe(ExternalTopologyEvent(_at(0.1), "light.path_2"))
    assert result.topology is ExternalBurstTopology.MULTI_LEAF_CHURN


def test_aggregate_only_burst_is_reported():
    correlator = ExternalBurstCorrelator()
    result = correlator.observe(
        ExternalTopologyEvent(
            _at(0),
            "light.path_group",
            ("light.path_1", "light.path_2"),
        )
    )
    assert result.topology is ExternalBurstTopology.AGGREGATE_ONLY
    assert result.as_dict()["external_intent_candidate"]["qualified"] is False


def test_gap_starts_new_burst():
    correlator = ExternalBurstCorrelator(window_seconds=2.0)
    correlator.observe(ExternalTopologyEvent(_at(0), "light.path_1"))
    result = correlator.observe(ExternalTopologyEvent(_at(3), "light.path_2"))
    assert result.topology is ExternalBurstTopology.ISOLATED_LEAF
    assert result.event_count == 1
    assert result.leaf_entities == ("light.path_2",)


def test_mixed_membership_stays_unresolved():
    correlator = ExternalBurstCorrelator()
    correlator.observe(ExternalTopologyEvent(_at(0), "light.path_1"))
    correlator.observe(ExternalTopologyEvent(_at(0.1), "light.other"))
    result = correlator.observe(
        ExternalTopologyEvent(_at(0.2), "light.path_group", ("light.path_1",))
    )
    assert result.topology is ExternalBurstTopology.MIXED_UNRESOLVED
    assert result.as_dict()["external_intent_candidate"]["qualified"] is False
