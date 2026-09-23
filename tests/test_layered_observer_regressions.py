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

        # The quarantine is bounded: a genuinely new command later remains eligible.
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
            )
            await hass.async_block_till_done()
            hass.states.async_set(
                group,
                "on",
                {"entity_id": [leaf], "brightness": 180},
            )
            await hass.async_block_till_done()

            layer = observer.runtime.engine.resolve(leaf).layer
            assert layer is not None and layer.kind is LayerKind.MANUAL
            attrs = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes
            assert attrs["nightly_boundary_settling"] is False
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
