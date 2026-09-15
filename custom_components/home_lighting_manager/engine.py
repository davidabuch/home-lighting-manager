"""Deterministic, side-effect-free ownership primitives.

This is a pure ownership engine. It deliberately does not listen to Home Assistant events,
persist state, or call services.
"""

from __future__ import annotations

from dataclasses import replace

from .model import (
    Appearance,
    FamilySession,
    GroupOffAction,
    GroupOffResult,
    LayerKind,
    OffAction,
    OffResult,
    OwnershipLayer,
    ResolvedEntity,
)

NIGHTLY_BOUNDARY = "nightly_0159"


class OwnershipEngine:
    """In-memory per-entity ownership state for contract tests and later adapters."""

    def __init__(self, generation: int = 1) -> None:
        self.generation = generation
        self._layers: dict[str, list[OwnershipLayer]] = {}
        self._families: dict[tuple[str, str], FamilySession] = {}
        self._group_off_armed: dict[str, frozenset[str]] = {}
        self._order = 0

    def next_generation(self) -> int:
        """Invalidate old planned work by establishing a newer authority generation."""
        self.generation += 1
        self._group_off_armed.clear()
        return self.generation

    def is_current_generation(self, generation: int) -> bool:
        """Return whether work belongs to the current authority generation."""
        return generation == self.generation

    def layers(self, entity_id: str) -> tuple[OwnershipLayer, ...]:
        """Return layers in creation order for inspection/tests."""
        return tuple(self._layers.get(entity_id, ()))

    def push(self, entity_id: str, layer: OwnershipLayer) -> OwnershipLayer:
        """Add a layer after assigning deterministic creation order."""
        if layer.generation != self.generation:
            raise ValueError("cannot push a stale-generation ownership layer")
        self._order += 1
        stored = replace(layer, order=self._order)
        self._layers.setdefault(entity_id, []).append(stored)
        return stored

    def remove_layer(self, entity_id: str, layer_id: str) -> None:
        """Remove one layer without disturbing valid layers underneath it."""
        current = self._layers.get(entity_id, [])
        remaining = [layer for layer in current if layer.layer_id != layer_id]
        if remaining:
            self._layers[entity_id] = remaining
        else:
            self._layers.pop(entity_id, None)

    def remove_owner(self, entity_id: str, owner: str) -> None:
        """Remove every layer for one owner on an entity."""
        current = self._layers.get(entity_id, [])
        remaining = [layer for layer in current if layer.owner != owner]
        if remaining:
            self._layers[entity_id] = remaining
        else:
            self._layers.pop(entity_id, None)

    def remove_homeowner_exceptions(self, entity_id: str) -> None:
        """Release Manual and Manual-OFF layers while preserving HLM/system layers."""
        current = self._layers.get(entity_id, [])
        remaining = [
            layer
            for layer in current
            if layer.kind not in (LayerKind.MANUAL, LayerKind.MANUAL_OFF)
        ]
        if remaining:
            self._layers[entity_id] = remaining
        else:
            self._layers.pop(entity_id, None)

    def expire_boundary(self, boundary: str) -> None:
        """Expire layers whose lifecycle explicitly ends at a named boundary."""
        for entity_id in list(self._layers):
            remaining = [
                layer
                for layer in self._layers[entity_id]
                if layer.expires_at_boundary != boundary
            ]
            if remaining:
                self._layers[entity_id] = remaining
            else:
                self._layers.pop(entity_id, None)
        self._group_off_armed.clear()

    def resolve(self, entity_id: str) -> ResolvedEntity:
        """Resolve the highest-precedence eligible layer, using recency as the tie-breaker."""
        eligible = [
            layer
            for layer in self._layers.get(entity_id, ())
            if layer.generation == self.generation and self._layer_eligible(layer)
        ]
        winner = max(
            eligible,
            key=lambda item: (item.precedence, item.order),
            default=None,
        )
        return ResolvedEntity(entity_id=entity_id, layer=winner)

    def start_family(self, family: str, session_id: str) -> FamilySession:
        """Start or retrieve an independent family session in the current generation."""
        key = (family, session_id)
        existing = self._families.get(key)
        if existing is not None and existing.generation == self.generation:
            return existing
        session = FamilySession(family=family, session_id=session_id, generation=self.generation)
        self._families[key] = session
        return session

    def suppress_family(self, family: str, session_id: str, reason: str) -> FamilySession:
        """Suppress a parent family and all child effects for one session."""
        current = self.start_family(family, session_id)
        suppressed = replace(current, suppressed=True, suppression_reason=reason)
        self._families[(family, session_id)] = suppressed
        return suppressed

    def family_eligible(self, family: str, session_id: str, generation: int | None = None) -> bool:
        """Return whether parent/child work for a family session is currently eligible."""
        expected_generation = self.generation if generation is None else generation
        if expected_generation != self.generation:
            return False
        session = self._families.get((family, session_id))
        return (
            session is not None
            and session.generation == self.generation
            and not session.suppressed
        )

    def push_manual_off(
        self,
        entity_id: str,
        *,
        expires_at_boundary: str = NIGHTLY_BOUNDARY,
        precedence: int | None = None,
        layer_id: str | None = None,
    ) -> OwnershipLayer:
        """Create explicit Manual-OFF for one entity and expose it above current ownership."""
        current = self.resolve(entity_id).layer
        effective_precedence = 0 if current is None else current.precedence
        if precedence is not None:
            effective_precedence = precedence
        self.remove_homeowner_exceptions(entity_id)
        return self.push(
            entity_id,
            OwnershipLayer(
                layer_id=layer_id or self._new_layer_id("manual-off", entity_id),
                owner="manual",
                kind=LayerKind.MANUAL_OFF,
                generation=self.generation,
                order=0,
                appearance=Appearance(on=False),
                expires_at_boundary=expires_at_boundary,
                precedence=effective_precedence,
            ),
        )

    def manual_release(self, entity_id: str) -> bool:
        """Release the currently exposed Manual appearance layer, if present."""
        resolved = self.resolve(entity_id).layer
        if resolved is None or resolved.kind is not LayerKind.MANUAL:
            return False
        self.remove_layer(entity_id, resolved.layer_id)
        return True

    def apply_off(
        self,
        entity_id: str,
        *,
        manual_off_precedence: int | None = None,
        expires_at_boundary: str = NIGHTLY_BOUNDARY,
    ) -> OffResult:
        """Interpret one deliberate entity OFF using the approved peel/dismiss semantics.

        - exposed Manual appearance -> release it and reveal the next valid layer;
        - exposed session-family layer -> suppress that family for the session;
        - exposed Manual-OFF -> keep the existing Manual-OFF;
        - otherwise -> create explicit Manual-OFF above the currently exposed HLM state.
        """
        previous = self.resolve(entity_id).layer

        if previous is not None and previous.kind is LayerKind.MANUAL:
            self.remove_layer(entity_id, previous.layer_id)
            action = OffAction.RELEASED_MANUAL
        elif previous is not None and previous.family is not None and previous.session_id is not None:
            self.suppress_family(previous.family, previous.session_id, "homeowner_off")
            action = OffAction.SUPPRESSED_FAMILY
        elif previous is not None and previous.kind is LayerKind.MANUAL_OFF:
            action = OffAction.ALREADY_MANUAL_OFF
        else:
            self.push_manual_off(
                entity_id,
                expires_at_boundary=expires_at_boundary,
                precedence=manual_off_precedence,
            )
            action = OffAction.CREATED_MANUAL_OFF

        return OffResult(
            entity_id=entity_id,
            action=action,
            previous_layer=previous,
            effective_layer=self.resolve(entity_id).layer,
        )

    def apply_group_off(
        self,
        group_id: str,
        entity_ids: tuple[str, ...] | list[str],
        *,
        manual_off_precedence: int | None = None,
        expires_at_boundary: str = NIGHTLY_BOUNDARY,
    ) -> GroupOffResult:
        """Implement the approved first/second group-OFF interaction.

        First OFF releases Manual exceptions for the exact commanded members and returns them to
        HLM/system ownership. A second OFF for the same group/member set creates Manual-OFF on all
        commanded members.
        """
        members = tuple(dict.fromkeys(entity_ids))
        if not members:
            raise ValueError("group OFF requires at least one entity")

        member_set = frozenset(members)
        if self._group_off_armed.get(group_id) == member_set:
            for entity_id in members:
                self.push_manual_off(
                    entity_id,
                    expires_at_boundary=expires_at_boundary,
                    precedence=manual_off_precedence,
                    layer_id=self._new_layer_id(f"group-manual-off:{group_id}", entity_id),
                )
            self._group_off_armed.pop(group_id, None)
            action = GroupOffAction.CREATED_GROUP_MANUAL_OFF
        else:
            for entity_id in members:
                self.remove_homeowner_exceptions(entity_id)
            self._group_off_armed[group_id] = member_set
            action = GroupOffAction.RELEASED_TO_HLM

        return GroupOffResult(group_id=group_id, action=action, entity_ids=members)

    def reset_group_off_sequence(self, group_id: str | None = None) -> None:
        """Invalidate a pending second group-OFF after intervening meaningful intent."""
        if group_id is None:
            self._group_off_armed.clear()
        else:
            self._group_off_armed.pop(group_id, None)

    def _new_layer_id(self, prefix: str, entity_id: str) -> str:
        return f"{prefix}:{self.generation}:{self._order + 1}:{entity_id}"

    def _layer_eligible(self, layer: OwnershipLayer) -> bool:
        if layer.family is None:
            return True
        if layer.session_id is None:
            return False
        return self.family_eligible(layer.family, layer.session_id, generation=layer.generation)
