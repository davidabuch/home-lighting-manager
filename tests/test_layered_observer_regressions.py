"""Preserve commissioned attribution while integrating operation identity/diagnostics."""

from unittest.mock import patch

import pytest
from homeassistant.core import Context, HomeAssistant

from custom_components.home_lighting_manager.ha_observer import DIAGNOSTIC_ENTITY_ID
from custom_components.home_lighting_manager.model import LayerKind
from custom_components.home_lighting_manager.promotion_observer import (
    PromotingHomeAssistantShadowObserver,
)


async def observer_for(tmp_path, entities):
    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = PromotingHomeAssistantShadowObserver(hass, entities, dict.fromkeys(entities, 250))
    await observer.async_start()
    return hass, observer


async def seed_group(hass, entity_id, members, *, state="off"):
    """Publish known topology without creating external homeowner evidence."""
    hass.states.async_set(
        entity_id,
        state,
        {"entity_id": list(members)},
        context=Context(parent_id="topology-seed"),
    )
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_late_external_qualification_cannot_replace_newer_direct_user_intent(tmp_path):
    leaf, group = "light.path_1", "light.path_group"
    hass, observer = await observer_for(tmp_path, [leaf, group])
    try:
        hass.states.async_set(leaf, "on", {"brightness": 100})
        await hass.async_block_till_done()
        hass.states.async_set(leaf, "on", {"brightness": 200}, context=Context(user_id="user"))
        await hass.async_block_till_done()
        hass.states.async_set(group, "on", {"entity_id": [leaf], "brightness": 100})
        await hass.async_block_till_done()
        assert observer.runtime.engine.resolve(leaf).appearance.brightness == 200
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert "stale successful intent" in attrs["latest_operation_rejection"]["reason"]
        assert attrs["command_authority"] is False
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_six_leaf_hue_group_burst_is_not_six_homeowner_operations(tmp_path):
    leaves = [f"light.path_{i}" for i in range(6)]
    groups = [
        "light.driveway_path",
        "light.front_yard",
        "light.holiday_path",
        "light.holiday_lighting",
    ]
    hass, observer = await observer_for(tmp_path, leaves + groups)
    try:
        for leaf in leaves:
            hass.states.async_set(leaf, "off")
        for group in groups:
            hass.states.async_set(group, "off", {"entity_id": leaves})
        await hass.async_block_till_done()
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert attrs["external_burst"]["topology"] == "multi_leaf_with_aggregate_propagation"
        assert not attrs["external_burst"]["external_intent_candidate"]["qualified"]
        assert not observer.runtime.operations.history
        assert all(observer.runtime.engine.resolve(leaf).layer is None for leaf in leaves)
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_sync_parent_chain_and_isolated_teardown_do_not_create_manual(tmp_path):
    leaves = ["light.left", "light.right"]
    hass, observer = await observer_for(tmp_path, leaves)
    try:
        for leaf in leaves:
            hass.states.async_set(
                leaf, "on", {"brightness": 220}, context=Context(parent_id="sync-stop")
            )
        await hass.async_block_till_done()
        hass.states.async_set(leaves[0], "on", {"brightness": 180})
        await hass.async_block_till_done()
        assert not observer.runtime.operations.history
        assert all(observer.runtime.engine.resolve(leaf).layer is None for leaf in leaves)
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_qualified_external_first_off_still_releases_manual_and_no_service_called(tmp_path):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    leaf, group = "light.leaf", "light.group"
    hass, observer = await observer_for(tmp_path, [leaf, group])
    try:
        with patch.object(
            type(hass.services), "async_call", side_effect=AssertionError("no dispatch")
        ):
            hass.states.async_set(leaf, "on", {"brightness": 150})
            await hass.async_block_till_done()
            hass.states.async_set(group, "on", {"entity_id": [leaf]})
            await hass.async_block_till_done()
            assert observer.runtime.engine.resolve(leaf).layer.kind is LayerKind.MANUAL
            later = dt_util.now() + timedelta(seconds=3)
            with patch(
                "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
                return_value=later,
            ):
                hass.states.async_set(leaf, "off")
                await hass.async_block_till_done()
                hass.states.async_set(group, "off", {"entity_id": [leaf]})
                await hass.async_block_till_done()
            assert observer.runtime.engine.resolve(leaf).layer is None
            attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
            assert attrs["latest_homeowner_operation"]["reason"] == "released_manual"
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_preknown_unique_exact_group_promotes_one_shared_manual_operation(tmp_path):
    left, right = "light.kitchen_left", "light.kitchen_right"
    exact, larger = "light.kitchen_cabinets", "light.kitchen"
    hass, observer = await observer_for(tmp_path, [left, right, exact, larger])
    try:
        await seed_group(hass, exact, (left, right))
        await seed_group(hass, larger, (left, right, "light.kitchen_other"))

        hass.states.async_set(left, "on", {"brightness": 120})
        await hass.async_block_till_done()
        hass.states.async_set(right, "on", {"brightness": 210})
        await hass.async_block_till_done()
        hass.states.async_set(exact, "on", {"entity_id": [left, right]})
        await hass.async_block_till_done()

        assert len(observer.runtime.operations.history) == 1
        latest = observer.runtime.operations.latest_homeowner
        assert latest["group_id"] == exact
        assert latest["affected"] == (left, right)
        left_layer = observer.runtime.engine.resolve(left).layer
        right_layer = observer.runtime.engine.resolve(right).layer
        assert left_layer.kind is LayerKind.MANUAL
        assert right_layer.kind is LayerKind.MANUAL
        assert left_layer.group_id == right_layer.group_id == exact
        assert left_layer.operation_id == right_layer.operation_id
        assert observer.runtime.engine.resolve(left).appearance.brightness == 120
        assert observer.runtime.engine.resolve(right).appearance.brightness == 210

        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        candidate = attrs["external_burst"]["external_intent_candidate"]
        assert candidate["qualified"] is True
        assert candidate["group_id"] == exact
        assert candidate["basis"] == "unique_exact_group_with_aggregate_propagation"
        assert candidate["promoted_to_homeowner"] is True
        assert attrs["command_authority"] is False
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_exact_group_first_off_releases_then_second_off_creates_manual_off(tmp_path):
    left, right, group = "light.left", "light.right", "light.group"
    hass, observer = await observer_for(tmp_path, [left, right, group])
    try:
        await seed_group(hass, group, (left, right))
        for entity, brightness in ((left, 100), (right, 180)):
            hass.states.async_set(entity, "on", {"brightness": brightness})
            await hass.async_block_till_done()
        hass.states.async_set(group, "on", {"entity_id": [left, right]})
        await hass.async_block_till_done()
        assert all(
            observer.runtime.engine.resolve(entity).layer.kind is LayerKind.MANUAL
            for entity in (left, right)
        )

        for entity in (left, right):
            hass.states.async_set(entity, "off")
            await hass.async_block_till_done()
        hass.states.async_set(group, "off", {"entity_id": [left, right]})
        await hass.async_block_till_done()
        assert observer.runtime.operations.latest_homeowner["reason"] == "released_to_hlm"
        assert all(observer.runtime.engine.resolve(entity).layer is None for entity in (left, right))

        # Simulate HLM/automatic physical reassertion without changing ownership.
        for entity in (left, right):
            hass.states.async_set(entity, "on", context=Context(parent_id="hlm-reassert"))
            await hass.async_block_till_done()
        hass.states.async_set(
            group,
            "on",
            {"entity_id": [left, right]},
            context=Context(parent_id="hlm-reassert"),
        )
        await hass.async_block_till_done()

        for entity in (left, right):
            hass.states.async_set(entity, "off")
            await hass.async_block_till_done()
        hass.states.async_set(group, "off", {"entity_id": [left, right]})
        await hass.async_block_till_done()
        assert observer.runtime.operations.latest_homeowner["reason"] == "created_group_manual_off"
        assert all(
            observer.runtime.engine.resolve(entity).layer.kind is LayerKind.MANUAL_OFF
            for entity in (left, right)
        )
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_duplicate_preknown_exact_groups_remain_ambiguous(tmp_path):
    left, right = "light.left", "light.right"
    group_a, group_b = "light.group_a", "light.group_b"
    hass, observer = await observer_for(tmp_path, [left, right, group_a, group_b])
    try:
        await seed_group(hass, group_a, (left, right))
        await seed_group(hass, group_b, (left, right))
        hass.states.async_set(left, "on", {"brightness": 100})
        await hass.async_block_till_done()
        hass.states.async_set(right, "on", {"brightness": 100})
        await hass.async_block_till_done()
        hass.states.async_set(group_a, "on", {"entity_id": [left, right]})
        await hass.async_block_till_done()
        hass.states.async_set(group_b, "on", {"entity_id": [left, right]})
        await hass.async_block_till_done()

        assert not observer.runtime.operations.history
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert not attrs["external_burst"]["external_intent_candidate"]["qualified"]
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_exact_group_does_not_promote_when_extra_leaf_is_in_same_burst(tmp_path):
    left, right, extra = "light.left", "light.right", "light.extra"
    group = "light.group"
    hass, observer = await observer_for(tmp_path, [left, right, extra, group])
    try:
        await seed_group(hass, group, (left, right))
        for entity in (left, right, extra):
            hass.states.async_set(entity, "on", {"brightness": 100})
            await hass.async_block_till_done()
        hass.states.async_set(group, "on", {"entity_id": [left, right]})
        await hass.async_block_till_done()
        assert not observer.runtime.operations.history
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_exact_group_mixed_member_operations_fail_closed(tmp_path):
    left, right, group = "light.left", "light.right", "light.group"
    hass, observer = await observer_for(tmp_path, [left, right, group])
    try:
        await seed_group(hass, group, (left, right))
        hass.states.async_set(left, "on", {"brightness": 100})
        await hass.async_block_till_done()
        hass.states.async_set(right, "off")
        await hass.async_block_till_done()
        hass.states.async_set(group, "on", {"entity_id": [left, right]})
        await hass.async_block_till_done()

        assert not observer.runtime.operations.history
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        candidate = attrs["external_burst"]["external_intent_candidate"]
        assert candidate["qualified"] is True
        assert candidate["promoted_to_homeowner"] is False
        assert "mixed member operations" in candidate["promotion_reason"]
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_late_exact_group_cannot_overwrite_newer_direct_member_intent(tmp_path):
    left, right, group = "light.left", "light.right", "light.group"
    hass, observer = await observer_for(tmp_path, [left, right, group])
    try:
        await seed_group(hass, group, (left, right))
        hass.states.async_set(left, "on", {"brightness": 80})
        await hass.async_block_till_done()
        hass.states.async_set(right, "on", {"brightness": 80})
        await hass.async_block_till_done()
        hass.states.async_set(
            left,
            "on",
            {"brightness": 220},
            context=Context(user_id="user"),
        )
        await hass.async_block_till_done()
        hass.states.async_set(group, "on", {"entity_id": [left, right]})
        await hass.async_block_till_done()

        assert observer.runtime.engine.resolve(left).appearance.brightness == 220
        assert observer.runtime.engine.resolve(right).layer is None
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert "stale successful intent" in attrs["latest_operation_rejection"]["reason"]
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()



