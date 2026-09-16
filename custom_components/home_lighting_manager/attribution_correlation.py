"""Pure correlation primitives for unattributed external lighting observations.

This module is diagnostic only. It groups nearby external state updates into a
short burst and describes the observed leaf/aggregate topology. It must not
promote an observation to homeowner intent or command any Home Assistant entity.
"""

from __future__ import annotations

from dataclasses import dataclass
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

    def as_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "topology": self.topology.value,
            "event_count": self.event_count,
            "unique_entity_count": self.unique_entity_count,
            "leaf_entities": list(self.leaf_entities),
            "aggregate_entities": list(self.aggregate_entities),
            "propagated_leaf_entities": list(self.propagated_leaf_entities),
        }


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

    def observe(self, event: ExternalTopologyEvent) -> ExternalBurstSummary:
        if self._events:
            delta = (event.timestamp - self._events[-1].timestamp).total_seconds()
            if delta < 0 or delta > self.window_seconds:
                self._events.clear()
        self._events.append(event)
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

        if leaves and not aggregates:
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
