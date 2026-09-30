"""Cross-surface real HA observation -> ownership -> legacy renderer feedback.

Device services are simulated; observers, correlation callbacks, projection and
renderer are real. No assertion infers ownership from the simulated bulb state.
"""

import asyncio
from datetime import timedelta

import pytest
from homeassistant.core import Context
from homeassistant.util import dt as dt_util
from test_effective_rendering import evaluate, touched

from custom_components.home_lighting_manager.ha_observer import (
    DIAGNOSTIC_ENTITY_ID,
    MANAGED_SURFACE_GROUPS,
)
from custom_components.home_lighting_manager.intent_policy import (
    IntentDisposition,
    IntentEvidence,
    IntentEvidenceKind,
    classify_intent,
)
from custom_components.home_lighting_manager.model import Appearance, LayerKind, OwnershipLayer
from custom_components.home_lighting_manager.promotion_observer import (
    PromotingHomeAssistantShadowObserver,
)
from custom_components.home_lighting_reconciliation.const import EVALUATORS, TRANSIENTS


async def start(rig, surface):
    members = tuple(rig.members[surface])
    group = MANAGED_SURFACE_GROUPS[surface]
    for entity in {*TRANSIENTS, *EVALUATORS}:
        rig.set(entity, "off", {"current": 0})
    for name in MANAGED_SURFACE_GROUPS:
        rig.set("sensor." + name + "_last_recall", "2026-09-29T12:00:00+00:00")
    original = rig.service

    async def physical_scene(call):
        await original(call)
        scene = call.data["entity_id"]
        if isinstance(scene, list):
            scene = scene[0]
        _, info = await rig.renderer.hue.read([scene], [surface])
        for target, action in info[scene]["actions"].items():
            rig.set(target, "on" if action["action"]["on"]["on"] else "off")

    rig.hass.services.async_register("scene", "turn_on", physical_scene)
    await rig.hass.async_block_till_done()
    observer = PromotingHomeAssistantShadowObserver(
        rig.hass, [*members, group], dict.fromkeys(members, 250)
    )
    await observer.async_start()
    for entity in members:
        observer.runtime.engine.push(entity, OwnershipLayer(
            "daily:" + entity, "daily", LayerKind.AUTOMATIC, 1, 0,
            appearance=Appearance(True, brightness=100),
        ))
    rig.set("input_boolean.home_lighting_" + surface + "_window", "on")

    async def mark(call):
        accepted = observer.register_renderer_command_consequences(
            call.data["entity_ids"], call.data["guard_entity"], call.data["operation"]
        )
        assert accepted == len(call.data["entity_ids"])

    rig.hass.services.async_register("home_lighting_manager", "mark_command_consequence", mark)
    observer._publish_diagnostics()
    rig.observer = observer
    return observer, members, group


async def receipt(rig, group, entity, operation, *, user=False):
    hold = rig.observer._surface_group_off_hold_seconds(entity, operation)
    rig.hass.states.async_set(entity, "off" if operation == "off" else "on",
                               {"brightness": 190},
                               context=Context(user_id="homeowner") if user else Context())
    await rig.hass.async_block_till_done()
    if not user:
        old = rig.get(group)
        aggregate_state = "on" if any(
            (state := rig.get(member)) is not None and state.state == "on"
            for member in old.attributes["entity_id"]
        ) else "off"
        rig.hass.states.async_set(group, aggregate_state, {
            **dict(old.attributes), "brightness": old.attributes.get("brightness", 0) + 1,
        })
        await rig.hass.async_block_till_done()
        # Real correlation timer: avoid hand-calling a callback before its deadline.
        if entity in rig.observer.pending_intent_entities():
            await asyncio.sleep(hold + 0.05)
            await rig.hass.async_block_till_done()


