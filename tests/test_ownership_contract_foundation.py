from custom_components.home_lighting_manager.engine import OwnershipEngine
from custom_components.home_lighting_manager.model import Appearance, LayerKind, OwnershipLayer


def layer(
    layer_id,
    owner,
    kind,
    generation=1,
    appearance=None,
    family=None,
    session_id=None,
    expires=None,
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
    )


def test_manual_first_off_reveals_automatic_then_second_off_can_be_manual_off():
    engine = OwnershipEngine()
    entity = "light.kitchen_kitchen_right_cabinet_lights"
    daily = engine.push(
        entity,
        layer("daily", "daily", LayerKind.AUTOMATIC, appearance=Appearance(on=True)),
    )
    engine.push(
        entity,
        layer(
            "manual-purple",
            "manual",
            LayerKind.MANUAL,
            appearance=Appearance(on=True, xy_color=(0.3, 0.15)),
            expires="nightly_0159",
        ),
    )

    assert engine.resolve(entity).layer.owner == "manual"
    assert engine.manual_release(entity) is True
    assert engine.resolve(entity).layer == daily

    manual_off = engine.push(
        entity,
        layer(
            "manual-off",
            "manual",
            LayerKind.MANUAL_OFF,
            appearance=Appearance(on=False),
            expires="nightly_0159",
        ),
    )
    assert engine.resolve(entity).layer == manual_off
    assert engine.resolve(entity).appearance == Appearance(on=False)


def test_nightly_boundary_expires_ordinary_manual_but_keeps_automatic_layer():
    engine = OwnershipEngine()
    entity = "light.living_room_left_ceiling_light"
    daily = engine.push(
        entity,
        layer("daily", "daily", LayerKind.AUTOMATIC, appearance=Appearance(on=True)),
    )
    engine.push(
        entity,
        layer(
            "manual",
            "manual",
            LayerKind.MANUAL,
            appearance=Appearance(on=True, brightness=80),
            expires="nightly_0159",
        ),
    )

    engine.expire_boundary("nightly_0159")

    assert engine.resolve(entity).layer == daily


def test_49ers_parent_suppression_makes_child_effect_ineligible_for_same_game():
    engine = OwnershipEngine()
    entity = "light.front_yard_front_eve_lights"
    engine.start_family("49ers", "game-1")
    engine.push(
        entity,
        layer(
            "49ers-theme",
            "49ers",
            LayerKind.AUTOMATIC,
            appearance=Appearance(on=True, scene_id="49ers"),
            family="49ers",
            session_id="game-1",
        ),
    )
    assert engine.resolve(entity).layer.owner == "49ers"
    assert engine.family_eligible("49ers", "game-1") is True

    engine.suppress_family("49ers", "game-1", "homeowner_override")

    assert engine.family_eligible("49ers", "game-1") is False
    assert engine.resolve(entity).layer is None

    engine.start_family("49ers", "game-2")
    assert engine.family_eligible("49ers", "game-2") is True


def test_spa_gauge_suppression_blocks_target_blink_for_current_session_only():
    engine = OwnershipEngine()
    engine.start_family("spa_gauge", "spa-1")
    assert engine.family_eligible("spa_gauge", "spa-1") is True

    engine.suppress_family("spa_gauge", "spa-1", "homeowner_dismissal")
    assert engine.family_eligible("spa_gauge", "spa-1") is False

    engine.start_family("spa_gauge", "spa-2")
    assert engine.family_eligible("spa_gauge", "spa-2") is True


def test_unavailable_is_not_modeled_as_manual_off():
    engine = OwnershipEngine()
    entity = "light.front_yard_front_eve_lights"
    daily = engine.push(
        entity,
        layer("daily", "daily", LayerKind.AUTOMATIC, appearance=Appearance(on=True)),
    )

    # Availability is observation quality and deliberately does not mutate ownership.
    assert engine.resolve(entity).layer == daily
    assert all(item.kind is not LayerKind.MANUAL_OFF for item in engine.layers(entity))


def test_new_generation_rejects_old_work_and_old_layers_do_not_resolve():
    engine = OwnershipEngine()
    entity = "light.backyard_spa_strip_lights"
    old_generation = engine.generation
    engine.push(
        entity,
        layer("daily", "daily", LayerKind.AUTOMATIC, appearance=Appearance(on=True)),
    )

    engine.next_generation()

    assert engine.is_current_generation(old_generation) is False
    assert engine.resolve(entity).layer is None
