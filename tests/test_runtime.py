import asyncio
from copy import deepcopy

import pytest
from test_engine import world

from custom_components.home_lighting_reconciliation.engine import verify
from custom_components.home_lighting_reconciliation.runtime import Reconciler


class FakeAdapter:
    def __init__(self, success=True):
        self.args = world()
        self.sent = []
        self.history = []
        self.success = success
        self.suppressed = {}
        self.before_repair = None
        self.args[1]["light.kitchen_kitchen_right_cabinet_lights"]["state"] = "off"

    def now(self):
        return "2026-09-08T20:00:00-07:00"

    def publish(self, diag):
        self.history.append(deepcopy(diag))

    async def inspect(self):
        return verify(*self.args, self.suppressed), deepcopy(self.args[0])

    async def repair(self, c, owners, valid):
        if self.before_repair:
            await self.before_repair()
        if not valid() or owners != self.args[0] or c.surface in self.suppressed:
            return False
        self.sent.append(c)
        if self.success:
            self.args[1][c.entity]["state"] = "on"
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [False, True])
async def test_repair_budget_and_final_verification(success):
    a = FakeAdapter(success)
    r = Reconciler(a, delay=0, retry_delay=0)
    r.schedule("sunset")
    await r.task
    assert len(a.sent) == (1 if success else 2)
    assert r.diag["health"] == ("repaired" if success else "degraded")
    assert r.diag["pending_reconciliation"] is False
    assert r.diag["retry_number"] == (1 if success else 2)


@pytest.mark.asyncio
async def test_coalescing_current_owner_and_transients():
    a = FakeAdapter()
    r = Reconciler(a, delay=0.01, retry_delay=0)
    r.schedule("Daily")
    r.schedule("Manual")
    a.args[0]["main_area"]["owner"] = "manual"
    r.schedule("49ers")
    await r.task
    assert not a.sent and r.generation == 3
    for kind in ("score", "pool_ready", "powerwall", "spa_release", "liquor_restore"):
        a.args[0]["main_area"]["owner"] = "daily"
        a.suppressed = {"main_area": kind}
        r.schedule(kind)
        await r.task
        assert not a.sent
    a.suppressed = {}
    r.schedule("restored")
    await r.task
    assert len(a.sent) == 1


@pytest.mark.asyncio
async def test_invalidated_during_await_cannot_issue_repair():
    a = FakeAdapter()
    r = Reconciler(a, delay=0, retry_delay=0)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def hold():
        entered.set()
        await finish.wait()

    a.before_repair = hold
    r.schedule("old Daily")
    old = r.task
    await entered.wait()
    a.args[0]["main_area"]["owner"] = "sync"
    r.schedule("Sync started")
    finish.set()
    await old
    await r.task
    assert not a.sent


@pytest.mark.asyncio
async def test_unknown_device_bounded_without_commands():
    a = FakeAdapter()
    a.args[1]["light.kitchen_kitchen_right_cabinet_lights"]["state"] = "unavailable"
    r = Reconciler(a, delay=0, retry_delay=0)
    r.schedule("startup")
    await r.task
    assert r.diag["health"] == "degraded" and not a.sent



def test_reconciliation_protected_entities_reports_only_exposed_manual_layers():
    from custom_components.home_lighting_manager.intent_policy import (
        IntentAttributionSource,
        IntentEvidence,
        IntentEvidenceKind,
    )
    from custom_components.home_lighting_manager.model import Appearance
    from custom_components.home_lighting_manager.shadow import ShadowObservation, ShadowRuntime

    entity = "light.living_room_living_room_right_ceiling"
    other = "light.kitchen_kitchen_left_cabinet_light"
    runtime = ShadowRuntime(generation=7, managed_entities=frozenset({entity, other}))
    evidence = IntentEvidence(
        kind=IntentEvidenceKind.EXPLICIT_HOMEOWNER_COMMAND,
        succeeded=True,
        attribution_coherent=True,
        attribution_source=IntentAttributionSource.HOME_ASSISTANT_USER,
        has_user_id=True,
        has_parent_id=False,
    )

    runtime.observe(
        ShadowObservation(
            entity_id=entity,
            evidence=evidence,
            appearance=Appearance(on=True, brightness=155),
            manual_precedence=250,
        )
    )

    assert runtime.reconciliation_protected_entities({entity, other}) == (entity,)

    runtime.observe(
        ShadowObservation(
            entity_id=entity,
            evidence=evidence,
            operation="off",
        )
    )

    assert runtime.reconciliation_protected_entities({entity, other}) == ()


@pytest.mark.asyncio
async def test_schedule_delay_override_bypasses_normal_debounce():
    a = FakeAdapter()
    r = Reconciler(a, delay=60, retry_delay=0)
    r.schedule("sensor.home_lighting_manager_shadow_health", delay=0)
    await asyncio.wait_for(r.task, timeout=1)
    assert len(a.sent) == 1
    assert r.diag["repair_count_today"] == 1
    assert r.diag["pending_reconciliation"] is False
