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


@dataclass(frozen=True)
class Appearance:
    """Desired visible appearance owned by one layer."""

    on: bool
    brightness: int | None = None
    color_temp_kelvin: int | None = None
    xy_color: tuple[float, float] | None = None
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
class ResolvedEntity:
    """Effective ownership result for one managed entity."""

    entity_id: str
    layer: OwnershipLayer | None

    @property
    def appearance(self) -> Appearance | None:
        return None if self.layer is None else self.layer.appearance
