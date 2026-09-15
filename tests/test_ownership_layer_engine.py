import pytest

from custom_components.home_lighting_manager.engine import NIGHTLY_BOUNDARY, OwnershipEngine
from custom_components.home_lighting_manager.model import (
    Appearance,
    GroupOffAction,
    LayerKind,
    OffAction,
    OwnershipLayer,
)


def layer(
    layer_id,
    owner,
    kind,
    generation=1,
    appearance=None,
    family=None,
    session_id=None,
    expires=None,
    precedence=0,
):
    return OwnershipLayer(
        layer_id=layer_id,
        owner=owner,
        kind=kind,
        generation=generation,
        order=0,
        appearance=appearance,
        family=family,
        session_id=session_id,
        expires_at_boundary=expires,
        precedence=precedence,
    )


def test_structural_precedence_beats_later_recency():
    engine = OwnershipEngine()
    entity = "light.example"
    sync = engine.push(
        entity,
        layer("sync", "sync", LayerKind.AUTOMATIC, precedence=500, appearance=Appearance(on=True)),
    )
    engine.push(
        entity,
        layer("manual", "manual", LayerKind.MANUAL, precedence=200, appearance=Appearance(on=True)),
    )

    assert engine.resolve(entity).layer == sync


def test_recency_breaks_ties_within_same_precedence():
    engine = OwnershipEngine()
    entity = "light.example"
    engine.push(entity, layer("daily", "daily", LayerKind.AUTOMATIC, precedence=100))
    holiday = engine.push(entity, layer("holiday", "holiday", LayerKind.AUTOMATIC, precedence=100))

    assert engine.resolve(entity).layer == holiday


def test_off_peels_manual_then_suppresses_spa_then_creates_manual_off():
    engine = OwnershipEngine()
    entity = "light.backyard_spa_strip_lights"
    evening = engine.push(
        entity,
        layer("evening", "daily", LayerKind.AUTOMATIC, precedence=100, appearance=Appearance(on=True)),
    )
    engine.start_family("spa_gauge", "spa-1")
    engine.push(
        entity,
        layer(
            "spa",
            "spa_gauge",
            LayerKind.AUTOMATIC,
            precedence=300,
            appearance=Appearance(on=True, xy_color=(0.2, 0.3)),
            family="spa_gauge",
            session_id="spa-1",
        ),
    )
    engine.push(
        entity,
        layer("manual-white", "manual", LayerKind.MANUAL, precedence=400, appearance=Appearance(on=True)),
    )

    first = engine.apply_off(entity, manual_off_precedence=400)
    assert first.action is OffAction.RELEASED_MANUAL
    assert engine.resolve(entity).layer.owner == "spa_gauge"

    second = engine.apply_off(entity, manual_off_precedence=400)
    assert second.action is OffAction.SUPPRESSED_FAMILY
    assert engine.resolve(entity).layer == evening

    third = engine.apply_off(entity, manual_off_precedence=400)
    assert third.action is OffAction.CREATED_MANUAL_OFF
    assert engine.resolve(entity).layer.kind is LayerKind.MANUAL_OFF
    assert engine.resolve(entity).appearance == Appearance(on=False)


def test_off_on_automatic_directly_creates_manual_off():
    engine = OwnershipEngine()
    entity = "light.kitchen"
    engine.push(entity, layer("daily", "daily", LayerKind.AUTOMATIC, precedence=100))

    result = engine.apply_off(entity, manual_off_precedence=200)

    assert result.action is OffAction.CREATED_MANUAL_OFF
    assert engine.resolve(entity).layer.kind is LayerKind.MANUAL_OFF


def test_manual_off_is_idempotent_until_lifecycle_transition():
    engine = OwnershipEngine()
    entity = "light.kitchen"
    engine.push(entity, layer("daily", "daily", LayerKind.AUTOMATIC, precedence=100))
    engine.apply_off(entity, manual_off_precedence=200)

    result = engine.apply_off(entity, manual_off_precedence=200)

    assert result.action is OffAction.ALREADY_MANUAL_OFF
    assert len([item for item in engine.layers(entity) if item.kind is LayerKind.MANUAL_OFF]) == 1


def test_manual_off_expires_at_nightly_boundary():
    engine = OwnershipEngine()
    entity = "light.kitchen"
    daily = engine.push(entity, layer("daily", "daily", LayerKind.AUTOMATIC, precedence=100))
    engine.apply_off(entity, manual_off_precedence=200)

    engine.expire_boundary(NIGHTLY_BOUNDARY)

    assert engine.resolve(entity).layer == daily


