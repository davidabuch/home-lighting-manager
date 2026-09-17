"""Persistence schema for shadow Home Lighting Manager ownership state.

Persisted data is evidence, not authority. Only homeowner-owned Manual/Manual-OFF layers and
suppressed automation-family sessions are serialized. Automatic, functional, and overlay layers
are intentionally excluded so they can be recomputed from current Home Assistant truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .model import Appearance, FamilySession, LayerKind, OwnershipLayer

STORAGE_VERSION = 1


@dataclass(frozen=True)
class PersistedOwnershipState:
    """Validated persisted evidence eligible for recovery evaluation."""

    layers: tuple[OwnershipLayer, ...]
    suppressed_sessions: tuple[FamilySession, ...]


def serialize_state(
    layers_by_entity: dict[str, tuple[OwnershipLayer, ...] | list[OwnershipLayer]],
    sessions: tuple[FamilySession, ...] | list[FamilySession],
) -> dict[str, Any]:
    """Serialize only state that is contractually eligible to survive restart."""
    persisted_layers: list[dict[str, Any]] = []
    for entity_id in sorted(layers_by_entity):
        for layer in layers_by_entity[entity_id]:
            if layer.kind not in (LayerKind.MANUAL, LayerKind.MANUAL_OFF):
                continue
            persisted_layers.append(
                {
                    "entity_id": entity_id,
                    "layer": _serialize_layer(layer),
                }
            )

    persisted_sessions = [
        _serialize_session(session)
        for session in sessions
        if session.suppressed
    ]

    return {
        "version": STORAGE_VERSION,
        "layers": persisted_layers,
        "suppressed_sessions": persisted_sessions,
    }


def deserialize_state(payload: dict[str, Any] | None) -> PersistedOwnershipState:
    """Parse persisted evidence conservatively.

    Malformed individual records are dropped rather than treated as ownership authority. An
    unsupported top-level schema version discards the whole payload.
    """
    if not isinstance(payload, dict) or payload.get("version") != STORAGE_VERSION:
        return PersistedOwnershipState(layers=(), suppressed_sessions=())

    layers: list[OwnershipLayer] = []
    for item in payload.get("layers", []):
        try:
            if not isinstance(item, dict):
                continue
            entity_id = item["entity_id"]
            if not isinstance(entity_id, str) or not entity_id:
                continue
            layer = _deserialize_layer(item["layer"])
            if layer.kind not in (LayerKind.MANUAL, LayerKind.MANUAL_OFF):
                continue
            metadata = dict(layer.metadata)
            metadata["persisted_entity_id"] = entity_id
            layers.append(_replace_metadata(layer, metadata))
        except (KeyError, TypeError, ValueError):
            continue

    sessions: list[FamilySession] = []
    for item in payload.get("suppressed_sessions", []):
        try:
            session = _deserialize_session(item)
            if session.suppressed:
                sessions.append(session)
        except (KeyError, TypeError, ValueError):
            continue

    return PersistedOwnershipState(
        layers=tuple(layers),
        suppressed_sessions=tuple(sessions),
    )


def _serialize_layer(layer: OwnershipLayer) -> dict[str, Any]:
    appearance = None if layer.appearance is None else asdict(layer.appearance)
    return {
        "layer_id": layer.layer_id,
        "owner": layer.owner,
        "kind": layer.kind.value,
        "generation": layer.generation,
        "order": layer.order,
        "appearance": appearance,
        "family": layer.family,
        "session_id": layer.session_id,
        "expires_at_boundary": layer.expires_at_boundary,
        "precedence": layer.precedence,
        "metadata": dict(layer.metadata),
    }


def _deserialize_layer(data: dict[str, Any]) -> OwnershipLayer:
    if not isinstance(data, dict):
        raise TypeError("layer record must be a mapping")

    layer_id = data.get("layer_id")
    owner = data.get("owner")
    generation = data.get("generation")
    order = data.get("order", 0)
    precedence = data.get("precedence", 0)
    if not isinstance(layer_id, str) or not layer_id:
        raise TypeError("layer_id must be a non-empty string")
    if not isinstance(owner, str) or not owner:
        raise TypeError("owner must be a non-empty string")
    if not isinstance(generation, int) or isinstance(generation, bool):
        raise TypeError("generation must be an integer")
    if not isinstance(order, int) or isinstance(order, bool):
        raise TypeError("order must be an integer")
    if not isinstance(precedence, int) or isinstance(precedence, bool):
        raise TypeError("precedence must be an integer")

    appearance_data = data.get("appearance")
    appearance = None
    if appearance_data is not None:
        if not isinstance(appearance_data, dict):
            raise TypeError("appearance must be a mapping")
        appearance = _deserialize_appearance(appearance_data)

    family = data.get("family")
    session_id = data.get("session_id")
    expires_at_boundary = data.get("expires_at_boundary")
    if family is not None and not isinstance(family, str):
        raise TypeError("family must be a string or null")
    if session_id is not None and not isinstance(session_id, str):
        raise TypeError("session_id must be a string or null")
    if expires_at_boundary is not None and not isinstance(expires_at_boundary, str):
        raise TypeError("expires_at_boundary must be a string or null")

    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise TypeError("metadata must be a mapping")

    return OwnershipLayer(
        layer_id=layer_id,
        owner=owner,
        kind=LayerKind(data["kind"]),
        generation=generation,
        order=order,
        appearance=appearance,
        family=family,
        session_id=session_id,
        expires_at_boundary=expires_at_boundary,
        precedence=precedence,
        metadata=dict(metadata),
    )


def _deserialize_appearance(data: dict[str, Any]) -> Appearance:
    normalized = dict(data)
    color_lengths = {"xy_color": 2, "rgb_color": 3, "hs_color": 2}
    for field, expected_length in color_lengths.items():
        value = normalized.get(field)
        if value is not None:
            if not isinstance(value, (list, tuple)) or len(value) != expected_length:
                raise TypeError(f"{field} must contain {expected_length} values")
            normalized[field] = tuple(value)
    return Appearance(**normalized)

def _serialize_session(session: FamilySession) -> dict[str, Any]:
    return {
        "family": session.family,
        "session_id": session.session_id,
        "generation": session.generation,
        "suppressed": session.suppressed,
        "suppression_reason": session.suppression_reason,
    }


def _deserialize_session(data: dict[str, Any]) -> FamilySession:
    if not isinstance(data, dict):
        raise TypeError("session record must be a mapping")
    family = data.get("family")
    session_id = data.get("session_id")
    generation = data.get("generation")
    suppressed = data.get("suppressed", False)
    suppression_reason = data.get("suppression_reason")
    if not isinstance(family, str) or not family:
        raise TypeError("family must be a non-empty string")
    if not isinstance(session_id, str) or not session_id:
        raise TypeError("session_id must be a non-empty string")
    if not isinstance(generation, int) or isinstance(generation, bool):
        raise TypeError("generation must be an integer")
    if not isinstance(suppressed, bool):
        raise TypeError("suppressed must be a boolean")
    if suppression_reason is not None and not isinstance(suppression_reason, str):
        raise TypeError("suppression_reason must be a string or null")
    return FamilySession(
        family=family,
        session_id=session_id,
        generation=generation,
        suppressed=suppressed,
        suppression_reason=suppression_reason,
    )

def _replace_metadata(layer: OwnershipLayer, metadata: dict[str, Any]) -> OwnershipLayer:
    return OwnershipLayer(
        layer_id=layer.layer_id,
        owner=layer.owner,
        kind=layer.kind,
        generation=layer.generation,
        order=layer.order,
        appearance=layer.appearance,
        family=layer.family,
        session_id=layer.session_id,
        expires_at_boundary=layer.expires_at_boundary,
        precedence=layer.precedence,
        metadata=metadata,
    )
