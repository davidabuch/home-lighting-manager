# Phase 1 pre-edit gap map — 2026-09-16

Baseline fetched from GitHub: `f8f57e35e1be3013a7a003841d19ec6c0eb10978`.
Local worktree was clean on `fix/live-topology-discovery`; feature branch created
from current origin/main: `feat/layered-ownership-core-off-engine`.
The supplied contract was read in full and is byte-identical to
`docs/OWNERSHIP_SCENARIO_CONTRACT.txt`; its frozen SHA is not the baseline.

Inspected README, ADR 0001, acceptance matrix, ownership/recovery/shadow tests,
all HLM modules, and merged commit history/diffs covering PRs #11–#22.

| Existing foundation | Gap to close |
|---|---|
| Typed per-entity layers, precedence/recency resolution | Explicit automatic insertion/lifecycle policy preserving Manual; parent identity |
| Desired appearance fields | Validate malformed/incomplete persisted state without color conversion |
| Individual OFF and two-step group OFF | One operation identity/history, exact targets, successful/available members, interleaved intent |
| Config generation | Runtime revision token invalidated by newer intent and state mutations |
| Family suppression | Explicit session ending and parent/child eligibility |
| Shadow classifier and qualified leaf promotion | Retain original observation ordering through delayed promotion; no group-burst expansion |
| Durable Manual/suppression evidence | Versioned operation provenance; fail-closed recovery; no replay of group double-OFF phase |
| Counts and attribution ledger | Bounded per-entity layers, reasons, operation and suppression diagnostics |

The Scenario 83 question recorded during the initial gap inspection is now settled: generic pop
resolves current eligibility; specific family + parent-session suppression prevents re-exposure
for that session. New independent sessions remain eligible. Group-OFF arming is runtime-only and
is discarded across restart, independently of trustworthy durable Manual-OFF. See the
[contract clarifications](../OWNERSHIP_CONTRACT_CLARIFICATIONS.md). Production family wiring remains deferred.

No platform limitation is asserted. Actual external group identity, repeated OFF
with no state transition, and complete scene identity cannot be inferred reliably
from the existing state-only adapter; the core accepts explicit operations while
that adapter work remains deferred. No service dispatcher is introduced.

This is the historical pre-edit gap map. The September 17 final gate found and corrected additional
admission, ordering, recovery and boundedness defects. Current conclusions are in
[the final review](phase-1-final-architecture-review.md); this map is not a completion claim.