def test_first_group_off_releases_mixed_homeowner_exceptions_only():
    engine = OwnershipEngine()
    left = "light.left"
    right = "light.right"
    left_daily = engine.push(left, layer("left-daily", "daily", LayerKind.AUTOMATIC, precedence=100))
    right_daily = engine.push(right, layer("right-daily", "daily", LayerKind.AUTOMATIC, precedence=100))
    engine.push(left, layer("left-manual", "manual", LayerKind.MANUAL, precedence=200))
    engine.push_manual_off(right, precedence=200)

    result = engine.apply_group_off("main", [left, right], manual_off_precedence=200)

    assert result.action is GroupOffAction.RELEASED_TO_HLM
    assert engine.resolve(left).layer == left_daily
    assert engine.resolve(right).layer == right_daily


def test_second_group_off_creates_manual_off_for_exact_members():
    engine = OwnershipEngine()
    left = "light.left"
    right = "light.right"
    outsider = "light.outside"
    for entity in (left, right, outsider):
        engine.push(entity, layer(f"daily:{entity}", "daily", LayerKind.AUTOMATIC, precedence=100))

    engine.apply_group_off("main", [left, right], manual_off_precedence=200)
    result = engine.apply_group_off("main", [left, right], manual_off_precedence=200)

    assert result.action is GroupOffAction.CREATED_GROUP_MANUAL_OFF
    assert engine.resolve(left).layer.kind is LayerKind.MANUAL_OFF
    assert engine.resolve(right).layer.kind is LayerKind.MANUAL_OFF
    assert engine.resolve(outsider).layer.kind is LayerKind.AUTOMATIC


def test_group_off_member_change_restarts_first_off_semantics():
    engine = OwnershipEngine()
    for entity in ("light.a", "light.b", "light.c"):
        engine.push(entity, layer(entity, "daily", LayerKind.AUTOMATIC, precedence=100))

    first = engine.apply_group_off("main", ["light.a", "light.b"], manual_off_precedence=200)
    changed = engine.apply_group_off("main", ["light.a", "light.c"], manual_off_precedence=200)

    assert first.action is GroupOffAction.RELEASED_TO_HLM
    assert changed.action is GroupOffAction.RELEASED_TO_HLM
    assert engine.resolve("light.a").layer.kind is LayerKind.AUTOMATIC


def test_intervening_intent_can_disarm_group_double_off():
    engine = OwnershipEngine()
    entity = "light.a"
    engine.push(entity, layer("daily", "daily", LayerKind.AUTOMATIC, precedence=100))
    engine.apply_group_off("main", [entity], manual_off_precedence=200)

    engine.reset_group_off_sequence("main")
    result = engine.apply_group_off("main", [entity], manual_off_precedence=200)

    assert result.action is GroupOffAction.RELEASED_TO_HLM


def test_suppressed_family_child_overlay_is_ineligible():
    engine = OwnershipEngine()
    entity = "light.front"
    engine.start_family("49ers", "game-1")
    engine.suppress_family("49ers", "game-1", "homeowner_override")
    engine.push(
        entity,
        layer(
            "score-flash",
            "49ers_score",
            LayerKind.OVERLAY,
            precedence=300,
            family="49ers",
            session_id="game-1",
        ),
    )

    assert engine.resolve(entity).layer is None
    assert engine.family_eligible("49ers", "game-1") is False


def test_new_family_session_is_not_contaminated_by_old_suppression():
    engine = OwnershipEngine()
    engine.start_family("49ers", "game-1")
    engine.suppress_family("49ers", "game-1", "homeowner_override")

    engine.start_family("49ers", "game-2")

    assert engine.family_eligible("49ers", "game-1") is False
    assert engine.family_eligible("49ers", "game-2") is True


def test_push_rejects_stale_generation_layer():
    engine = OwnershipEngine()
    old = engine.generation
    engine.next_generation()

    with pytest.raises(ValueError, match="stale-generation"):
        engine.push("light.a", layer("old", "daily", LayerKind.AUTOMATIC, generation=old))


def test_off_default_manual_off_is_exposed_without_precedence_hint():
    engine = OwnershipEngine()
    entity = "light.kitchen"
    engine.push(entity, layer("daily", "daily", LayerKind.AUTOMATIC, precedence=250))

    result = engine.apply_off(entity)

    assert result.action is OffAction.CREATED_MANUAL_OFF
    assert engine.resolve(entity).layer.kind is LayerKind.MANUAL_OFF


def test_second_group_off_matches_same_members_in_different_order():
    engine = OwnershipEngine()
    left = "light.left"
    right = "light.right"
    for entity in (left, right):
        engine.push(entity, layer(f"daily:{entity}", "daily", LayerKind.AUTOMATIC, precedence=250))

    first = engine.apply_group_off("main", [left, right])
    second = engine.apply_group_off("main", [right, left])

    assert first.action is GroupOffAction.RELEASED_TO_HLM
    assert second.action is GroupOffAction.CREATED_GROUP_MANUAL_OFF
    assert engine.resolve(left).layer.kind is LayerKind.MANUAL_OFF
    assert engine.resolve(right).layer.kind is LayerKind.MANUAL_OFF