def assert_owner(observer, entity, kind):
    layer = observer.runtime.engine.resolve(entity).layer
    assert layer is not None and layer.kind is kind
    assert layer.owner == ("daily" if kind is LayerKind.AUTOMATIC else "manual")
    assert (entity in observer.runtime.reconciliation_protected_entities()) == (
        kind is not LayerKind.AUTOMATIC
    )
    assert observer.runtime.engine.layers(entity)[0].owner == "daily"


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", MANAGED_SURFACE_GROUPS)
@pytest.mark.parametrize("user", [True, False], ids=["HA-user", "Hue-contextless"])
async def test_manual_peel_render_feedback_and_second_off(rig, surface, user):
    observer, members, group = await start(rig, surface)
    entity = members[0]
    try:
        await receipt(rig, group, entity, "appearance", user=user)
        assert_owner(observer, entity, LayerKind.MANUAL)
        assert observer.runtime.engine.resolve(entity).appearance.brightness == 190
        await receipt(rig, group, entity, "off", user=user)
        assert_owner(observer, entity, LayerKind.AUTOMATIC)
        assert observer.runtime.operations.latest_homeowner["reason"] == "released_manual"
        await evaluate(rig, surface)
        await rig.hass.async_block_till_done()
        assert rig.get(entity).state == "on"
        assert_owner(observer, entity, LayerKind.AUTOMATIC)
        ledger = rig.get(DIAGNOSTIC_ENTITY_ID).attributes["recent_evidence"]
        assert any(e["normalized_causality"]["renderer_consequence"]
                   and not e["allows_homeowner_mutation"] for e in ledger)
        await receipt(rig, group, entity, "off", user=user)
        assert_owner(observer, entity, LayerKind.MANUAL_OFF)
        assert observer.runtime.operations.latest_homeowner["reason"] == "created_manual_off"
        await evaluate(rig, surface)
        assert entity not in touched(rig)
        assert rig.get(entity).state == "off"
        assert rig.get(DIAGNOSTIC_ENTITY_ID).attributes["command_authority"] is False
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", MANAGED_SURFACE_GROUPS)
async def test_boundary_closes_on_current_renderer_evidence_not_elapsed_time(rig, surface, monkeypatch):
    observer, members, group = await start(rig, surface)
    entity = members[0]
    boundary = dt_util.now()
    try:
        observer._handle_nightly_boundary(boundary)
        await rig.hass.async_block_till_done()
        # Even after 17 hours raw ON cannot manufacture ownership or clear causality.
        later = boundary + timedelta(hours=17)
        monkeypatch.setattr(dt_util, "now", lambda: later)
        rig.set(entity, "on", {"brightness": 99})
        await rig.hass.async_block_till_done()
        assert entity in observer._post_boundary_off_entities
        assert_owner(observer, entity, LayerKind.AUTOMATIC)
        await evaluate(rig, surface)
        await rig.hass.async_block_till_done()
        assert entity not in observer._post_boundary_off_entities
        assert_owner(observer, entity, LayerKind.AUTOMATIC)
        await receipt(rig, group, entity, "off")
        assert_owner(observer, entity, LayerKind.MANUAL_OFF)
        assert observer.runtime.operations.latest_homeowner["reason"] == "created_manual_off"
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", MANAGED_SURFACE_GROUPS)
@pytest.mark.parametrize("user", [True, False], ids=["HA-user", "Hue-contextless"])
async def test_rapid_user_peels_do_not_inherit_sibling_restore_markers(rig, surface, user):
    observer, members, group = await start(rig, surface)
    try:
        for entity in members:
            await receipt(rig, group, entity, "appearance", user=True)
        for index, entity in enumerate(members[:3]):
            await receipt(rig, group, entity, "off", user=user)
            assert_owner(observer, entity, LayerKind.AUTOMATIC)
            for sibling in members[index + 1:]:
                assert_owner(observer, sibling, LayerKind.MANUAL)
            await evaluate(rig, surface)
            await rig.hass.async_block_till_done()
            assert rig.get(entity).state == "on"
            assert_owner(observer, entity, LayerKind.AUTOMATIC)
        for entity in members[:3]:
            assert entity not in observer.runtime.reconciliation_protected_entities()
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", MANAGED_SURFACE_GROUPS)
async def test_pending_correlation_defers_render_without_creating_manual(rig, surface):
    observer, members, group = await start(rig, surface)
    entity = members[0]
    try:
        rig.set(entity, "on", {"brightness": 150})
        await rig.hass.async_block_till_done()
        assert entity in observer.pending_intent_entities()
        assert_owner(observer, entity, LayerKind.AUTOMATIC)
        await evaluate(rig, surface)
        assert entity not in touched(rig)
        assert entity not in observer.runtime.reconciliation_protected_entities()
        projected = rig.get(DIAGNOSTIC_ENTITY_ID).attributes["effective_ownership"]
        assert projected["entities"][entity]["kind"] == "automatic"
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
async def test_restart_ignores_legacy_persisted_shutdown_epoch(rig):
    observer, members, group = await start(rig, "backyard")
    observer._post_boundary_off_entities = set(members)
    await observer.async_save()
    raw = await observer.store.async_load()
    assert "post_boundary_off_entities" not in raw
    await observer.async_shutdown()
    # A v0.2.31 payload must also be accepted without restoring runtime quarantine.
    raw["post_boundary_off_entities"] = list(members)
    await observer.store.async_save(raw)
    replacement = PromotingHomeAssistantShadowObserver(rig.hass, [*members, group],
                                                       dict.fromkeys(members, 250))
    await replacement.async_start()
    try:
        assert not replacement._post_boundary_off_entities
        assert not replacement.runtime.engine.group_off_sequences()
        await receipt(rig, group, members[0], "appearance", user=True)
        assert replacement.runtime.engine.resolve(members[0]).layer.kind is LayerKind.MANUAL
    finally:
        await replacement.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.parametrize("flag", ["boundary_settling", "boundary_shutdown_pending",
                                  "scene_rendering", "dynamic_telemetry"])
