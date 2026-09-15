"""Pure ownership-domain types for Home Lighting Manager.

This module intentionally imports no Home Assistant APIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class LayerKind(StrEnum):
    """Semantic ownership-layer kinds."""

    AUTOMATIC = "automatic"
    MANUAL = "manual"
    MANUAL_OFF = "manual_off"
    FUNCTIONAL = "functional"
    OVERLAY = "overlay"


class OffAction(StrEnum):
    """Ownership mutation produced by an explicit homeowner OFF."""

    RELEASED_MANUAL = "released_manual"
    SUPPRESSED_FAMILY = "suppressed_family"
    CREATED_MANUAL_OFF = "created_manual_off"
    ALREADY_MANUAL_OFF = "already_manual_off"


class GroupOffAction(StrEnum):
    """Phase of the approved two-step group-OFF interaction."""

    RELEASED_TO_HLM = "released_to_hlm"
    CREATED_GROUP_MANUAL_OFF = "created_group_manual_off"


@dataclass(frozen=True)
class Appearance:
    """Desired visible appearance owned by one layer."""

    on: bool
    brightness: int | None = None
    color_temp_kelvin: int | None = None
    xy_color: tuple[float, float] | None = None
    rgb_color: tuple[int, int, int] | None = None
    hs_color: tuple[float, float] | None = None
    effect: str | None = None
    scene_id: str | None = None


@dataclass(frozen=True)
class OwnershipLayer:
    """One valid ownership layer for one managed entity."""

    layer_id: str
    owner: str
    kind: LayerKind
    generation: int
    order: int
    appearance: Appearance | None = None
    family: str | None = None
    session_id: str | None = None
    expires_at_boundary: str | None = None
    precedence: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FamilySession:
    """Session-scoped eligibility for an automation family."""

    family: str
    session_id: str
    generation: int
    suppressed: bool = False
    suppression_reason: str | None = None


@dataclass(frozen=True)
class OffResult:
    """Result of interpreting one explicit entity OFF command."""

    entity_id: str
    action: OffAction
    previous_layer: OwnershipLayer | None
    effective_layer: OwnershipLayer | None


@dataclass(frozen=True)
class GroupOffResult:
    """Result of interpreting one explicit group OFF command."""

    group_id: str
    action: GroupOffAction
    entity_ids: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedEntity:
    """Effective ownership result for one managed entity."""

    entity_id: str
    layer: OwnershipLayer | None

    @property
    def appearance(self) -> Appearance | None:
        return None if self.layer is None else self.layer.appearance
