from custom_components.home_lighting_manager.intent import IntentEvidence, IntentEvidenceKind
from custom_components.home_lighting_manager.model import (
    Appearance,
    FamilySession,
    LayerKind,
    OwnershipLayer,
)
from custom_components.home_lighting_manager.persistence import (
    STORAGE_VERSION,
    deserialize_state,
    serialize_state,
)
from custom_components.home_lighting_manager.recovery import (
    FamilyRecoveryEvidence,
    ManualRecoveryEvidence,
)
from custom_components.home_lighting_manager.shadow import ShadowObservation, ShadowRuntime


def layer(layer_id, kind, *, generation=1, owner="manual", appearance=None):
    return OwnershipLayer(
        layer_id=layer_id,
        owner=owner,
        kind=kind,
        generation=generation,
        order=1,
        appearance=appearance,
        precedence=100,
    )


def homeowner_evidence():
    return IntentEvidence(kind=IntentEvidenceKind.EXPLICIT_HOMEOWNER_COMMAND)


def test_persistence_serializes_only_manual_layers_and_suppressed_sessions():
    manual = layer("manual", LayerKind.MANUAL, appearance=Appearance(on=True, brightness=120))
    automatic = layer("daily", LayerKind.AUTOMATIC, owner="daily")
    suppressed = FamilySession("49ers", "game-1", 1, True, "homeowner_override")
    active = FamilySession("49ers", "game-2", 1, False, None)

    payload = serialize_state(
        {"light.kitchen": [automatic, manual]},
        [suppressed, active],
    )

    assert payload["version"] == STORAGE_VERSION
    assert len(payload["layers"]) == 1
    assert payload["layers"][0]["layer"]["kind"] == "manual"
    assert len(payload["suppressed_sessions"]) == 1
    assert payload["suppressed_sessions"][0]["session_id"] == "game-1"


def test_unknown_storage_version_discards_all_evidence():
    restored = deserialize_state({"version": 999, "layers": [], "suppressed_sessions": []})
    assert restored.layers == ()
    assert restored.suppressed_sessions == ()


def test_malformed_records_are_dropped_conservatively():
    restored = deserialize_state(
        {
            "version": STORAGE_VERSION,
            "layers": [{"entity_id": "light.a", "layer": {"kind": "manual"}}],
            "suppressed_sessions": [{"family": "49ers"}],
        }
    )
    assert restored.layers == ()
    assert restored.suppressed_sessions == ()


def test_round_trip_preserves_manual_appearance_and_entity_identity():
    manual = layer(
        "manual",
        LayerKind.MANUAL,
        appearance=Appearance(on=True, rgb_color=(10, 20, 30), effect="candle"),
    )
    restored = deserialize_state(serialize_state({"light.a": [manual]}, []))

    assert len(restored.layers) == 1
    item = restored.layers[0]
    assert item.appearance == manual.appearance
    assert item.metadata["persisted_entity_id"] == "light.a"


def test_shadow_ignores_recovery_telemetry_for_manual_ownership():
    runtime = ShadowRuntime()
    decision = runtime.observe(
        ShadowObservation(
            entity_id="light.a",
            evidence=IntentEvidence(kind=IntentEvidenceKind.RECOVERY_TELEMETRY),
            appearance=Appearance(on=True),
        )
    )

    assert decision.mutated is False
    assert runtime.engine.resolve("light.a").layer is None


def test_shadow_records_explicit_homeowner_appearance_without_commanding():
    runtime = ShadowRuntime()
    decision = runtime.observe(
        ShadowObservation(
            entity_id="light.a",
            evidence=homeowner_evidence(),
            appearance=Appearance(on=True, xy_color=(0.3, 0.2)),
            manual_precedence=200,
        )
    )

    assert decision.mutated is True
    resolved = runtime.engine.resolve("light.a").layer
    assert resolved is not None
    assert resolved.kind is LayerKind.MANUAL
    assert resolved.appearance == Appearance(on=True, xy_color=(0.3, 0.2))


def test_shadow_explicit_off_delegates_to_existing_off_semantics():
    runtime = ShadowRuntime()
    runtime.observe(
        ShadowObservation(
            entity_id="light.a",
            evidence=homeowner_evidence(),
            appearance=Appearance(on=True),
            manual_precedence=200,
        )
    )

    result = runtime.observe(
        ShadowObservation(
            entity_id="light.a",
            evidence=homeowner_evidence(),
            operation="off",
        )
    )

    assert result.reason == "released_manual"
    assert runtime.engine.resolve("light.a").layer is None


