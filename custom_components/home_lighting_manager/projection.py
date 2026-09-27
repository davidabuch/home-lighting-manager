"""Read-only migration contract. Desired ownership, never observed light state."""

from dataclasses import asdict

from .model import LayerKind


def effective_ownership(engine, entities, groups=None):
    """Derive each record afresh; no independent cache or persisted rendering history."""
    records = {}
    for entity in sorted(set(entities)):
        layer = engine.resolve(entity).layer
        records[entity] = {
            "owner": layer.owner if layer else None,
            "kind": layer.kind.value if layer else None,
            "desired": asdict(layer.appearance) if layer and layer.appearance else None,
            "protected": bool(layer and layer.kind != LayerKind.AUTOMATIC),
            "underlying_automatic": [
                {"owner": item.owner, "family": item.family, "session_id": item.session_id}
                for item in engine.layers(entity)
                if item.kind == LayerKind.AUTOMATIC and engine.layer_eligible(entity, item)
            ],
            "layer_id": layer.layer_id if layer else None,
            "group_id": layer.group_id if layer else None,
            "family": layer.family if layer else None,
            "session_id": layer.session_id if layer else None,
        }
    token = engine.work_token()
    return {
        "version": 1,
        "authority_id": token.authority_id,
        "generation": token.generation,
        "revision": token.revision,
        "automatic_authority": "legacy_resolver",
        "entities": records,
        "groups": {
            g: sorted(set(m) & set(records))
            for g, m in (groups or {}).items()
            if set(m) & set(records)
        },
    }
