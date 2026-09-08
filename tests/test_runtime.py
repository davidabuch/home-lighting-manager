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