def test_shadow_export_contains_no_automatic_state():
    runtime = ShadowRuntime()
    runtime.engine.push(
        "light.a",
        layer("daily", LayerKind.AUTOMATIC, owner="daily"),
    )
    runtime.observe(
        ShadowObservation(
            entity_id="light.b",
            evidence=homeowner_evidence(),
            appearance=Appearance(on=True),
            manual_precedence=200,
        )
    )

    payload = runtime.export_persistence()
    assert [item["entity_id"] for item in payload["layers"]] == ["light.b"]


def test_shadow_restore_rebinds_trusted_manual_state_to_current_generation():
    old = layer("manual", LayerKind.MANUAL, generation=1, appearance=Appearance(on=True))
    persisted = deserialize_state(serialize_state({"light.a": [old]}, []))
    runtime = ShadowRuntime(generation=8)

    runtime.restore(
        persisted,
        manual_evidence={
            "light.a": ManualRecoveryEvidence(
                temporally_valid=True,
                desired_state_trustworthy=True,
                ownership_evidence_coherent=True,
            )
        },
        family_evidence={},
    )

    restored = runtime.engine.resolve("light.a").layer
    assert restored is not None
    assert restored.generation == 8


def test_shadow_restore_drops_ambiguous_manual_state():
    old = layer("manual", LayerKind.MANUAL, generation=1, appearance=Appearance(on=True))
    persisted = deserialize_state(serialize_state({"light.a": [old]}, []))
    runtime = ShadowRuntime(generation=2)

    runtime.restore(
        persisted,
        manual_evidence={
            "light.a": ManualRecoveryEvidence(
                temporally_valid=True,
                desired_state_trustworthy=False,
                ownership_evidence_coherent=True,
            )
        },
        family_evidence={},
    )

    assert runtime.engine.resolve("light.a").layer is None


def test_shadow_restore_preserves_only_validated_same_session_suppression():
    persisted = deserialize_state(
        serialize_state(
            {},
            [FamilySession("spa_gauge", "spa-1", 1, True, "homeowner_off")],
        )
    )
    runtime = ShadowRuntime(generation=3)

    runtime.restore(
        persisted,
        manual_evidence={},
        family_evidence={
            ("spa_gauge", "spa-1"): FamilyRecoveryEvidence(
                same_session_active=True,
                session_evidence_coherent=True,
            )
        },
    )

    assert runtime.engine.family_eligible("spa_gauge", "spa-1") is False
    assert runtime.diagnostics().suppressed_sessions == 1


def test_shadow_diagnostics_are_non_sensitive_counts_only():
    runtime = ShadowRuntime()
    runtime.observe(
        ShadowObservation(
            entity_id="light.a",
            evidence=homeowner_evidence(),
            appearance=Appearance(on=True),
            manual_precedence=200,
        )
    )
    runtime.observe(
        ShadowObservation(
            entity_id="light.b",
            evidence=IntentEvidence(kind=IntentEvidenceKind.PHYSICAL_DRIFT),
        )
    )

    diagnostic = runtime.diagnostics()
    assert diagnostic.observed_events == 2
    assert diagnostic.homeowner_events == 1
    assert diagnostic.ignored_or_hlm_events == 1
    assert diagnostic.managed_entities == 1


def test_json_round_trip_normalizes_color_sequences_back_to_tuples():
    import json

    manual = layer(
        "manual",
        LayerKind.MANUAL,
        appearance=Appearance(
            on=True,
            xy_color=(0.3, 0.2),
            rgb_color=(10, 20, 30),
            hs_color=(180.0, 50.0),
        ),
    )
    payload = json.loads(json.dumps(serialize_state({"light.a": [manual]}, [])))
    restored = deserialize_state(payload)

    assert restored.layers[0].appearance == manual.appearance


def test_deserializer_drops_type_coercible_but_malformed_records():
    restored = deserialize_state(
        {
            "version": STORAGE_VERSION,
            "layers": [
                {
                    "entity_id": "light.a",
                    "layer": {
                        "layer_id": None,
                        "owner": 123,
                        "kind": "manual",
                        "generation": "1",
                    },
                }
            ],
            "suppressed_sessions": [
                {
                    "family": 49,
                    "session_id": "game-1",
                    "generation": "1",
                    "suppressed": "true",
                }
            ],
        }
    )

    assert restored.layers == ()
    assert restored.suppressed_sessions == ()