@pytest.mark.asyncio
async def test_guarded_automatic_right_ceiling_burst_does_not_create_manual(tmp_path):
    """A manager-owned repair must not become homeowner intent via Hue propagation."""

    leaf = "light.living_room_living_room_right_ceiling"
    group = "light.holiday_main_area"
    guard = "input_boolean.home_lighting_ha_guard_main_area"
    hass, observer = await observer_for(tmp_path, [leaf, group])
    try:
        hass.states.async_set(guard, "on")
        hass.states.async_set(
            leaf,
            "on",
            {
                "brightness": 43,
                "color_mode": "xy",
                "xy_color": [0.4711, 0.3867],
            },
        )
        await hass.async_block_till_done()
        hass.states.async_set(
            group,
            "on",
            {
                "entity_id": [leaf],
                "brightness": 43,
            },
        )
        await hass.async_block_till_done()

        assert observer.runtime.engine.resolve(leaf).layer is None
        assert not observer.runtime.operations.history
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        candidate = attrs["external_burst"]["external_intent_candidate"]
        assert candidate["qualified"] is True
        assert candidate["promoted_to_homeowner"] is False
        assert attrs["command_authority"] is False
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_hue_dynamic_palette_churn_does_not_create_manual(tmp_path):
    """Ongoing dynamic-scene leaf churn is automatic telemetry, not homeowner intent."""

    leaf = "light.front_yard_front_path_light_1"
    group = "light.holiday_path"
    hass, observer = await observer_for(tmp_path, [leaf, group])
    try:
        hass.states.async_set(
            leaf,
            "on",
            {
                "brightness": 255,
                "color_mode": "xy",
                "xy_color": [0.4634, 0.4181],
                "dynamics": "dynamic_palette",
            },
        )
        await hass.async_block_till_done()
        hass.states.async_set(
            group,
            "on",
            {
                "entity_id": [leaf],
                "brightness": 255,
                "dynamics": "dynamic_palette",
            },
        )
        await hass.async_block_till_done()

        assert observer.runtime.engine.resolve(leaf).layer is None
        assert not observer.runtime.operations.history
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        candidate = attrs["external_burst"]["external_intent_candidate"]
        assert candidate["qualified"] is True
        assert candidate["promoted_to_homeowner"] is False
        assert attrs["command_authority"] is False
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_unrelated_backyard_churn_does_not_poison_main_area_first_off(tmp_path):
    """A nearby external burst on another surface must not block first-OFF release."""

    cabinet = "light.living_room_living_room_right_cabinet_lights"
    main_group = "light.holiday_main_area"
    backyard_leaf = "light.backyard_behind_pool_6"
    backyard_group = "light.holiday_backyard"
    hass, observer = await observer_for(
        tmp_path,
        [cabinet, main_group, backyard_leaf, backyard_group],
    )
    try:
        await seed_group(hass, main_group, (cabinet,))
        await seed_group(hass, backyard_group, (backyard_leaf,))

        # Establish a legitimate homeowner Manual appearance on Main Area.
        hass.states.async_set(
            cabinet,
            "on",
            {"brightness": 87, "dynamics": "none"},
            context=Context(user_id="homeowner"),
        )
        await hass.async_block_till_done()
        assert observer.runtime.engine.resolve(cabinet).layer.kind is LayerKind.MANUAL

        # Unrelated Hue animation churn occurs immediately before the homeowner OFF.
        hass.states.async_set(
            backyard_leaf,
            "on",
            {"brightness": 200, "dynamics": "dynamic_palette"},
        )
        await hass.async_block_till_done()
        hass.states.async_set(
            backyard_group,
            "on",
            {"entity_id": [backyard_leaf], "dynamics": "dynamic_palette"},
        )
        await hass.async_block_till_done()

        # Main Area first OFF plus its aggregate propagation must form a fresh burst.
        hass.states.async_set(cabinet, "off", {"dynamics": "none"})
        await hass.async_block_till_done()
        hass.states.async_set(main_group, "on", {"entity_id": [cabinet]})
        await hass.async_block_till_done()

        assert observer.runtime.engine.resolve(cabinet).layer is None
        assert observer.runtime.operations.latest_homeowner["reason"] == "released_manual"
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert attrs["command_authority"] is False
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_nightly_boundary_quarantines_late_contextless_hue_rebound(tmp_path):
    """A late Hue rebound after 01:59 must not recreate expired Manual ownership."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    leaf, group = (
        "light.kitchen_kitchen_left_cabinet_light",
        "light.holiday_main_area",
    )
    hass, observer = await observer_for(tmp_path, [leaf, group])
    try:
        await seed_group(hass, group, (leaf,), state="on")
        boundary = dt_util.now()
        observer._handle_nightly_boundary(boundary)
        await hass.async_block_till_done()

        rebound = boundary + timedelta(seconds=10)
        with (
            patch(
                "custom_components.home_lighting_manager.ha_observer.dt_util.now",
                return_value=rebound,
            ),
            patch(
                "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
                return_value=rebound,
            ),
        ):
            hass.states.async_set(
                leaf,
                "on",
                {
                    "brightness": 255,
                    "color_temp_kelvin": 2724,
                    "dynamics": "none",
                },
            )
            await hass.async_block_till_done()
            hass.states.async_set(group, "on", {"entity_id": [leaf]})
            await hass.async_block_till_done()

            assert observer.runtime.engine.resolve(leaf).layer is None
            attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
            assert attrs["nightly_boundary_settling"] is True
            assert leaf not in attrs["reconciliation_protected_entities"]

        # The short settling timer may end, but ambiguous topology remains blocked
        # for the entire post-boundary OFF epoch. A direct HA-user receipt is affirmative.
        after_settle = boundary + timedelta(seconds=16)
        with (
            patch(
                "custom_components.home_lighting_manager.ha_observer.dt_util.now",
                return_value=after_settle,
            ),
            patch(
                "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
                return_value=after_settle,
            ),
        ):
            hass.states.async_set(
                leaf,
                "on",
                {
                    "brightness": 180,
                    "color_temp_kelvin": 3000,
                    "dynamics": "none",
                },
                context=Context(user_id="homeowner"),
            )
            await hass.async_block_till_done()

            layer = observer.runtime.engine.resolve(leaf).layer
            assert layer is not None and layer.kind is LayerKind.MANUAL
            attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
            assert attrs["nightly_boundary_settling"] is False
            assert leaf not in attrs["post_boundary_off_entities"]
            assert leaf in attrs["reconciliation_protected_entities"]
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_post_boundary_off_epoch_blocks_delayed_contextless_rebound(tmp_path):
    """A Hue rebound well after the settling timer must not manufacture Manual."""
    from datetime import timedelta
    from unittest.mock import patch

    from homeassistant.core import Context
    from homeassistant.util import dt as dt_util

    leaf, group = (
        "light.kitchen_kitchen_left_cabinet_light",
        "light.holiday_main_area",
    )
    hass, observer = await observer_for(tmp_path, [leaf, group])
    try:
        await seed_group(hass, group, (leaf,), state="on")
        boundary = dt_util.now()
        observer._handle_nightly_boundary(boundary)
        await hass.async_block_till_done()

        # Reproduce the class of failures seen at +10 seconds and +31 minutes.
        rebound = boundary + timedelta(minutes=31)
        with (
            patch(
                "custom_components.home_lighting_manager.ha_observer.dt_util.now",
                return_value=rebound,
            ),
            patch(
                "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
                return_value=rebound,
            ),
        ):
            hass.states.async_set(
                leaf,
                "on",
                {"brightness": 165, "color_temp_kelvin": 2724, "dynamics": "none"},
            )
            await hass.async_block_till_done()
            hass.states.async_set(group, "on", {"entity_id": [leaf]})
            await hass.async_block_till_done()

        assert observer.runtime.engine.resolve(leaf).layer is None
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert attrs["nightly_boundary_settling"] is False
        assert leaf in attrs["post_boundary_off_entities"]
        assert leaf not in attrs["reconciliation_protected_entities"]

        # Affirmative direct HA-user provenance re-opens the entity immediately.
        hass.states.async_set(
            leaf,
            "on",
            {"brightness": 190, "dynamics": "none"},
            context=Context(user_id="homeowner"),
        )
        await hass.async_block_till_done()
        assert leaf not in hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes[
            "post_boundary_off_entities"
        ]
        layer = observer.runtime.engine.resolve(leaf).layer
        assert layer is not None and layer.kind is LayerKind.MANUAL
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_authoritative_main_area_scene_recall_promotes_exact_manual_scene_group(tmp_path):
    """A newer raw Hue scene recall is stronger evidence than leaf rendering churn."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    leaves = (
        "light.kitchen_kitchen_left_cabinet_light",
        "light.kitchen_kitchen_right_cabinet_lights",
        "light.living_room_living_room_left_cabinets",
        "light.living_room_living_room_right_cabinet_lights",
        "light.living_room_left_ceiling_light",
        "light.living_room_living_room_right_ceiling",
        "light.living_room_liquor_cabinet_light",
    )
    aggregate = "light.holiday_main_area"
    observer_entities = [*leaves, aggregate]
    hass, observer = await observer_for(tmp_path, observer_entities)
    try:
        await seed_group(hass, aggregate, leaves, state="off")
        hass.states.async_set("input_boolean.home_lighting_ha_guard_main_area", "off")
        hass.states.async_set("binary_sensor.hue_bridge_living_room", "off")
        await hass.async_block_till_done()

        old = dt_util.now() - timedelta(minutes=5)
        new = dt_util.now()
        hass.states.async_set(
            "sensor.main_area_last_recall",
            old.isoformat(),
            {
                "scene_name": "49ers!",
                "scene_id": "5228223d-532f-4bef-b266-0ceceb780819",
            },
        )
        await hass.async_block_till_done()
        hass.states.async_set(
            "sensor.main_area_last_recall",
            new.isoformat(),
            {
                "scene_name": "49ers!",
                "scene_id": "5228223d-532f-4bef-b266-0ceceb780819",
            },
        )
        await hass.async_block_till_done()

        assert len(observer.runtime.operations.history) == 1
        op = observer.runtime.operations.latest_homeowner
        assert op is not None
        assert op["group_id"] == aggregate
        assert tuple(op["affected"]) == tuple(sorted(leaves))
        for leaf in leaves:
            layer = observer.runtime.engine.resolve(leaf).layer
            assert layer is not None and layer.kind is LayerKind.MANUAL
            assert layer.group_id == aggregate
            assert layer.appearance.scene_id == "5228223d-532f-4bef-b266-0ceceb780819"
            assert layer.appearance.scene_evidence is not None

        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert set(leaves).issubset(set(attrs["reconciliation_protected_entities"]))
        assert attrs["command_authority"] is False

        # Immediate scene-rendering churn must not replace scene identity with a leaf snapshot.
        hass.states.async_set(leaves[0], "on", {"brightness": 180, "dynamics": "none"})
        await hass.async_block_till_done()
        hass.states.async_set(aggregate, "on", {"entity_id": list(leaves), "brightness": 180})
        await hass.async_block_till_done()
        assert observer.runtime.engine.resolve(leaves[0]).appearance.scene_id == (
            "5228223d-532f-4bef-b266-0ceceb780819"
        )
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_guarded_main_area_scene_recall_does_not_create_manual(tmp_path):
    """Manager-owned Hue scene recalls remain automatic when the surface guard is active."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    leaves = ("light.kitchen_kitchen_left_cabinet_light", "light.kitchen_kitchen_right_cabinet_lights")
    aggregate = "light.holiday_main_area"
    hass, observer = await observer_for(tmp_path, [*leaves, aggregate])
    try:
        await seed_group(hass, aggregate, leaves, state="off")
        hass.states.async_set("input_boolean.home_lighting_ha_guard_main_area", "on")
        hass.states.async_set("binary_sensor.hue_bridge_living_room", "off")
        await hass.async_block_till_done()

        old = dt_util.now() - timedelta(minutes=5)
        new = dt_util.now()
        hass.states.async_set(
            "sensor.main_area_last_recall",
            old.isoformat(),
            {"scene_name": "49ers!", "scene_id": "scene-id"},
        )
        await hass.async_block_till_done()
        hass.states.async_set(
            "sensor.main_area_last_recall",
            new.isoformat(),
            {"scene_name": "49ers!", "scene_id": "scene-id"},
        )
        await hass.async_block_till_done()

        assert not observer.runtime.operations.history
        assert all(observer.runtime.engine.resolve(leaf).layer is None for leaf in leaves)
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert not attrs["reconciliation_protected_entities"]
        assert attrs["command_authority"] is False
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_authoritative_manual_scene_group_off_releases_despite_nested_aggregate_noise(tmp_path):
    """Main Area first-OFF releases the owned Manual scene even with Hue aggregate fan-out."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    leaves = (
        "light.kitchen_kitchen_left_cabinet_light",
        "light.kitchen_kitchen_right_cabinet_lights",
        "light.living_room_living_room_left_cabinets",
        "light.living_room_living_room_right_cabinet_lights",
        "light.living_room_left_ceiling_light",
        "light.living_room_living_room_right_ceiling",
        "light.living_room_liquor_cabinet_light",
    )
    main = "light.holiday_main_area"
    celebration = "light.celebration"
    holiday_all = "light.holiday_lighting"
    living = "light.living_room_living_room"
    kitchen = "light.kitchen_kitchen"
    cabinets = "light.kitchen_cabinet_lights"
    observer_entities = [
        *leaves,
        main,
        celebration,
        holiday_all,
        living,
        kitchen,
        cabinets,
    ]
    hass, observer = await observer_for(tmp_path, observer_entities)
    try:
        await seed_group(hass, main, leaves, state="off")
        await seed_group(hass, celebration, (*leaves, "light.extra"), state="off")
        await seed_group(hass, holiday_all, (*leaves, "light.extra_2"), state="off")
        await seed_group(
            hass,
            living,
            (
                "light.living_room_living_room_left_cabinets",
                "light.living_room_living_room_right_cabinet_lights",
                "light.living_room_left_ceiling_light",
                "light.living_room_living_room_right_ceiling",
                "light.living_room_liquor_cabinet_light",
            ),
            state="off",
        )
        await seed_group(
            hass,
            kitchen,
            (
                "light.kitchen_kitchen_left_cabinet_light",
                "light.kitchen_kitchen_right_cabinet_lights",
                "light.extra_kitchen",
            ),
            state="off",
        )
        await seed_group(
            hass,
            cabinets,
            (
                "light.kitchen_kitchen_left_cabinet_light",
                "light.kitchen_kitchen_right_cabinet_lights",
            ),
            state="off",
        )
        hass.states.async_set("input_boolean.home_lighting_ha_guard_main_area", "off")
        hass.states.async_set("binary_sensor.hue_bridge_living_room", "off")
        await hass.async_block_till_done()

        old = dt_util.now() - timedelta(minutes=5)
        recalled = dt_util.now()
        hass.states.async_set(
            "sensor.main_area_last_recall",
            old.isoformat(),
            {"scene_name": "49ers!", "scene_id": "scene-49ers"},
        )
        await hass.async_block_till_done()
        hass.states.async_set(
            "sensor.main_area_last_recall",
            recalled.isoformat(),
            {"scene_name": "49ers!", "scene_id": "scene-49ers"},
        )
        await hass.async_block_till_done()

        assert all(
            observer.runtime.engine.resolve(entity).layer is not None
            and observer.runtime.engine.resolve(entity).layer.kind is LayerKind.MANUAL
            for entity in leaves
        )

        # Scene rendering has made these aggregates physically ON before the homeowner
        # later turns the zone OFF. Parent context keeps this setup from becoming
        # homeowner ingress on its own.
        render_context = Context(parent_id="scene-render")
        for aggregate, aggregate_members in (
            (main, leaves),
            (celebration, (*leaves, "light.extra")),
            (holiday_all, (*leaves, "light.extra_2")),
            (
                living,
                (
                    "light.living_room_living_room_left_cabinets",
                    "light.living_room_living_room_right_cabinet_lights",
                    "light.living_room_left_ceiling_light",
                    "light.living_room_living_room_right_ceiling",
                    "light.living_room_liquor_cabinet_light",
                ),
            ),
            (
                kitchen,
                (
                    "light.kitchen_kitchen_left_cabinet_light",
                    "light.kitchen_kitchen_right_cabinet_lights",
                    "light.extra_kitchen",
                ),
            ),
            (
                cabinets,
                (
                    "light.kitchen_kitchen_left_cabinet_light",
                    "light.kitchen_kitchen_right_cabinet_lights",
                ),
            ),
        ):
            hass.states.async_set(
                aggregate,
                "on",
                {"entity_id": list(aggregate_members)},
                context=render_context,
            )
        await hass.async_block_till_done()

        later = recalled + timedelta(seconds=3)
        with patch(
            "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
            return_value=later,
        ):
            for entity in leaves:
                # Real Hue OFF receipts preserve the prior dynamic-scene marker even
                # though the light is now off. This must not erase homeowner OFF evidence.
                hass.states.async_set(entity, "off", {"dynamics": "dynamic_palette"})
                await hass.async_block_till_done()

            # Hue fans the same physical group OFF through several nested aggregates.
            hass.states.async_set(celebration, "off", {"entity_id": [*leaves, "light.extra"]})
            await hass.async_block_till_done()
            hass.states.async_set(holiday_all, "off", {"entity_id": [*leaves, "light.extra_2"]})
            await hass.async_block_till_done()
            hass.states.async_set(
                living,
                "off",
                {
                    "entity_id": [
                        "light.living_room_living_room_left_cabinets",
                        "light.living_room_living_room_right_cabinet_lights",
                        "light.living_room_left_ceiling_light",
                        "light.living_room_living_room_right_ceiling",
                        "light.living_room_liquor_cabinet_light",
                    ]
                },
            )
            await hass.async_block_till_done()
            hass.states.async_set(
                main,
                "off",
                {"entity_id": list(leaves)},
            )
            await hass.async_block_till_done()
            hass.states.async_set(
                kitchen,
                "off",
                {
                    "entity_id": [
                        "light.kitchen_kitchen_left_cabinet_light",
                        "light.kitchen_kitchen_right_cabinet_lights",
                        "light.extra_kitchen",
                    ]
                },
            )
            await hass.async_block_till_done()
        latest = observer.runtime.operations.latest_homeowner
        assert latest is not None
        assert latest["group_id"] == main
        assert latest["kind"] == "off"
        assert latest["reason"] == "released_to_hlm"
        assert all(observer.runtime.engine.resolve(entity).layer is None for entity in leaves)

        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert attrs["latest_homeowner_operation"]["group_id"] == main
        assert attrs["latest_homeowner_operation"]["kind"] == "off"
        assert attrs["latest_homeowner_operation"]["reason"] == "released_to_hlm"
        assert tuple(attrs["group_off_sequences"][main]) == tuple(sorted(leaves))
        assert attrs["command_authority"] is False

        # The underlying Daily layer is then rendered back ON by HA. This is not
        # homeowner intent and must not disarm the already-armed Main Area OFF sequence.
        daily_context = Context(parent_id="daily-render")
        for entity in leaves:
            hass.states.async_set(
                entity,
                "on",
                {"brightness": 183, "dynamics": "none"},
                context=daily_context,
            )
        for aggregate, aggregate_members in (
            (main, leaves),
            (celebration, (*leaves, "light.extra")),
            (holiday_all, (*leaves, "light.extra_2")),
            (
                living,
                (
                    "light.living_room_living_room_left_cabinets",
                    "light.living_room_living_room_right_cabinet_lights",
                    "light.living_room_left_ceiling_light",
                    "light.living_room_living_room_right_ceiling",
                    "light.living_room_liquor_cabinet_light",
                ),
            ),
            (
                kitchen,
                (
                    "light.kitchen_kitchen_left_cabinet_light",
                    "light.kitchen_kitchen_right_cabinet_lights",
                    "light.extra_kitchen",
                ),
            ),
            (
                cabinets,
                (
                    "light.kitchen_kitchen_left_cabinet_light",
                    "light.kitchen_kitchen_right_cabinet_lights",
                ),
            ),
        ):
            hass.states.async_set(
                aggregate,
                "on",
                {"entity_id": list(aggregate_members)},
                context=daily_context,
            )
        await hass.async_block_till_done()

        second = later + timedelta(seconds=10)
        with patch(
            "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
            return_value=second,
        ):
            for entity in leaves:
                hass.states.async_set(entity, "off", {"dynamics": "none"})
                await hass.async_block_till_done()

            # Match the live ordering that previously let the nested kitchen group
            # steal the second OFF after the Main Area group had already been armed.
            hass.states.async_set(
                celebration,
                "off",
                {"entity_id": [*leaves, "light.extra"]},
            )
            await hass.async_block_till_done()
            hass.states.async_set(
                holiday_all,
                "off",
                {"entity_id": [*leaves, "light.extra_2"]},
            )
            await hass.async_block_till_done()
            # Nested Kitchen Cabinet aggregate arrives before Main Area in the
            # live Hue event order. It must be vetoed while Main Area is armed.
            hass.states.async_set(
                cabinets,
                "off",
                {
                    "entity_id": [
                        "light.kitchen_kitchen_left_cabinet_light",
                        "light.kitchen_kitchen_right_cabinet_lights",
                    ]
                },
            )
            await hass.async_block_till_done()
            hass.states.async_set(main, "off", {"entity_id": list(leaves)})
            await hass.async_block_till_done()
            hass.states.async_set(
                living,
                "off",
                {
                    "entity_id": [
                        "light.living_room_living_room_left_cabinets",
                        "light.living_room_living_room_right_cabinet_lights",
                        "light.living_room_left_ceiling_light",
                        "light.living_room_living_room_right_ceiling",
                        "light.living_room_liquor_cabinet_light",
                    ]
                },
            )
            await hass.async_block_till_done()
            hass.states.async_set(
                kitchen,
                "off",
                {
                    "entity_id": [
                        "light.kitchen_kitchen_left_cabinet_light",
                        "light.kitchen_kitchen_right_cabinet_lights",
                        "light.extra_kitchen",
                    ]
                },
            )
            await hass.async_block_till_done()
            hass.states.async_set(
                cabinets,
                "off",
                {
                    "entity_id": [
                        "light.kitchen_kitchen_left_cabinet_light",
                        "light.kitchen_kitchen_right_cabinet_lights",
                    ]
                },
            )
            await hass.async_block_till_done()

        latest = observer.runtime.operations.latest_homeowner
        assert latest is not None
        assert latest["group_id"] == main
        assert latest["kind"] == "off"
        assert latest["reason"] == "created_group_manual_off"
        for entity in leaves:
            layer = observer.runtime.engine.resolve(entity).layer
            assert layer is not None
            assert layer.kind is LayerKind.MANUAL_OFF
            assert layer.group_id == main

        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert attrs["latest_homeowner_operation"]["group_id"] == main
        assert attrs["latest_homeowner_operation"]["reason"] == "created_group_manual_off"
        assert set(leaves).issubset(set(attrs["reconciliation_protected_entities"]))
        assert "light.kitchen_cabinet_lights" not in attrs["group_off_sequences"]
        assert attrs["command_authority"] is False
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_observer_reset_clears_manual_off_and_starts_fresh_correlation_epoch(tmp_path):
    leaves = ["light.reset_a", "light.reset_b"]
    group = "light.reset_group"
    hass, observer = await observer_for(tmp_path, [*leaves, group])
    try:
        observer.runtime.engine.apply_group_off(group, leaves)
        for leaf in leaves:
            observer.runtime.engine.push_manual_off(leaf)
        observer._pending_external_leaves[leaves[0]] = object()
        observer._external_group_burst_topology = {group: tuple(leaves)}

        result = await observer.async_reset_homeowner_control()

        assert result["removed_homeowner_layers"] == 2
        assert observer.runtime.engine.group_off_sequences() == {}
        assert observer._pending_external_leaves == {}
        assert observer._external_group_burst_topology == {}
        assert observer.runtime.reconciliation_protected_entities(set(leaves)) == ()
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert attrs["last_mutation_reason"] == "ownership_reset"
        assert attrs["command_authority"] is False
        assert set(attrs["post_boundary_off_entities"]) == set(observer.manual_precedence)
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_displaced_backyard_scene_with_broad_surface_burst_creates_exact_manual_group(tmp_path):
    """Hue scene displacement plus broad surface corroboration covers unchanged bulbs safely."""
    from homeassistant.util import dt as dt_util

    leaves = tuple(f"light.backyard_leaf_{index}" for index in range(12))
    aggregate = "light.holiday_backyard"
    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = PromotingHomeAssistantShadowObserver(
        hass,
        [*leaves, aggregate],
        {},
    )
    await observer.async_start()
    try:
        for index, leaf in enumerate(leaves):
            hass.states.async_set(
                leaf,
                "on",
                {
                    "brightness": 100 + index,
                    "color_mode": "xy",
                    "xy_color": [0.2 + index / 1000, 0.3],
                    "dynamics": "none",
                },
                context=Context(parent_id="topology-seed"),
            )
        await seed_group(hass, aggregate, leaves, state="on")
        hass.states.async_set("input_boolean.home_lighting_ha_guard_backyard", "off")
        hass.states.async_set("binary_sensor.hue_bridge_backyard", "off")
        await hass.async_block_till_done()

        recalled = dt_util.now().isoformat()
        hass.states.async_set(
            "sensor.backyard_last_recall",
            recalled,
            {
                "scene_name": "Forest adventure",
                "scene_id": "scene-forest",
                "active": "dynamic_palette",
            },
        )
        await hass.async_block_till_done()
        hass.states.async_set(
            "sensor.backyard_last_recall",
            recalled,
            {
                "scene_name": "Forest adventure",
                "scene_id": "scene-forest",
                "active": "inactive",
            },
        )
        await hass.async_block_till_done()

        for index, leaf in enumerate(leaves[:9]):
            hass.states.async_set(
                leaf,
                "on",
                {
                    "brightness": 180 + index,
                    "color_mode": "xy",
                    "xy_color": [0.4 + index / 1000, 0.2],
                    "dynamics": "none",
                },
            )
        await hass.async_block_till_done()
        hass.states.async_set(
            aggregate,
            "on",
            {"entity_id": list(leaves), "brightness": 188, "dynamics": "none"},
        )
        await hass.async_block_till_done()

        op = observer.runtime.operations.latest_homeowner
        assert op is not None
        assert op["group_id"] == aggregate
        assert op["kind"] == "appearance"
        assert tuple(op["affected"]) == tuple(sorted(leaves))
        for leaf in leaves:
            layer = observer.runtime.engine.resolve(leaf).layer
            assert layer is not None and layer.kind is LayerKind.MANUAL
            assert layer.group_id == aggregate

        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert set(attrs["reconciliation_protected_entities"]) == set(leaves)
        assert attrs["precedence_configured_entities"] == 0
        assert attrs["command_authority"] is False
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_broad_surface_burst_without_scene_displacement_stays_ambiguous(tmp_path):
    """Broad context-less appearance churn alone must not manufacture homeowner ownership."""
    leaves = tuple(f"light.backyard_leaf_{index}" for index in range(12))
    aggregate = "light.holiday_backyard"
    hass, observer = await observer_for(tmp_path, [*leaves, aggregate])
    try:
        for index, leaf in enumerate(leaves):
            hass.states.async_set(
                leaf,
                "on",
                {
                    "brightness": 100 + index,
                    "color_mode": "xy",
                    "xy_color": [0.2 + index / 1000, 0.3],
                    "dynamics": "none",
                },
                context=Context(parent_id="topology-seed"),
            )
        await seed_group(hass, aggregate, leaves, state="on")
        hass.states.async_set("input_boolean.home_lighting_ha_guard_backyard", "off")
        hass.states.async_set("binary_sensor.hue_bridge_backyard", "off")
        await hass.async_block_till_done()

        for index, leaf in enumerate(leaves[:9]):
            hass.states.async_set(
                leaf,
                "on",
                {
                    "brightness": 180 + index,
                    "color_mode": "xy",
                    "xy_color": [0.4 + index / 1000, 0.2],
                    "dynamics": "none",
                },
            )
        await hass.async_block_till_done()
        hass.states.async_set(
            aggregate,
            "on",
            {"entity_id": list(leaves), "brightness": 188, "dynamics": "none"},
        )
        await hass.async_block_till_done()

        assert observer.runtime.operations.latest_homeowner is None
        assert all(observer.runtime.engine.resolve(leaf).layer is None for leaf in leaves)
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert attrs["command_authority"] is False
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_guarded_scene_displacement_cannot_open_fallback_homeowner_window(tmp_path):
    """HA-owned scene displacement remains automatic when the Backyard guard is active."""
    from homeassistant.util import dt as dt_util

    leaves = tuple(f"light.backyard_leaf_{index}" for index in range(12))
    aggregate = "light.holiday_backyard"
    hass, observer = await observer_for(tmp_path, [*leaves, aggregate])
    try:
        for index, leaf in enumerate(leaves):
            hass.states.async_set(
                leaf,
                "on",
                {"brightness": 100 + index, "dynamics": "none"},
                context=Context(parent_id="topology-seed"),
            )
        await seed_group(hass, aggregate, leaves, state="on")
        hass.states.async_set("input_boolean.home_lighting_ha_guard_backyard", "on")
        hass.states.async_set("binary_sensor.hue_bridge_backyard", "off")
        await hass.async_block_till_done()

        recalled = dt_util.now().isoformat()
        hass.states.async_set(
            "sensor.backyard_last_recall",
            recalled,
            {"scene_id": "scene-forest", "active": "dynamic_palette"},
        )
        await hass.async_block_till_done()
        hass.states.async_set(
            "sensor.backyard_last_recall",
            recalled,
            {"scene_id": "scene-forest", "active": "inactive"},
        )
        await hass.async_block_till_done()

        for index, leaf in enumerate(leaves[:9]):
            hass.states.async_set(
                leaf,
                "on",
                {"brightness": 180 + index, "dynamics": "none"},
            )
        await hass.async_block_till_done()
        hass.states.async_set(
            aggregate,
            "on",
            {"entity_id": list(leaves), "brightness": 188, "dynamics": "none"},
        )
        await hass.async_block_till_done()

        assert observer.runtime.operations.latest_homeowner is None
        assert all(observer.runtime.engine.resolve(leaf).layer is None for leaf in leaves)
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_displaced_backyard_scene_survives_noisy_bridge_burst_overflow(tmp_path):
    """Unique displaced-scene evidence survives repeated Hue telemetry beyond burst capacity."""
    from homeassistant.util import dt as dt_util

    leaves = tuple(f"light.backyard_slow_leaf_{index}" for index in range(12))
    aggregate = "light.holiday_backyard"
    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = PromotingHomeAssistantShadowObserver(hass, [*leaves, aggregate], {})
    await observer.async_start()
    try:
        for index, leaf in enumerate(leaves):
            hass.states.async_set(
                leaf,
                "on",
                {
                    "brightness": 90 + index,
                    "color_mode": "xy",
                    "xy_color": [0.2 + index / 1000, 0.3],
                    "dynamics": "none",
                },
                context=Context(parent_id="topology-seed"),
            )
        await seed_group(hass, aggregate, leaves, state="on")
        hass.states.async_set("input_boolean.home_lighting_ha_guard_backyard", "off")
        hass.states.async_set("binary_sensor.hue_bridge_backyard", "off")
        await hass.async_block_till_done()

        recalled = dt_util.now().isoformat()
        hass.states.async_set(
            "sensor.backyard_last_recall",
            recalled,
            {"scene_id": "scene-forest", "active": "dynamic_palette"},
        )
        await hass.async_block_till_done()
        hass.states.async_set(
            "sensor.backyard_last_recall",
            recalled,
            {"scene_id": "scene-forest", "active": "inactive"},
        )
        await hass.async_block_till_done()

        for index, leaf in enumerate(leaves[:8]):
            hass.states.async_set(
                leaf,
                "on",
                {
                    "brightness": 170 + index,
                    "color_mode": "xy",
                    "xy_color": [0.45 + index / 1000, 0.2],
                    "dynamics": "none",
                },
            )
        hass.states.async_set(
            aggregate,
            "on",
            {"entity_id": list(leaves), "brightness": 188, "dynamics": "none"},
        )
        await hass.async_block_till_done()

        # Repeated bridge telemetry can overflow the ordinary 128-event burst.
        for step in range(140):
            leaf = leaves[step % 8]
            hass.states.async_set(
                leaf,
                "on",
                {
                    "brightness": 180 + (step % 40),
                    "color_mode": "xy",
                    "xy_color": [0.5 + (step % 20) / 1000, 0.2],
                    "dynamics": "none",
                },
            )
        await hass.async_block_till_done()

        assert observer.runtime.operations.latest_homeowner is None

        hass.states.async_set(
            leaves[8],
            "on",
            {
                "brightness": 199,
                "color_mode": "xy",
                "xy_color": [0.54, 0.2],
                "dynamics": "none",
            },
        )
        await hass.async_block_till_done()

        op = observer.runtime.operations.latest_homeowner
        assert op is not None
        assert op["group_id"] == aggregate
        assert op["kind"] == "appearance"
        assert tuple(op["affected"]) == tuple(sorted(leaves))
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert set(attrs["reconciliation_protected_entities"]) == set(leaves)
        assert attrs["command_authority"] is False
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_displaced_backyard_scene_can_correlate_preceding_leaf_fanout(tmp_path):
    """Real Hue ordering may deliver the leaf fanout before active becomes inactive."""
    from homeassistant.util import dt as dt_util

    leaves = tuple(f"light.backyard_preceding_leaf_{index}" for index in range(12))
    aggregate = "light.holiday_backyard"
    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = PromotingHomeAssistantShadowObserver(hass, [*leaves, aggregate], {})
    await observer.async_start()
    try:
        for index, leaf in enumerate(leaves):
            hass.states.async_set(
                leaf,
                "on",
                {
                    "brightness": 80 + index,
                    "color_mode": "xy",
                    "xy_color": [0.2 + index / 1000, 0.3],
                    "dynamics": "none",
                },
                context=Context(parent_id="topology-seed"),
            )
        await seed_group(hass, aggregate, leaves, state="on")
        hass.states.async_set("input_boolean.home_lighting_ha_guard_backyard", "off")
        hass.states.async_set("binary_sensor.hue_bridge_backyard", "off")
        await hass.async_block_till_done()

        recalled = dt_util.now().isoformat()
        hass.states.async_set(
            "sensor.backyard_last_recall",
            recalled,
            {"scene_id": "scene-forest", "active": "dynamic_palette"},
        )
        await hass.async_block_till_done()

        # Replacement scene reaches the bridge leaves before the scene monitor
        # reports that the previously active scene was displaced.
        for index, leaf in enumerate(leaves[:9]):
            hass.states.async_set(
                leaf,
                "on",
                {
                    "brightness": 170 + index,
                    "color_mode": "xy",
                    "xy_color": [0.45 + index / 1000, 0.2],
                    "dynamics": "none",
                },
            )
        hass.states.async_set(
            aggregate,
            "on",
            {"entity_id": list(leaves), "brightness": 188, "dynamics": "none"},
        )
        await hass.async_block_till_done()
        assert observer.runtime.operations.latest_homeowner is None

        hass.states.async_set(
            "sensor.backyard_last_recall",
            recalled,
            {"scene_id": "scene-forest", "active": "inactive"},
        )
        await hass.async_block_till_done()

        # A later aggregate receipt is enough to evaluate the retained unique
        # pre-displacement leaves; no new leaf fanout is required.
        hass.states.async_set(
            aggregate,
            "on",
            {"entity_id": list(leaves), "brightness": 189, "dynamics": "none"},
        )
        await hass.async_block_till_done()

        op = observer.runtime.operations.latest_homeowner
        assert op is not None
        assert op["group_id"] == aggregate
        assert op["kind"] == "appearance"
        assert tuple(op["affected"]) == tuple(sorted(leaves))
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert set(attrs["reconciliation_protected_entities"]) == set(leaves)
        assert attrs["command_authority"] is False
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("recall_sensor", "group_id", "guard_id", "sync_id", "leaves"),
    (
        (
            "sensor.main_area_last_recall",
            "light.holiday_main_area",
            "input_boolean.home_lighting_ha_guard_main_area",
            "binary_sensor.hue_bridge_living_room",
            ("light.universal_main_a", "light.universal_main_b"),
        ),
        (
            "sensor.path_last_recall",
            "light.holiday_path",
            "input_boolean.home_lighting_ha_guard_path",
            None,
            ("light.universal_path_a", "light.universal_path_b"),
        ),
        (
            "sensor.front_eve_last_recall",
            "light.front_eve_zone",
            "input_boolean.home_lighting_ha_guard_front_eve",
            None,
            ("light.universal_front_eve",),
        ),
        (
            "sensor.backyard_last_recall",
            "light.holiday_backyard",
            "input_boolean.home_lighting_ha_guard_backyard",
            "binary_sensor.hue_bridge_backyard",
            (
                "light.universal_backyard_a",
                "light.universal_backyard_b",
                "light.universal_backyard_c",
            ),
        ),
    ),
)
async def test_all_surfaces_use_managed_subset_for_first_and_second_group_off(
    tmp_path,
    recall_sensor,
    group_id,
    guard_id,
    sync_id,
    leaves,
):
    """Raw Hue extras must never break the universal managed-surface OFF contract."""
    from datetime import timedelta

    from homeassistant.core import Context
    from homeassistant.util import dt as dt_util

    unmanaged_extra = f"{group_id}_unmanaged_extra"
    surface = {
        "light.holiday_main_area": "main_area",
        "light.front_eve_zone": "front_eve",
        "light.holiday_path": "path",
        "light.holiday_backyard": "backyard",
    }[group_id]
    observer = PromotingHomeAssistantShadowObserver(
        HomeAssistant(str(tmp_path)),
        [*leaves, group_id],
        {},
        {surface: [unmanaged_extra]},
    )
    hass = observer.hass
    hass.config.time_zone = "America/Los_Angeles"
    await observer.async_start()
    try:
        for index, leaf in enumerate(leaves):
            hass.states.async_set(
                leaf,
                "on",
                {"brightness": 120 + index, "dynamics": "none"},
                context=Context(parent_id="seed"),
            )
        hass.states.async_set(
            unmanaged_extra,
            "on",
            {"brightness": 99, "dynamics": "none"},
            context=Context(parent_id="seed"),
        )
        await seed_group(
            hass,
            group_id,
            (*leaves, unmanaged_extra),
            state="on",
        )
        hass.states.async_set(guard_id, "off")
        if sync_id is not None:
            hass.states.async_set(sync_id, "off")
        await hass.async_block_till_done()

        old = dt_util.now() - timedelta(minutes=5)
        recalled = dt_util.now()
        hass.states.async_set(
            recall_sensor,
            old.isoformat(),
            {"scene_name": "Automatic baseline", "scene_id": "old-scene"},
        )
        await hass.async_block_till_done()
        hass.states.async_set(
            recall_sensor,
            recalled.isoformat(),
            {"scene_name": "Homeowner scene", "scene_id": "manual-scene"},
        )
        await hass.async_block_till_done()

        assert all(
            observer.runtime.engine.resolve(entity).layer is not None
            and observer.runtime.engine.resolve(entity).layer.kind is LayerKind.MANUAL
            and observer.runtime.engine.resolve(entity).layer.group_id == group_id
            for entity in leaves
        )
        assert unmanaged_extra not in observer.entity_ids

        first = recalled + timedelta(seconds=3)
        with patch(
            "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
            return_value=first,
        ):
            for leaf in leaves:
                hass.states.async_set(leaf, "off", {"dynamics": "none"})
                await hass.async_block_till_done()
            hass.states.async_set(unmanaged_extra, "off", {"dynamics": "none"})
            hass.states.async_set(
                group_id,
                "off",
                {"entity_id": [*leaves, unmanaged_extra]},
            )
            await hass.async_block_till_done()

        latest = observer.runtime.operations.latest_homeowner
        assert latest is not None
        assert latest["group_id"] == group_id
        assert latest["reason"] == "released_to_hlm"
        assert tuple(latest["affected"]) == tuple(sorted(leaves))
        assert all(observer.runtime.engine.resolve(entity).layer is None for entity in leaves)
        assert tuple(observer.runtime.engine.group_off_sequences()[group_id]) == tuple(
            sorted(leaves)
        )

        daily = Context(parent_id="daily-render")
        for leaf in leaves:
            hass.states.async_set(
                leaf,
                "on",
                {"brightness": 180, "dynamics": "none"},
                context=daily,
            )
        hass.states.async_set(
            unmanaged_extra,
            "on",
            {"brightness": 180, "dynamics": "none"},
            context=daily,
        )
        hass.states.async_set(
            group_id,
            "on",
            {"entity_id": [*leaves, unmanaged_extra]},
            context=daily,
        )
        await hass.async_block_till_done()

        second = first + timedelta(seconds=10)
        with patch(
            "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
            return_value=second,
        ):
            for leaf in leaves:
                hass.states.async_set(leaf, "off", {"dynamics": "none"})
                await hass.async_block_till_done()
            hass.states.async_set(unmanaged_extra, "off", {"dynamics": "none"})
            hass.states.async_set(
                group_id,
                "off",
                {"entity_id": [*leaves, unmanaged_extra]},
            )
            await hass.async_block_till_done()

        latest = observer.runtime.operations.latest_homeowner
        assert latest is not None
        assert latest["group_id"] == group_id
        assert latest["reason"] == "created_group_manual_off"
        assert tuple(latest["affected"]) == tuple(sorted(leaves))
        for leaf in leaves:
            layer = observer.runtime.engine.resolve(leaf).layer
            assert layer is not None
            assert layer.kind is LayerKind.MANUAL_OFF
            assert layer.group_id == group_id
        assert unmanaged_extra not in set(
            hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes[
                "reconciliation_protected_entities"
            ]
        )
        assert hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes["command_authority"] is False
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_backyard_first_group_off_survives_real_hue_fanout_beyond_two_seconds(tmp_path):
    """Canonical surface OFF uses bounded retained evidence across ordinary burst rollover."""
    from datetime import timedelta

    from homeassistant.core import Context, HomeAssistant
    from homeassistant.util import dt as dt_util

    group_id = "light.holiday_backyard"
    guard_id = "input_boolean.home_lighting_ha_guard_backyard"
    sync_id = "binary_sensor.hue_bridge_backyard"
    recall_sensor = "sensor.backyard_last_recall"
    leaves = tuple(f"light.slow_backyard_{index}" for index in range(11))
    unmanaged_extra = "light.slow_backyard_festavia"

    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    observer = PromotingHomeAssistantShadowObserver(
        hass,
        [*leaves, group_id],
        {},
        {"backyard": [unmanaged_extra]},
    )
    await observer.async_start()
    try:
        for index, leaf in enumerate(leaves):
            hass.states.async_set(
                leaf,
                "on",
                {"brightness": 100 + index, "dynamics": "none"},
                context=Context(parent_id="seed"),
            )
        hass.states.async_set(
            unmanaged_extra,
            "on",
            {"brightness": 150, "dynamics": "none"},
            context=Context(parent_id="seed"),
        )
        await seed_group(hass, group_id, (*leaves, unmanaged_extra), state="on")
        hass.states.async_set(guard_id, "off")
        hass.states.async_set(sync_id, "off")
        await hass.async_block_till_done()

        baseline = dt_util.now()
        hass.states.async_set(
            recall_sensor,
            (baseline - timedelta(minutes=5)).isoformat(),
            {"scene_name": "Daily", "scene_id": "daily"},
        )
        await hass.async_block_till_done()
        hass.states.async_set(
            recall_sensor,
            baseline.isoformat(),
            {"scene_name": "Manual", "scene_id": "manual"},
        )
        await hass.async_block_till_done()

        assert all(
            observer.runtime.engine.resolve(entity).layer is not None
            and observer.runtime.engine.resolve(entity).layer.kind is LayerKind.MANUAL
            and observer.runtime.engine.resolve(entity).layer.group_id == group_id
            for entity in leaves
        )

        # Reproduce the live 2026-09-27 ordering: about half the leaves arrive in
        # the first ordinary burst, the rest after the 2-second burst rolls over,
        # and only then does the canonical aggregate finally report OFF.
        first_wave = baseline + timedelta(seconds=3)
        with patch(
            "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
            return_value=first_wave,
        ):
            for leaf in leaves[:6]:
                hass.states.async_set(leaf, "off", {"dynamics": "none"})
                await hass.async_block_till_done()

        second_wave = first_wave + timedelta(seconds=2.6)
        with patch(
            "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
            return_value=second_wave,
        ):
            for leaf in leaves[6:]:
                hass.states.async_set(leaf, "off", {"dynamics": "none"})
                await hass.async_block_till_done()
            hass.states.async_set(unmanaged_extra, "off", {"dynamics": "none"})
            await hass.async_block_till_done()

        aggregate_off = second_wave + timedelta(seconds=0.4)
        with patch(
            "custom_components.home_lighting_manager.promotion_observer.dt_util.now",
            return_value=aggregate_off,
        ):
            hass.states.async_set(
                group_id,
                "off",
                {"entity_id": [*leaves, unmanaged_extra]},
            )
            await hass.async_block_till_done()

        latest = observer.runtime.operations.latest_homeowner
        assert latest is not None
        assert latest["group_id"] == group_id
        assert latest["reason"] == "released_to_hlm"
        assert tuple(latest["affected"]) == tuple(sorted(leaves))
        assert all(observer.runtime.engine.resolve(entity).layer is None for entity in leaves)
        assert tuple(observer.runtime.engine.group_off_sequences()[group_id]) == tuple(
            sorted(leaves)
        )
        attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
        assert attrs["command_authority"] is False
        assert unmanaged_extra not in attrs["reconciliation_protected_entities"]
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()
