"""Deterministic, side-effect-free ownership primitives.

This is a Phase 1 foundation, not a production Home Assistant controller. It deliberately does not
listen to events, persist state, or call services.
"""

from __future__ import annotations

from dataclasses import replace

from .model import FamilySession, LayerKind, OwnershipLayer, ResolvedEntity


class OwnershipEngine:
    """In-memory per-entity ownership state for contract tests and later adapters."""

    def __init__(self, generation: int = 1) -> None:
        self.generation = generation
        self._layers: dict[str, list[OwnershipLayer]] = {}
        self._families: dict[tuple[str, str], FamilySession] = {}
        self._order = 0

    def next_generation(self) -> int:
        """Invalidate old planned work by establishing a newer authority generation."""
        self.generation += 1
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

    def resolve(self, entity_id: str) -> ResolvedEntity:
        """Resolve the newest eligible layer for one entity.

        Later policy work may add explicit structural precedence. Phase 1 intentionally models the
        contract's recency/layer exposure semantics while excluding suppressed automation families.
        """
        eligible = [
            layer
            for layer in self._layers.get(entity_id, ())
            if layer.generation == self.generation and self._layer_eligible(layer)
        ]
        winner = max(eligible, key=lambda item: item.order, default=None)
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

    def manual_release(self, entity_id: str) -> bool:
        """Release the currently exposed Manual appearance layer, if present.

        This is the first-OFF primitive for an exposed Manual appearance. It deliberately does not
        create Manual-OFF; the caller can interpret a subsequent deliberate OFF after HLM has been
        re-exposed as explicit Manual-OFF.
        """
        resolved = self.resolve(entity_id).layer
        if resolved is None or resolved.kind is not LayerKind.MANUAL:
            return False
        self.remove_layer(entity_id, resolved.layer_id)
        return True

    def _layer_eligible(self, layer: OwnershipLayer) -> bool:
        if layer.family is None:
            return True
        if layer.session_id is None:
            return False
        return self.family_eligible(layer.family, layer.session_id, generation=layer.generation)
