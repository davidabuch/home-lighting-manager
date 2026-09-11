"""One cancellable, generation-checked debounce/retry loop."""

import asyncio


class Reconciler:
    """Adapter supplies live inspection and guarded commands; no captured owners."""

    def __init__(self, adapter, delay=30, retry_delay=10):
        self.adapter = adapter
        self.delay = delay
        self.retry_delay = retry_delay
        self.generation = 0
        self.task = None
        self.diag = {
            "health": "degraded",
            "last_error": "Awaiting startup verification",
            "pending_reconciliation": False,
            "retry_number": 0,
            "repair_count_today": 0,
            "unresolved": [],
            "generation": 0,
        }

    def schedule(self, reason):
        self.generation += 1
        if self.task:
            self.task.cancel()
        self.diag.update(
            generation=self.generation,
            pending_reconciliation=True,
            last_check_trigger=reason,
            retry_number=0,
        )
        self.task = asyncio.create_task(self._run(self.generation))
        self.adapter.publish(self.diag)

    def close(self):
        self.generation += 1
        if self.task:
            self.task.cancel()

    async def _run(self, token):
        repaired = False
        try:
            await asyncio.sleep(self.delay)
            for attempt in range(3):
                check, owners = await self.adapter.inspect()
                if token != self.generation:
                    return
                now = self.adapter.now()
                if self.diag.get("repair_count_date") != now[:10]:
                    self.diag.update(repair_count_date=now[:10], repair_count_today=0)
                self.diag.update(
                    last_check=now,
                    retry_number=attempt,
                    unresolved=check.issues,
                    intentionally_unmanaged=check.skipped,
                )
                self.diag.update({"owner_" + s: v["owner"] for s, v in owners.items()})
                if not check.issues:
                    self.diag.update(health="repaired" if repaired else "healthy", last_error=None)
                    break
                if attempt == 2 or not check.commands:
                    self.diag.update(
                        health="degraded",
                        last_error="Unresolved discrepancies; bounded verification stopped",
                    )
                    break
                for command in check.commands:
                    if token != self.generation:
                        return
                    # Re-read current owners/transients AND discrepancy after every
                    # awaited operation. An outdated plan is never sent blindly.
                    sent = await self.adapter.repair(
                        command, owners, lambda: token == self.generation
                    )
                    if token != self.generation:
                        return
                    if sent:
                        repaired = True
                        self.diag.update(
                            last_repair=self.adapter.now(),
                            last_repair_surface=command.surface,
                            last_repair_entities=[command.entity],
                            repair_count_today=self.diag["repair_count_today"] + 1,
                        )
                self.adapter.publish(self.diag)
                await asyncio.sleep(self.retry_delay)
        except asyncio.CancelledError:
            return
        except Exception as err:
            # Adapter exceptions are deliberately sanitized: no URLs/credentials.
            self.diag.update(
                health="degraded",
                last_error=type(err).__name__,
                unresolved=[{"error": "Inspection/repair failed; see error type"}],
            )
        finally:
            if token == self.generation:
                self.diag["pending_reconciliation"] = False
                self.adapter.publish(self.diag)
