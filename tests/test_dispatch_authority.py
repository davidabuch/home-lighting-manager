"""F01: exercise final dispatch authority through the real migration renderer."""

import pytest
from test_effective_rendering import evaluate, publish, setup, touched
from test_layered_operations import operation

from custom_components.home_lighting_manager.model import LayerKind
from custom_components.home_lighting_reconciliation import rendering
from custom_components.home_lighting_reconciliation.engine import GROUPS


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", GROUPS)
@pytest.mark.parametrize("owner", ["daily", "holiday"])
@pytest.mark.parametrize("kind", ["appearance", "off"])
@pytest.mark.parametrize("boundary", ["owner_read", "hue_read", "guard", "marker"])
async def test_new_manual_at_each_render_await_prevents_dispatch(
    rig, monkeypatch, surface, owner, kind, boundary
):
    runtime, members = setup(rig, surface, owner)
    victim = members[0]
    publish(rig, runtime, members)
    injected = False

    def accept_manual():
        nonlocal injected
        if injected:
            return
        injected = True
        result = runtime.observe_operation(operation(1, members=(victim,), kind=kind))
        assert result.mutated
        rig.set(victim, "off" if kind == "off" else "on", {"brightness": 91})
        publish(rig, runtime, members)

    if boundary == "owner_read":
        original = rig.renderer.owners

        async def owners():
            result = await original()
            accept_manual()
            return result

        monkeypatch.setattr(rig.renderer, "owners", owners)
    elif boundary == "hue_read":
        original = rig.renderer.hue.read

        async def read(*args):
            result = await original(*args)
            accept_manual()
            return result

        monkeypatch.setattr(rig.renderer.hue, "read", read)
    elif boundary == "guard":
        async def guard(call):
            if call.domain == "input_boolean" and call.service == "turn_on":
                accept_manual()

        rig.before_service = guard
    else:
        original = rendering.mark_command_consequence

        async def mark(hass, entities, guard, op):
            await original(hass, entities, guard, op)
            if victim in entities:
                accept_manual()

        monkeypatch.setattr(rendering, "mark_command_consequence", mark)

    await evaluate(rig, surface)
    assert injected
    layer = runtime.engine.resolve(victim).layer
    assert layer.kind is (LayerKind.MANUAL_OFF if kind == "off" else LayerKind.MANUAL)
    assert victim in runtime.reconciliation_protected_entities()
    assert rig.get(victim).state == ("off" if kind == "off" else "on")
    assert rig.get(victim).attributes["brightness"] == 91
    assert victim not in touched(rig)
    assert not any(service == "scene.turn_on" for service, _ in rig.lights())


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", GROUPS)
@pytest.mark.parametrize("change", ["unavailable", "presentation", "guard", "generation", "owner"])
async def test_marker_await_rechecks_current_dispatch_requirements(rig, monkeypatch, surface, change):
    runtime, members = setup(rig, surface, "holiday")
    victim = members[0]
    publish(rig, runtime, members)
    original = rendering.mark_command_consequence
    injected = False

    async def mark(hass, entities, guard, op):
        nonlocal injected
        await original(hass, entities, guard, op)
        if injected:
            return
        injected = True
        if change == "unavailable":
            rig.set(victim, "unavailable")
        elif change == "presentation":
            script = rig.scripts["home_lighting_apply_" + surface + "_baseline"]
            script.sequence = [*script.sequence, {"delay": {"seconds": 1}}]
        elif change == "guard":
            rig.set(guard, "off")
        elif change == "owner":
            rig.set("input_boolean.home_lighting_holiday_active", "off")
        else:
            runtime.engine.next_generation()
            publish(rig, runtime, members)

    monkeypatch.setattr(rendering, "mark_command_consequence", mark)
    await evaluate(rig, surface)
    assert injected
    assert not any(service == "scene.turn_on" for service, _ in rig.lights())
    assert victim not in touched(rig)


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["main_area", "path", "backyard"])
async def test_selective_scene_marker_await_preserves_new_manual_member(rig, monkeypatch, surface):
    runtime, members = setup(rig, surface, "holiday")
    assert runtime.observe_operation(operation(1, members=(members[0],), kind="off")).mutated
    publish(rig, runtime, members)
    victim = members[1]
    original = rendering.mark_command_consequence

    async def mark(hass, entities, guard, op):
        await original(hass, entities, guard, op)
        if victim in entities:
            assert runtime.observe_operation(operation(2, members=(victim,), kind="off")).mutated
            publish(rig, runtime, members)

    monkeypatch.setattr(rendering, "mark_command_consequence", mark)
    await evaluate(rig, surface)
    assert runtime.engine.resolve(victim).layer.kind is LayerKind.MANUAL_OFF
    assert victim not in touched(rig)
    assert not any(service == "scene.turn_on" for service, _ in rig.lights())


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["main_area", "front_eve"])
async def test_static_member_unavailable_during_marker_await_is_not_commanded(rig, monkeypatch, surface):
    runtime, members = setup(rig, surface)
    victim = members[0]
    publish(rig, runtime, members)
    original = rendering.mark_command_consequence

    async def mark(hass, entities, guard, op):
        await original(hass, entities, guard, op)
        if victim in entities:
            rig.set(victim, "unavailable")

    monkeypatch.setattr(rendering, "mark_command_consequence", mark)
    await evaluate(rig, surface)
    assert rig.get(victim).state == "unavailable"
    assert victim not in touched(rig)
