"""Deterministic, side-effect-free ownership primitives.

This is a pure ownership engine. It deliberately does not listen to Home Assistant events,
persist state, or call services.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

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
    WorkToken,
)

NIGHTLY_BOUNDARY = "nightly_0159"
MAX_ENTITIES = 256
MAX_LAYERS = 32
MAX_FAMILIES = 64
MAX_GROUPS = 64


class OwnershipEngine:
    """In-memory per-entity ownership state for contract tests and later adapters."""

    def __init__(self, generation: int = 1, managed_entities: frozenset[str] | None = None) -> None:
        self._authority_id = uuid4().hex
        self.generation = generation
        self.managed_entities = managed_entities
        self._layers: dict[str, list[OwnershipLayer]] = {}
        self._families: dict[tuple[str, str], FamilySession] = {}
        self._group_off_armed: dict[str, frozenset[str]] = {}
        self._order = 0
        self.revision = 0
        self.last_mutation_reason = "initialized"
        self._ended_sessions: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._session_sequences: dict[str, int] = {}
        self._session_ids: dict[str, str] = {}
        self._legacy_sessions_exhausted = False

    def next_generation(self) -> int:
        """Invalidate old planned work by establishing a newer authority generation."""
        self.generation += 1
        self._layers.clear()
        self._families.clear()
        self._ended_sessions.clear()
        self._session_sequences.clear()
        self._session_ids.clear()
        self._legacy_sessions_exhausted = False
        self._changed("configuration_generation_changed")
        self._group_off_armed.clear()
        return self.generation

    def is_current_generation(self, generation: int) -> bool:
        """Return whether work belongs to the current authority generation."""
        return generation == self.generation

    def layers(self, entity_id: str) -> tuple[OwnershipLayer, ...]:
        """Return defensive copies in admitted bottom-to-top exposure order."""
        return tuple(deepcopy(self._layers.get(entity_id, ())))

    def accepts_entity(self, entity_id: str) -> bool:
        return (
            isinstance(entity_id, str)
            and entity_id.startswith("light.")
            and len(entity_id) <= 256
            and (self.managed_entities is None or entity_id in self.managed_entities)
        )

    @staticmethod
    def _identity(layer: OwnershipLayer) -> tuple:
        if layer.kind in (LayerKind.MANUAL, LayerKind.MANUAL_OFF):
            return ("homeowner",)
        return (layer.owner, layer.kind, layer.family, layer.session_id, layer.parent_layer_id)

    def push(
        self, entity_id: str, layer: OwnershipLayer, *, expected: WorkToken | None = None
    ) -> OwnershipLayer:
        """Compatibility admission for already-classified layers; updates retain exposure.

        Numeric rank is used only when inserting a new legacy layer. Resolution itself traverses
        the admitted stack; refreshing an underlying desired appearance never raises that layer.
        """
        if expected is not None and not self.is_current_work(expected):
            raise ValueError("stale work token")
        if layer.generation != self.generation:
            raise ValueError("cannot push a stale-generation ownership layer")
        if not self.accepts_entity(entity_id):
            raise ValueError("entity is not managed in this configuration")
        if entity_id not in self._layers and len(self._layers) >= MAX_ENTITIES:
            raise ValueError("entity capacity exceeded")
        if layer.family and layer.session_id:
            session = self._families.get((layer.family, layer.session_id))
            if session is None:
                raise ValueError("layer requires an existing parent session")
            if (
                layer.session_sequence is not None
                and layer.session_sequence != session.sequence
            ):
                raise ValueError("stale layer session sequence")
            layer = replace(layer, session_sequence=session.sequence)
        items = self._layers.setdefault(entity_id, [])
        if any(item.layer_id == layer.layer_id and self._identity(item) != self._identity(layer)
               for item in items):
            raise ValueError("layer ID collision across logical owners")
        existing = next(
            (
                i
                for i, item in enumerate(items)
                if item.layer_id == layer.layer_id or self._identity(item) == self._identity(layer)
            ),
            None,
        )
        if existing is not None:
            old = items[existing]
            stored = replace(deepcopy(layer), layer_id=old.layer_id, order=old.order)
            if stored != old:
                items[existing] = stored
                self._changed("layer_updated")
            return deepcopy(stored)
        if len(items) >= MAX_LAYERS:
            raise ValueError("layer capacity exceeded")
        self._order += 1
        stored = replace(deepcopy(layer), order=self._order)
        index = next(
            (i for i, item in enumerate(items) if item.precedence > stored.precedence), len(items)
        )
        items.insert(index, stored)
        self._changed("layer_pushed")
        return deepcopy(stored)

    def admit_manual(self, entity_id: str, layer: OwnershipLayer) -> OwnershipLayer:
        """Explicit homeowner placement, independent of automatic priority numbers."""
        self.remove_homeowner_exceptions(entity_id)
        stored = self.push(entity_id, layer)
        items = self._layers[entity_id]
        items.remove(stored)
        index = next(
            (i for i, item in enumerate(items)
             if item.protects_from_manual and self.layer_eligible(entity_id, item)),
            len(items),
        )
        items.insert(index, stored)
        return deepcopy(stored)

    def remove_layer(self, entity_id: str, layer_id: str) -> None:
        """Remove one layer without disturbing valid layers underneath it."""
        current = self._layers.get(entity_id, [])
        remaining = [layer for layer in current if layer.layer_id != layer_id]
        if remaining:
            self._layers[entity_id] = remaining
        else:
            self._layers.pop(entity_id, None)
        if remaining != current:
            self._changed("remove_layer")

    def remove_owner(self, entity_id: str, owner: str) -> None:
        """Remove every layer for one owner on an entity."""
        current = self._layers.get(entity_id, [])
        remaining = [layer for layer in current if layer.owner != owner]
        if remaining:
            self._layers[entity_id] = remaining
        else:
            self._layers.pop(entity_id, None)
        if remaining != current:
            self._changed("remove_owner")

    def remove_homeowner_exceptions(self, entity_id: str) -> None:
        """Release Manual and Manual-OFF layers while preserving HLM/system layers."""
        current = self._layers.get(entity_id, [])
        remaining = [
            layer for layer in current if layer.kind not in (LayerKind.MANUAL, LayerKind.MANUAL_OFF)
        ]
        if remaining:
            self._layers[entity_id] = remaining
        else:
            self._layers.pop(entity_id, None)
        if remaining != current:
            self._changed("remove_homeowner_exceptions")

    def expire_boundary(self, boundary: str) -> None:
        """Expire layers whose lifecycle explicitly ends at a named boundary."""
        for entity_id in list(self._layers):
            remaining = [
                layer for layer in self._layers[entity_id] if layer.expires_at_boundary != boundary
            ]
            if remaining:
                self._layers[entity_id] = remaining
            else:
                self._layers.pop(entity_id, None)
        self._group_off_armed.clear()
        self._changed(f"boundary:{boundary}")

    def resolve(self, entity_id: str) -> ResolvedEntity:
        """Expose the last valid admitted layer; physical state is not an input."""
        eligible = [
            layer
            for layer in self._layers.get(entity_id, ())
            if layer.generation == self.generation and self._layer_eligible(layer, entity_id)
        ]
        winner = eligible[-1] if eligible else None
        return ResolvedEntity(entity_id=entity_id, layer=deepcopy(winner))

    def start_family(
        self,
        family: str,
        session_id: str,
        *,
        sequence: int | None = None,
        expected: WorkToken | None = None,
    ) -> FamilySession:
        """Start exact session; ordered receipts scale without retaining all historical IDs.

        Callers of the explicit API supply monotonic per-family sequence. Legacy callers may
        omit it until the bounded retired-ID window fills, after which admission fails closed.
        """
        if expected is not None and not self.is_current_work(expected):
            raise ValueError("stale work token")
        key = (family, session_id)
        existing = self._families.get(key)
        if existing is not None:
            if sequence is not None and sequence != self._session_sequences[family]:
                raise ValueError("session identity sequence mismatch")
            return existing
        if key in self._ended_sessions:
            raise ValueError("ended session cannot be restarted; use a new session ID")
        last = self._session_sequences.get(family, 0)
        if sequence is None:
            if self._legacy_sessions_exhausted:
                raise ValueError("explicit session sequence required after retirement window")
            sequence = last + 1
        if type(sequence) is not int or sequence <= last:
            raise ValueError("stale session sequence")
        if self._session_ids.get(family) == session_id:
            raise ValueError("ended session identity cannot be reused")
        if family not in self._session_sequences and len(self._session_sequences) >= MAX_FAMILIES:
            raise ValueError("family capacity exceeded")
        for old_family, old_id in tuple(self._families):
            if old_family == family:
                self.end_family(family, old_id)
        self._session_sequences[family] = sequence
        self._session_ids[family] = session_id
        session = FamilySession(family, session_id, self.generation, sequence=sequence)
        self._families[key] = session
        self._changed("family_started")
        return session

    def suppress_family(
        self, family: str, session_id: str, reason: str, *, expected: WorkToken | None = None
    ) -> FamilySession:
        """Suppress a parent family and all child effects for one session."""
        if expected is not None and not self.is_current_work(expected):
            raise ValueError("stale work token")
        current = self._families.get((family, session_id))
        if current is None:
            raise ValueError("suppression requires an existing parent session")
        suppressed = replace(current, suppressed=True, suppression_reason=reason)
        self._families[(family, session_id)] = suppressed
        if suppressed != current:
            self._changed(reason)
        return suppressed

    def family_eligible(self, family: str, session_id: str, generation: int | None = None) -> bool:
        """Return whether parent/child work for a family session is currently eligible."""
        expected_generation = self.generation if generation is None else generation
        if expected_generation != self.generation:
            return False
        session = self._families.get((family, session_id))
        return (
            session is not None and session.generation == self.generation and not session.suppressed
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
        return self.admit_manual(
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
        elif (
            previous is not None and previous.family is not None and previous.session_id is not None
        ):
            self.suppress_family(previous.family, previous.session_id, "homeowner_off")
            action = OffAction.SUPPRESSED_FAMILY
        elif previous is not None and previous.removable:
            self.remove_layer(entity_id, previous.layer_id)
            action = OffAction.POPPED_LAYER
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
        eligible_members: frozenset[str] | None = None,
        manual_off_precedence: int | None = None,
        expires_at_boundary: str = NIGHTLY_BOUNDARY,
    ) -> GroupOffResult:
        """Implement the approved first/second group-OFF interaction.

        First OFF releases Manual exceptions for the exact commanded members and returns them to
        HLM/system ownership. A second OFF for the same group/member set creates Manual-OFF on all
        commanded members.
        """
        members = tuple(sorted(set(entity_ids)))
        if not members:
            raise ValueError("group OFF requires at least one entity")

        member_set = frozenset(members)
        eligible = member_set if eligible_members is None else member_set & eligible_members
        if not eligible:
            return GroupOffResult(group_id, GroupOffAction.RELEASED_TO_HLM, ())
        # An overlapping group's newer operation invalidates its earlier two-OFF sequence.
        for other, targets in list(self._group_off_armed.items()):
            if other != group_id and targets & member_set:
                self._group_off_armed.pop(other)
        if self._group_off_armed.get(group_id) == member_set:
            exposed = [self.resolve(entity_id).layer for entity_id in sorted(eligible)]
            for layer in exposed:
                if layer is not None and layer.family and layer.session_id:
                    self.suppress_family(layer.family, layer.session_id, "homeowner_group_off")
            for entity_id in members:
                if entity_id not in eligible:
                    continue
                self.push_manual_off(
                    entity_id,
                    expires_at_boundary=expires_at_boundary,
                    precedence=manual_off_precedence,
                    layer_id=self._new_layer_id(f"group-manual-off:{group_id}", entity_id),
                )
            # Further OFFs remain explicit OFF until meaningful intervening intent.
            action = GroupOffAction.CREATED_GROUP_MANUAL_OFF
        else:
            for entity_id in members:
                if entity_id not in eligible:
                    continue
                self.remove_homeowner_exceptions(entity_id)
            if group_id not in self._group_off_armed and len(self._group_off_armed) >= MAX_GROUPS:
                self._group_off_armed.pop(next(iter(self._group_off_armed)))
            self._group_off_armed[group_id] = member_set
            action = GroupOffAction.RELEASED_TO_HLM

        self._changed(action.value)
        return GroupOffResult(
            group_id=group_id,
            action=action,
            entity_ids=tuple(item for item in members if item in eligible),
        )

    def reset_group_off_sequence(self, group_id: str | None = None) -> None:
        """Invalidate a pending second group-OFF after intervening meaningful intent."""
        if self._group_off_armed:
            self._changed("group_off_sequence_reset")
        if group_id is None:
            self._group_off_armed.clear()
        else:
            self._group_off_armed.pop(group_id, None)

    def _new_layer_id(self, prefix: str, entity_id: str) -> str:
        return f"{prefix}:{self.generation}:{self._order + 1}:{entity_id}"

    def _layer_eligible(self, layer: OwnershipLayer, entity_id: str) -> bool:
        if layer.kind is LayerKind.OVERLAY and layer.family and layer.parent_layer_id is None:
            return False
        if layer.parent_layer_id is not None:
            parent = next(
                (item for item in self.layers(entity_id) if item.layer_id == layer.parent_layer_id),
                None,
            )
            if (
                parent is None
                or parent.parent_layer_id is not None
                or parent.generation != self.generation
                or parent.family != layer.family
                or parent.session_id != layer.session_id
                or not self._layer_eligible(parent, entity_id)
            ):
                return False
        if layer.family is None:
            return True
        if layer.session_id is None:
            return False
        session = self._families.get((layer.family, layer.session_id))
        return (
            session is not None
            and layer.session_sequence == session.sequence
            and self.family_eligible(layer.family, layer.session_id, generation=layer.generation)
        )

    def _changed(self, reason: str) -> None:
        self.revision += 1
        self.last_mutation_reason = reason

    def invalidate_work(self, reason: str) -> None:
        """Invalidate calculations even when newer intent leaves the same exposed state."""
        self._changed(reason)

    def work_token(self) -> WorkToken:
        return WorkToken(self.generation, self.revision, self._authority_id)

    def is_current_work(self, token: WorkToken) -> bool:
        return token == self.work_token()

    def family_sessions(self) -> tuple[FamilySession, ...]:
        return tuple(self._families.values())

    def end_family(
        self, family: str, session_id: str, *, expected: WorkToken | None = None
    ) -> None:
        """End exact session, remove its layers/children, reject delayed session resurrection."""
        if expected is not None and not self.is_current_work(expected):
            raise ValueError("stale work token")
        if self._families.pop((family, session_id), None) is None:
            return  # Duplicate/stale end cannot disturb a newer session or group interaction.
        self._ended_sessions[(family, session_id)] = None
        if len(self._ended_sessions) > 128:
            self._ended_sessions.popitem(last=False)
            self._legacy_sessions_exhausted = True
        for entity_id in list(self._layers):
            for layer in self.layers(entity_id):
                if (layer.family, layer.session_id) == (family, session_id):
                    self.remove_layer(entity_id, layer.layer_id)
        self.reset_group_off_sequence()
        self._changed("family_ended")

    def activate_automatic(
        self,
        entity_id: str,
        layer: OwnershipLayer,
        *,
        supersede: bool = False,
        priority: int | None = None,
        protects_from_manual: bool = False,
        expected: WorkToken | None = None,
    ) -> OwnershipLayer:
        """Admit system priority separately from homeowner placement and exposure recency.

        Same logical owner/session updates retain their stack slot. New system layers respect
        higher automatic priorities even with supersession. No observed bulb state is consulted.
        """
        if layer.kind not in (LayerKind.AUTOMATIC, LayerKind.FUNCTIONAL, LayerKind.OVERLAY):
            raise ValueError("automatic activation requires a system layer")
        if expected is not None and not self.is_current_work(expected):
            raise ValueError("stale work token")
        if priority is not None and layer.family and layer.session_sequence is None:
            raise ValueError("explicit system admission requires a session sequence")
        items = self.layers(entity_id)
        old = next((item for item in items if self._identity(item) == self._identity(layer)), None)
        priority = layer.precedence if priority is None else priority
        admitted = replace(
            layer, system_priority=priority, protects_from_manual=protects_from_manual
        )
        if old is not None:
            if (old.system_priority, old.protects_from_manual) != (priority, protects_from_manual):
                raise ValueError("change admission policy through a new configuration generation")
            return self.push(entity_id, admitted, expected=expected)
        stored = self.push(entity_id, admitted, expected=expected)
        stack = self._layers[entity_id]
        stack.remove(stored)

        def blocks(item):
            if item.kind in (LayerKind.MANUAL, LayerKind.MANUAL_OFF):
                return not supersede
            other_priority = (
                item.system_priority if item.system_priority is not None else item.precedence
            )
            return other_priority > priority

        index = next((i for i, item in enumerate(stack) if blocks(item)), len(stack))
        stack.insert(index, stored)
        return deepcopy(stored)

    def evening_activation(self) -> None:
        """A new evening reclaims Manual-OFF but preserves valid daytime Manual appearance."""
        for entity_id in list(self._layers):
            for layer in self.layers(entity_id):
                if layer.kind is LayerKind.MANUAL_OFF:
                    self.remove_layer(entity_id, layer.layer_id)
        self.reset_group_off_sequence()
        self._changed("evening_activation")

    def reset_group_off_for_members(self, members: frozenset[str]) -> None:
        """Intervening successful intent disarms only overlapping group sequences."""
        for group_id, targets in list(self._group_off_armed.items()):
            if targets & members:
                self._group_off_armed.pop(group_id)
                self._changed("intervening_group_member_intent")

    def entity_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._layers))

    def layer_eligible(self, entity_id: str, layer: OwnershipLayer) -> bool:
        return layer.generation == self.generation and self._layer_eligible(layer, entity_id)

    def ended_family_sessions(self) -> tuple[tuple[str, str], ...]:
        """Bounded diagnostic identities, never executable work or recovery input."""
        return tuple(self._ended_sessions)[-16:]

    def group_off_sequences(self) -> dict[str, tuple[str, ...]]:
        """Current-runtime group-OFF arming; never durable Manual-OFF ownership."""
        return {group: tuple(sorted(members)) for group, members in self._group_off_armed.items()}

    def reconfigure(self, entity_ids: frozenset[str]) -> int:
        """Discard old scopes/authority; current truth must be reconstructed by the caller."""
        self.managed_entities = frozenset(entity_ids)
        return self.next_generation()
