from copy import deepcopy

import pytest
from custom_components.home_lighting_reconciliation.engine import Check
from custom_components.home_lighting_reconciliation.runtime import Reconciler


AMBIGUOUS = "different or unknown latest recall; possible Manual intent"


def owners():
    return {
        "main_area": {"owner": "off"},
        "front_eve": {"owner": "off"},
        "path": {"owner": "off"},
        "backyard": {"owner": "daily"},
    }


class FakeAdapter:
    def __init__(self, checks):
        self.checks = list(checks)
        self.published = []
        self._time = 0

    def now(self):
        self._time += 1
        return f"2026-09-27T18:00:{self._time:02d}+00:00"

    def publish(self, diag):
        self.published.append(deepcopy(diag))

    async def inspect(self):
        if len(self.checks) > 1:
            check = self.checks.pop(0)
        else:
            check = self.checks[0]
        return check, owners()

    async def repair(self, command, current_owners, valid):
        raise AssertionError("scene-evidence grace must not issue repair commands")


@pytest.mark.asyncio
async def test_scene_evidence_gap_retries_and_self_heals():
    pending = Check(
        issues=[{"surface": "backyard", "error": AMBIGUOUS}],
        commands=[],
    )
    healthy = Check()
    adapter = FakeAdapter([pending, healthy])
    reconciler = Reconciler(adapter, delay=0, retry_delay=0)

    reconciler.schedule("first_off_rebound", delay=0)
    await reconciler.task

    assert reconciler.diag["health"] == "healthy"
    assert reconciler.diag["last_error"] is None
    assert reconciler.diag["unresolved"] == []
    assert reconciler.diag["retry_number"] == 1
    assert reconciler.diag["pending_reconciliation"] is False


@pytest.mark.asyncio
async def test_scene_evidence_gap_still_degrades_after_retry_budget():
    pending = Check(
        issues=[{"surface": "backyard", "error": AMBIGUOUS}],
        commands=[],
    )
    adapter = FakeAdapter([pending])
    reconciler = Reconciler(adapter, delay=0, retry_delay=0)

    reconciler.schedule("first_off_rebound", delay=0)
    await reconciler.task

    assert reconciler.diag["health"] == "degraded"
    assert reconciler.diag["last_error"] == (
        "Unresolved discrepancies; bounded verification stopped"
    )
    assert reconciler.diag["retry_number"] == 2
    assert reconciler.diag["pending_reconciliation"] is False
    assert reconciler.diag["unresolved"] == [
        {"surface": "backyard", "error": AMBIGUOUS}
    ]


@pytest.mark.asyncio
async def test_non_scene_commandless_failure_does_not_get_grace():
    missing = Check(
        issues=[{"surface": "backyard", "error": "missing ownership evidence"}],
        commands=[],
    )
    adapter = FakeAdapter([missing])
    reconciler = Reconciler(adapter, delay=0, retry_delay=0)

    reconciler.schedule("ownership_input_missing", delay=0)
    await reconciler.task

    assert reconciler.diag["health"] == "degraded"
    assert reconciler.diag["retry_number"] == 0
    assert reconciler.diag["pending_reconciliation"] is False
