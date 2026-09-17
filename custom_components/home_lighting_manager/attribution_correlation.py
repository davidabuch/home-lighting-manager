"""Pure correlation primitives for unattributed external lighting observations.

This module is diagnostic only. It groups nearby external state updates into a
short burst and describes the observed leaf/aggregate topology. It must not
promote an observation to homeowner intent or command any Home Assistant entity.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum


class ExternalBurstTopology(StrEnum):
    """Observed topology for one short unattributed-external event burst."""

    ISOLATED_LEAF = "isolated_leaf"
    LEAF_WITH_AGGREGATE_PROPAGATION = "leaf_with_aggregate_propagation"
    MULTI_LEAF_WITH_AGGREGATE_PROPAGATION = "multi_leaf_with_aggregate_propagation"
    MULTI_LEAF_CHURN = "multi_leaf_churn"
    AGGREGATE_ONLY = "aggregate_only"
    MIXED_UNRESOLVED = "mixed_unresolved"


@dataclass(frozen=True)
class ExternalTopologyEvent:
    """One light observation reduced to topology-relevant evidence."""

    timestamp: datetime
    entity_id: str
    member_entity_ids: tuple[str, ...] = ()

    @property
    def is_aggregate(self) -> bool:
        return bool(self.member_entity_ids)


@dataclass(frozen=True)
class ExternalBurstSummary:
    """Current summary for the active short external-observation burst."""

    started_at: datetime
    updated_at: datetime
    topology: ExternalBurstTopology
    event_count: int
    unique_entity_count: int
    leaf_entities: tuple[str, ...]
    aggregate_entities: tuple[str, ...]
    propagated_leaf_entities: tuple[str, ...]

    def external_intent_candidate(self) -> dict[str, object]:
        """Describe whether topology is strong enough for a shadow intent candidate.

        This is deliberately diagnostic only. A qualified candidate is not
        homeowner intent and must not mutate ownership. It simply records that
        one context-less leaf change is corroborated by aggregate propagation.
        Multi-leaf qualification requires exact group topology and is resolved
        by the HA adapter against its live topology cache.
        """
        qualified = (
            self.topology is ExternalBurstTopology.LEAF_WITH_AGGREGATE_PROPAGATION
            and len(self.leaf_entities) == 1
            and self.propagated_leaf_entities == self.leaf_entities
        )
        return {
            "qualified": qualified,
            "entity_id": self.leaf_entities[0] if qualified else None,
            "basis": (
                "single_leaf_with_aggregate_propagation"
                if qualified
                else "insufficient_topology_evidence"
            ),
            "evidence_topology": self.topology.value,
            "promoted_to_homeowner": False,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "topology": self.topology.value,
            "event_count": self.event_count,
            "unique_entity_count": self.unique_entity_count,
            "leaf_entities": list(self.leaf_entities[:32]),
            "aggregate_entities": list(self.aggregate_entities[:32]),
            "propagated_leaf_entities": list(self.propagated_leaf_entities[:32]),
            "external_intent_candidate": self.external_intent_candidate(),
        }


def resolve_unique_exact_group(
    *,
    leaf_entities: tuple[str, ...],
    observed_aggregate_entities: tuple[str, ...],
    topology_members: Mapping[str, tuple[str, ...]],
) -> str | None:
    """Return one uniquely identifiable exact aggregate, otherwise fail closed.

    Exact means direct membership equals the observed leaf set. Uniqueness is
    evaluated across the complete known topology, not only aggregates that emitted
    state during the burst. The winning aggregate must also have appeared in the
    burst. This prevents a nested/larger aggregate or duplicate exact groups from
    being guessed as homeowner scope.
    """
    if len(leaf_entities) < 2:
        return None
    leaf_set = frozenset(leaf_entities)
    exact = sorted(
        aggregate
        for aggregate, members in topology_members.items()
        if len(members) == len(leaf_set) and frozenset(members) == leaf_set
    )
    if len(exact) != 1:
        return None
    return exact[0] if exact[0] in set(observed_aggregate_entities) else None


class ExternalBurstCorrelator:
    """Group nearby external light updates and describe their topology.

    The correlator deliberately makes no ownership decision. A caller may use
    the summary for commissioning and later policy research, but ambiguity still
    belongs to HLM until a separate policy explicitly establishes otherwise.
    """

    def __init__(self, *, window_seconds: float = 2.0) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.window_seconds = float(window_seconds)
        self._events: list[ExternalTopologyEvent] = []
        self._last_timestamp: datetime | None = None
        self._overflow = False

    def observe(self, event: ExternalTopologyEvent) -> ExternalBurstSummary:
        if self._events:
            delta = (event.timestamp - self._last_timestamp).total_seconds()
            if delta < 0 or delta > self.window_seconds:
                self._events.clear()
                self._overflow = False
        self._last_timestamp = event.timestamp
        if len(self._events) >= 128 or len(event.member_entity_ids) > 256:
            self._overflow = True
        if len(self._events) < 128:
            self._events.append(replace(event, member_entity_ids=event.member_entity_ids[:256]))
        return self.summary()

    def summary(self) -> ExternalBurstSummary:
        if not self._events:
            raise ValueError("no external events have been observed")

        leaves = sorted({event.entity_id for event in self._events if not event.is_aggregate})
        aggregates = sorted({event.entity_id for event in self._events if event.is_aggregate})
        aggregate_members = {
            member
            for event in self._events
            if event.is_aggregate
            for member in event.member_entity_ids
        }
        propagated = sorted(set(leaves) & aggregate_members)

        if self._overflow:
            topology = ExternalBurstTopology.MIXED_UNRESOLVED
        elif leaves and not aggregates:
            topology = (
                ExternalBurstTopology.ISOLATED_LEAF
                if len(leaves) == 1
                else ExternalBurstTopology.MULTI_LEAF_CHURN
            )
        elif aggregates and not leaves:
            topology = ExternalBurstTopology.AGGREGATE_ONLY
        elif len(leaves) == 1 and propagated == leaves:
            topology = ExternalBurstTopology.LEAF_WITH_AGGREGATE_PROPAGATION
        elif len(leaves) > 1 and set(propagated) == set(leaves):
            topology = ExternalBurstTopology.MULTI_LEAF_WITH_AGGREGATE_PROPAGATION
        else:
            topology = ExternalBurstTopology.MIXED_UNRESOLVED

        return ExternalBurstSummary(
            started_at=self._events[0].timestamp,
            updated_at=self._events[-1].timestamp,
            topology=topology,
            event_count=len(self._events),
            unique_entity_count=len({event.entity_id for event in self._events}),
            leaf_entities=tuple(leaves),
            aggregate_entities=tuple(aggregates),
            propagated_leaf_entities=tuple(propagated),
        )