def test_canonical_policy_affirmative_intent_beats_temporal_quarantine(flag):
    explicit = IntentEvidence(IntentEvidenceKind.EXPLICIT_HOMEOWNER_COMMAND, **{flag: True})
    assert classify_intent(explicit).disposition is IntentDisposition.HOMEOWNER_INTENT
    ambiguous = IntentEvidence(IntentEvidenceKind.UNKNOWN, **{flag: True})
    assert not classify_intent(ambiguous).allows_homeowner_mutation


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", MANAGED_SURFACE_GROUPS)
async def test_guard_without_exact_receipt_cannot_veto_user_intent(rig, surface):
    observer, members, group = await start(rig, surface)
    try:
        rig.set("input_boolean.home_lighting_ha_guard_" + surface, "on")
        await receipt(rig, group, members[0], "off", user=True)
        assert_owner(observer, members[0], LayerKind.MANUAL_OFF)
        latest = rig.get(DIAGNOSTIC_ENTITY_ID).attributes["recent_evidence"][-1]
        assert latest["intent"] == "homeowner_intent"
        assert latest["mutated"]
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["main_area", "backyard"])
async def test_sync_churn_is_evidence_not_new_manual_intent(rig, surface):
    observer, members, group = await start(rig, surface)
    try:
        rig.set("binary_sensor.hue_bridge_" + (
            "living_room" if surface == "main_area" else "backyard"), "on")
        await receipt(rig, group, members[0], "appearance")
        assert_owner(observer, members[0], LayerKind.AUTOMATIC)
        assert not observer.runtime.operations.latest_homeowner
        ledger = rig.get(DIAGNOSTIC_ENTITY_ID).attributes["recent_evidence"]
        assert any(e["normalized_causality"]["structural_activity"]
                   and not e["allows_homeowner_mutation"] for e in ledger)
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
async def test_pending_evidence_expires_without_another_physical_event(rig):
    observer, members, group = await start(rig, "path")
    try:
        rig.set(members[0], "on", {"brightness": 172})
        await rig.hass.async_block_till_done()
        assert members[0] in rig.get(DIAGNOSTIC_ENTITY_ID).attributes["pending_intent_entities"]
        await asyncio.sleep(2.1)
        await rig.hass.async_block_till_done()
        assert not rig.get(DIAGNOSTIC_ENTITY_ID).attributes["pending_intent_entities"]
        assert_owner(observer, members[0], LayerKind.AUTOMATIC)
        assert not observer.runtime.operations.latest_homeowner
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", MANAGED_SURFACE_GROUPS)
async def test_reconciliation_receipts_use_same_consequence_pipeline(rig, surface):
    observer, members, _group = await start(rig, surface)
    original_read = rig.renderer.hue.read

    async def read_evidence(scenes, surfaces):
        # The old fixture assigns every scene to surfaces[0]. An inspection requests
        # all four groups, even when only this surface currently has a scene. Real Hue
        # scene actions belong to their resource group, not the first requested group.
        _, metadata = await original_read(scenes, [surface])
        return {s: rig.members[s] for s in surfaces}, metadata

    rig.renderer.hue.read = read_evidence
    try:
        check, owners = await rig.renderer.inspect()
        commands = [command for command in check.commands if command.surface == surface]
        assert commands
        sent = await rig.renderer.repair(commands[0], owners, lambda: True)
        assert sent
        await rig.hass.async_block_till_done()
        old_group = rig.get(_group)
        rig.set(_group, "on", {**dict(old_group.attributes), "repair_receipt": 1})
        await rig.hass.async_block_till_done()
        await asyncio.sleep(2.05)
        await rig.hass.async_block_till_done()
        assert any(rig.get(entity).state == "on" for entity in members)
        for entity in members:
            assert_owner(observer, entity, LayerKind.AUTOMATIC)
        assert not observer.runtime.operations.latest_homeowner
        ledger = rig.get(DIAGNOSTIC_ENTITY_ID).attributes["recent_evidence"]
        assert any(entry["normalized_causality"]["renderer_consequence"] for entry in ledger)
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", MANAGED_SURFACE_GROUPS)
@pytest.mark.parametrize("user", [True, False], ids=["HA-user", "Hue-contextless"])
async def test_scene_identity_then_individual_peel_during_scene_settling(rig, surface, user):
    observer, members, group = await start(rig, surface)
    try:
        rig.set("input_boolean.home_lighting_ha_guard_" + surface, "off")
        sensor = "sensor." + surface + "_last_recall"
        now = dt_util.now()
        rig.set(sensor, (now - timedelta(seconds=3)).isoformat(), {"scene_id": "previous"})
        await rig.hass.async_block_till_done()
        rig.set(sensor, now.isoformat(), {"scene_id": "manual-scene", "scene_name": "Manual"})
        await rig.hass.async_block_till_done()
        for entity in members:
            assert_owner(observer, entity, LayerKind.MANUAL)
            assert observer.runtime.engine.resolve(entity).appearance.scene_id == "manual-scene"
        entity = members[0]
        await receipt(rig, group, entity, "off", user=user)
        assert_owner(observer, entity, LayerKind.AUTOMATIC)
        for sibling in members[1:]:
            assert_owner(observer, sibling, LayerKind.MANUAL)
        await evaluate(rig, surface)
        await rig.hass.async_block_till_done()
        assert rig.get(entity).state == "on"
        assert_owner(observer, entity, LayerKind.AUTOMATIC)
        expected = "released_to_hlm" if not user and len(members) == 1 else "released_manual"
        assert observer.runtime.operations.latest_homeowner["reason"] == expected
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", MANAGED_SURFACE_GROUPS)
async def test_recovery_off_with_live_marker_is_not_boundary_completion(rig, surface):
    observer, members, _group = await start(rig, surface)
    entity = members[0]
    try:
        observer._post_boundary_off_entities.add(entity)
        guard = "input_boolean.home_lighting_ha_guard_" + surface
        rig.set(guard, "on")
        rig.set(entity, "unavailable")
        await rig.hass.async_block_till_done()
        observer.register_renderer_command_consequences([entity], guard, "appearance")
        rig.set(entity, "on", {"brightness": 100})
        await rig.hass.async_block_till_done()
        assert entity in observer._post_boundary_off_entities
        assert_owner(observer, entity, LayerKind.AUTOMATIC)
        assert not observer.runtime.operations.latest_homeowner
        assert rig.get(DIAGNOSTIC_ENTITY_ID).attributes["recent_evidence"][-1]["canonical_reason"] == (
            "availability/recovery telemetry is not homeowner intent"
        )
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
async def test_authority_is_published_before_disk_checkpoint_completes(rig, monkeypatch):
    observer, members, _group = await start(rig, "main_area")
    original_save = observer.store.async_save
    release = asyncio.Event()
    entered = asyncio.Event()

    async def slow_save(payload):
        entered.set()
        await release.wait()
        await original_save(payload)

    monkeypatch.setattr(observer.store, "async_save", slow_save)
    try:
        rig.hass.states.async_set(members[0], "on", {"brightness": 213},
                                   context=Context(user_id="homeowner"))
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert_owner(observer, members[0], LayerKind.MANUAL)
        view = rig.get(DIAGNOSTIC_ENTITY_ID).attributes["effective_ownership"]
        assert view["entities"][members[0]]["protected"] is True
        await evaluate(rig, "main_area")
        assert members[0] not in touched(rig)
    finally:
        release.set()
        await rig.hass.async_block_till_done()
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", MANAGED_SURFACE_GROUPS)
async def test_renderer_off_does_not_create_manual_and_new_appearance_is_eligible(rig, surface):
    observer, members, group = await start(rig, surface)
    entity = members[0]
    try:
        rig.hass.states.async_set(entity, "on", {"brightness": 100},
                                   context=Context(parent_id="automatic-seed"))
        await rig.hass.async_block_till_done()
        guard = "input_boolean.home_lighting_ha_guard_" + surface
        rig.set(guard, "on")
        assert observer.register_renderer_command_consequences([entity], guard, "off") == 1
        rig.set(entity, "off")
        await rig.hass.async_block_till_done()
        assert_owner(observer, entity, LayerKind.AUTOMATIC)
        assert not observer.runtime.operations.latest_homeowner
        await receipt(rig, group, entity, "appearance")
        assert_owner(observer, entity, LayerKind.MANUAL)
        assert observer.runtime.engine.resolve(entity).appearance.brightness == 190
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", MANAGED_SURFACE_GROUPS)
@pytest.mark.parametrize("state", ["unavailable", "unknown", None], ids=["unavailable", "unknown", "missing"])
async def test_scene_recall_never_owns_unavailable_member(rig, surface, state):
    observer, members, _group = await start(rig, surface)
    try:
        if state is None:
            rig.hass.states.async_remove(members[0])
        else:
            rig.set(members[0], state)
        await rig.hass.async_block_till_done()
        sensor = "sensor." + surface + "_last_recall"
        rig.set(sensor, dt_util.now().isoformat(), {"scene_id": "new-manual-scene"})
        await rig.hass.async_block_till_done()
        assert_owner(observer, members[0], LayerKind.AUTOMATIC)
        for sibling in members[1:]:
            assert_owner(observer, sibling, LayerKind.MANUAL)
        rig.set(members[0], "on", {"brightness": 100})
        await rig.hass.async_block_till_done()
        assert_owner(observer, members[0], LayerKind.AUTOMATIC)
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", MANAGED_SURFACE_GROUPS)
async def test_exact_scene_group_releases_barrier_then_second_off_during_restore_guard(rig, surface):
    observer, members, group = await start(rig, surface)
    try:
        rig.set("sensor." + surface + "_last_recall", dt_util.now().isoformat(),
                {"scene_id": "manual-group-scene"})
        await rig.hass.async_block_till_done()
        for entity in members:
            rig.set(entity, "on", {"brightness": 190})
        await rig.hass.async_block_till_done()
        for entity in members:
            assert_owner(observer, entity, LayerKind.MANUAL)
            rig.set(entity, "off", {"brightness": 190})
        await rig.hass.async_block_till_done()
        rig.set(group, "off", {"entity_id": list(members), "receipt": 1})
        await rig.hass.async_block_till_done()
        assert observer.runtime.operations.latest_homeowner["reason"] == "released_to_hlm"
        assert not observer.pending_intent_entities()
        assert observer.runtime.engine.group_off_sequences()[group] == tuple(sorted(members))
        await evaluate(rig, surface)
        await rig.hass.async_block_till_done()
        for entity in members:
            assert rig.get(entity).state == "on"
            assert_owner(observer, entity, LayerKind.AUTOMATIC)
        assert rig.get("input_boolean.home_lighting_ha_guard_" + surface).state == "on"
        for entity in members:
            rig.set(entity, "off", {"brightness": 190})
        await rig.hass.async_block_till_done()
        rig.set(group, "off", {"entity_id": list(members), "receipt": 2})
        await rig.hass.async_block_till_done()
        assert observer.runtime.operations.latest_homeowner["reason"] == "created_group_manual_off"
        assert not observer.pending_intent_entities()
        for entity in members:
            assert_owner(observer, entity, LayerKind.MANUAL_OFF)
        await evaluate(rig, surface)
        assert not set(members) & touched(rig)
        for entity in members:
            assert rig.get(entity).state == "off"
            assert_owner(observer, entity, LayerKind.MANUAL_OFF)
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
async def test_repair_final_recheck_preserves_unrelated_legacy_overlay_view(rig):
    observer, members, _group = await start(rig, "main_area")
    original_read = rig.renderer.hue.read

    async def read_evidence(scenes, surfaces):
        _, metadata = await original_read(scenes, ["main_area"])
        return {surface: rig.members[surface] for surface in surfaces}, metadata

    rig.renderer.hue.read = read_evidence
    try:
        rig.set("sensor.nfl_san_francisco_49ers", "IN")
        for entity in members:
            observer.runtime.engine.push(entity, OwnershipLayer(
                "game:" + entity, "49ers", LayerKind.AUTOMATIC, 1, 1,
                appearance=Appearance(True), precedence=200,
            ))
        observer._publish_diagnostics()
        await rig.hass.async_block_till_done()
        check, owners = await rig.renderer.inspect()
        commands = [command for command in check.commands if command.surface == "main_area"]
        assert commands
        assert "light.festavia_permanent_1" in owners["backyard"]["excluded_entities"]
        assert await rig.renderer.repair(commands[0], owners, lambda: True)
        await rig.hass.async_block_till_done()
        assert not observer.runtime.reconciliation_protected_entities()
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", MANAGED_SURFACE_GROUPS)
async def test_initial_discovery_fanout_is_recovery_then_new_intent_is_eligible(tmp_path, surface):
    from conftest import seed_observed_lights
    from homeassistant.core import HomeAssistant

    hass = HomeAssistant(str(tmp_path))
    group, entity = MANAGED_SURFACE_GROUPS[surface], "light." + surface + "_new_device"
    observer = PromotingHomeAssistantShadowObserver(hass, [entity, group], {entity: 250})
    await observer.async_start()
    try:
        hass.states.async_set(entity, "on", {"brightness": 100})
        await hass.async_block_till_done()
        hass.states.async_set(group, "on", {"entity_id": [entity]})
        await hass.async_block_till_done()
        assert observer.runtime.engine.resolve(entity).layer is None
        assert not observer.runtime.operations.latest_homeowner
        assert not observer.pending_intent_entities()
        assert not observer.runtime.reconciliation_protected_entities()
        ledger = hass.states.get(DIAGNOSTIC_ENTITY_ID).attributes["recent_evidence"]
        assert all(e["evidence_kind"] == "recovery_telemetry" and not e["mutated"] for e in ledger)
        # Controls now provide ready/inactive structural evidence; established states
        # are retained. This is a NEW receipt, not promotion/replay of discovery.
        seed_observed_lights(hass, [entity, group])
        await hass.async_block_till_done()
        hass.states.async_set(entity, "on", {"brightness": 200})
        await hass.async_block_till_done()
        hass.states.async_set(group, "on", {"entity_id": [entity], "brightness": 200})
        await hass.async_block_till_done()
        assert observer.runtime.engine.resolve(entity).layer.kind is LayerKind.MANUAL
        assert observer.runtime.engine.resolve(entity).appearance.brightness == 200
        assert observer.runtime.reconciliation_protected_entities() == (entity,)
    finally:
        await observer.async_shutdown()
        await hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["main_area", "backyard"])
async def test_unknown_structural_session_is_not_assumed_off(rig, surface):
    observer, members, group = await start(rig, surface)
    try:
        rig.set("binary_sensor.hue_bridge_" + (
            "living_room" if surface == "main_area" else "backyard"), "unavailable")
        await receipt(rig, group, members[0], "appearance")
        assert_owner(observer, members[0], LayerKind.AUTOMATIC)
        assert not observer.runtime.operations.latest_homeowner
        ledger = rig.get(DIAGNOSTIC_ENTITY_ID).attributes["recent_evidence"]
        assert any(e["normalized_causality"]["structural_state_unknown"]
                   and not e["allows_homeowner_mutation"] for e in ledger)
    finally:
        await observer.async_shutdown()
        await rig.hass.async_block_till_done()
