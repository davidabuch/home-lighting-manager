"""Pure ownership-domain types for Home Lighting Manager.

This module intentionally imports no Home Assistant APIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
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
    POPPED_LAYER = "popped_layer"


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
    color_mode: str | None = None
    scene_evidence: str | None = None

    def is_valid(self) -> bool:
        """Validate native representations without guessing or converting color models."""
        if type(self.on) is not bool:
            return False
        if self.brightness is not None and (
            type(self.brightness) is not int or not 0 <= self.brightness <= 255
        ):
            return False
        if self.color_temp_kelvin is not None and (
            type(self.color_temp_kelvin) is not int or self.color_temp_kelvin <= 0
        ):
            return False
        for values, bounds, integral in (
            (self.xy_color, ((0, 1), (0, 1)), False),
            (self.rgb_color, ((0, 255),) * 3, True),
            (self.hs_color, ((0, 360), (0, 100)), False),
        ):
            if values is None:
                continue
            if not isinstance(values, tuple) or len(values) != len(bounds):
                return False
            for value, (low, high) in zip(values, bounds, strict=True):
                if type(value) not in ((int,) if integral else (int, float)):
                    return False
                if not low <= value <= high or not isfinite(value):
                    return False
        if self.color_mode is not None and not isinstance(self.color_mode, str):
            return False
        if self.color_mode not in (None, "onoff", "brightness", "xy", "rgb", "hs", "color_temp"):
            return False
        if self.color_mode == "brightness" and self.brightness is None:
            return False
        native_color = {
            "xy": self.xy_color,
            "rgb": self.rgb_color,
            "hs": self.hs_color,
            "color_temp": self.color_temp_kelvin,
        }
        if self.color_mode in native_color and native_color[self.color_mode] is None:
            return False
        return all(
            value is None or (isinstance(value, str) and 0 < len(value) <= 256)
            for value in (self.effect, self.scene_id, self.color_mode, self.scene_evidence)
        )


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
    operation_id: str | None = None
    group_id: str | None = None
    parent_layer_id: str | None = None
    # Explicit system policy. None selects the transitional numeric-admission adapter.
    system_priority: int | None = None
    protects_from_manual: bool = False
    removable: bool = False
    session_sequence: int | None = None


@dataclass(frozen=True)
class FamilySession:
    """Session-scoped eligibility for an automation family."""

    family: str
    session_id: str
    generation: int
    suppressed: bool = False
    suppression_reason: str | None = None
    sequence: int = 0


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


@dataclass(frozen=True)
class WorkToken:
    """A calculation is current only in the same configuration AND runtime revision."""

    generation: int
    revision: int
    authority_id: str = ""