def test_shadow_off_family_suppression_is_exported_for_restart_recovery():
    runtime = ShadowRuntime()
    runtime.engine.start_family("spa_gauge", "spa-1")
    runtime.engine.push(
        "light.spa",
        OwnershipLayer(
            layer_id="spa",
            owner="spa_gauge",
            kind=LayerKind.AUTOMATIC,
            generation=runtime.engine.generation,
            order=0,
            appearance=Appearance(on=True),
            family="spa_gauge",
            session_id="spa-1",
            precedence=300,
        ),
    )

    result = runtime.observe(
        ShadowObservation(
            entity_id="light.spa",
            evidence=homeowner_evidence(),
            operation="off",
        )
    )

    assert result.reason == "suppressed_family"
    payload = runtime.export_persistence()
    assert len(payload["suppressed_sessions"]) == 1
    assert payload["suppressed_sessions"][0]["family"] == "spa_gauge"
    assert payload["suppressed_sessions"][0]["session_id"] == "spa-1"


def test_shadow_appearance_requires_explicit_manual_precedence_policy():
    runtime = ShadowRuntime()

    result = runtime.observe(
        ShadowObservation(
            entity_id="light.a",
            evidence=homeowner_evidence(),
            appearance=Appearance(on=True, brightness=100),
        )
    )

    assert result.mutated is False
    assert result.reason == "Manual precedence policy is required for appearance ownership"
    assert runtime.engine.resolve("light.a").layer is None


def test_shadow_manual_appearance_does_not_overtake_structural_sync():
    runtime = ShadowRuntime()
    sync = runtime.engine.push(
        "light.a",
        OwnershipLayer(
            layer_id="sync",
            owner="sync",
            kind=LayerKind.AUTOMATIC,
            generation=runtime.engine.generation,
            order=0,
            appearance=Appearance(on=True),
            precedence=500,
        ),
    )

    result = runtime.observe(
        ShadowObservation(
            entity_id="light.a",
            evidence=homeowner_evidence(),
            appearance=Appearance(on=True, rgb_color=(128, 0, 128)),
            manual_precedence=200,
        )
    )

    assert result.mutated is True
    assert runtime.engine.resolve("light.a").layer == sync
    manual_layers = [
        item for item in runtime.engine.layers("light.a") if item.kind is LayerKind.MANUAL
    ]
    assert len(manual_layers) == 1
    assert manual_layers[0].appearance == Appearance(on=True, rgb_color=(128, 0, 128))


def test_shadow_appearance_override_suppresses_exposed_family_and_persists_it():
    runtime = ShadowRuntime()
    runtime.engine.push(
        "light.a",
        OwnershipLayer(
            layer_id="daily",
            owner="daily",
            kind=LayerKind.AUTOMATIC,
            generation=runtime.engine.generation,
            order=0,
            appearance=Appearance(on=True),
            precedence=100,
        ),
    )
    runtime.engine.start_family("49ers", "game-1")
    runtime.engine.push(
        "light.a",
        OwnershipLayer(
            layer_id="49ers",
            owner="49ers",
            kind=LayerKind.AUTOMATIC,
            generation=runtime.engine.generation,
            order=0,
            appearance=Appearance(on=True, rgb_color=(170, 0, 0)),
            family="49ers",
            session_id="game-1",
            precedence=300,
        ),
    )

    result = runtime.observe(
        ShadowObservation(
            entity_id="light.a",
            evidence=homeowner_evidence(),
            appearance=Appearance(on=True, color_temp_kelvin=4000),
            manual_precedence=200,
        )
    )

    assert result.mutated is True
    assert runtime.engine.family_eligible("49ers", "game-1") is False
    assert runtime.engine.resolve("light.a").layer.kind is LayerKind.MANUAL

    runtime.engine.manual_release("light.a")
    assert runtime.engine.resolve("light.a").layer.owner == "daily"

    payload = runtime.export_persistence()
    assert payload["suppressed_sessions"] == [
        {
            "family": "49ers",
            "session_id": "game-1",
            "generation": 1,
            "suppressed": True,
            "suppression_reason": "homeowner_override",
        }
    ]
