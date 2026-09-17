# Phase 1 final architectural gate — 2026-09-17

Status: corrected for review, **unstaged/uncommitted**, shadow only. Not Phase 2.
Baseline/current HEAD/GitHub main independently verified as
`f8f57e35e1be3013a7a003841d19ec6c0eb10978`. Branch:
`feat/layered-ownership-core-off-engine`. No reset/rebase discarded the prior implementation.
The entire contract was reread; its repository copy remains byte-identical to the attachment.

## Material findings and resolutions

| Concern | Contract interpretation and correction | Regression evidence |
|---|---|---|
| Automatic admission could raise hidden state or collapse priority into homeowner exposure | System priority determines insertion; resolver traverses the valid admitted stack. Updates of the same logical owner/session retain identity and position. New low-priority systems cannot cross higher-priority systems. Explicit Manual placement is independent of system numbers. | `test_priority_never_reordered_by_hidden_owner_update_or_low_priority_supersession`, `test_priority_and_manual_exposure_are_independent` |
| Generic temporary layer could not peel without pretending to be Spa | Add generic removable system-layer behavior; no production family is hard-coded. | `test_sequence_b_generic_removable_system_not_production_family` |
| Operation/group races and duplicate counting | Canonical member order, one pre-operation snapshot, atomic rollback on admission failure, duplicate ID/sequence protection, bounded receipt retirement floor. Duplicates do not inflate homeowner counters. | sequences A–E, group permutation, duplicate and capacity tests |
| Delayed external promotion | Original ingress sequence/generation retained. Older promotion cannot replace newer intent; rapid operations each require fresh aggregate evidence instead of sharing one burst-start identity. | existing delayed-vs-user regression plus rapid ON/OFF, OFF/ON, appearance tests |
| Stale retained availability evidence | Invalidate pending leaves on availability or parent-chain transitions. Aggregate user context is not an explicit group receipt and does not create canonical aggregate ownership. | availability invalidation and aggregate-context tests |
| Session lifetime leaked history and active sessions | One current session per family; monotonic session sequence, bound layer/session identity; bounded retired IDs; legacy sequence-less API fails closed when evidence window fills. New independent sessions do not inherit suppression. | sequence F and 1,000-session simulation |
| Missing parent | A family child overlay without parent identity cannot become exposed. Parent absence/suppression/expiry/generation invalidates children. | orphan-child and existing parent/suppression tests |
| Recovery could overwrite newer intent | Restore only at initial empty-runtime startup; filter current managed entity scope, reject duplicate/conflicting records and incomplete desired state. | newer-intent/recovery, removed/missing/unavailable, malformed/partial persistence tests |
| Runtime token collision | Token includes opaque runtime incarnation as well as generation/revision; matching integers from an old process do not authorize work. Admission/operations/session lifecycle can check expected work. | runtime-incarnation and stale-token tests |
| Long-running buffers | Fixed caps and lifecycle pruning; overflow correlation stays unqualified rather than classifying a truncated tail. Defensive copies prevent returned metadata mutating authoritative state. | correlation overflow, group cap, mutable-metadata and session simulation |

All named review tests above are in `tests/test_phase1_architectural_review.py` unless described as
existing. The prior Phase 1 Sync supersession test now supplies an explicit high system priority;
its previous `precedence=1` assumption allowed a lower system to jump higher systems. The assertion
that Sync supersedes Manual and preserves its expiry remains, and a lower-priority counterexample
was added. No baseline PR #22 tests were weakened or removed.

## Scenario 83 and Spa suppression

**Settled Scenario 83 interpretation:** Generic OFF/pop exposes the next currently eligible
layer, not the next historical entry. Specific family/session suppression rules govern eligibility.
A qualified homeowner override suppresses the entire active family + parent session; releasing
Manual exposes the next eligible non-suppressed owner. An unsuppressed underlying family may still
be exposed by a generic pop. Suppression is explicit session state, not inferred from Manual.

Suppressed parent/child layers retain useful desired state and provenance until session end.
Updating hidden state does not move its stack slot, clear suppression or re-enable children.
Suppression requires an existing parent session; child/layer receipts cannot create a missing or
ended parent. Ending a session removes its layers and suppression; stale end deliveries are no-ops.
A genuinely new independent session is evaluated independently. These are generic primitives:
no production Spa/49ers behavior is wired. See the [settled contract clarifications](../OWNERSHIP_CONTRACT_CLARIFICATIONS.md).

## Group OFF across restart

First group OFF → restart → later group OFF is a **new first group OFF**. Group-OFF arming
is transient runtime interaction state, never durable ownership. Recovery cannot reconstruct it
from operation/group provenance, logs, diagnostics, timestamps, physical OFF or persisted Manual.
This is settled by the owner's clarification, consistent with ambiguity → HLM and no historical replay.

Trustworthy Manual-OFF remains independently durable within its lifecycle. Restoring it does not
arm a group interaction. A later explicit first group OFF applies the existing §4 rule: release
homeowner exceptions for available requested members, including restored Manual-OFF. Until that
new operation or a valid lifecycle boundary, restored Manual-OFF continues to own its members.

## Persistence and rollback

Domain payload v2, HA Store envelope v1. Old payload v1 is parsed conservatively, without fabricated
operation provenance. The fixture `tests/fixtures/pr22_shadow_store.json` was generated using the
actual model and serializer from the baseline SHA with synthetic entities/values; it is not live
`.storage`. `pr22_persistence.py.txt` is the exact baseline parser source, retained as a deliberate
rollback-test fixture. No Git history/network access is required when tests run.

Forward migration preserves only ordinary Manual/Manual-OFF with complete native desired state,
current managed entity identity, temporal validity and corroborating current state. Unknown,
unavailable, missing, removed, ambiguous, future-dated, conflicting or expired evidence is dropped.
Ordinary Manual receives the nightly lifecycle. Same-session suppression needs independent evidence;
HA adapter currently supplies none and therefore does not guess active sessions.

Rollback to PR #22: Store loads the unchanged envelope; its actual parser rejects domain v2 and
returns empty evidence. Its existing generation reader advances generation and its normal startup
checkpoint writes empty domain v1. No crash, stale intent replay, or Manual creation is required.
The deliberate cost is loss of shadow Manual/suppression preference, consistent with ambiguity →
HLM. Forward migration can read that empty v1 checkpoint. No arbitrary storage editing or deletion
is part of migration/rollback, and neither was performed.

| Classification | State |
|---|---|
| Durable evidence | Valid native Manual/Manual-OFF, operation/group provenance, ordinary lifecycle; independently corroborated exact-session suppression |
| Reconstructed | Automatic/functional/overlay eligibility, schedule/window, availability, current Sync/game/spa truth |
| Transient | Group OFF arming, operation/deduplication history, ingress watermarks, correlation/pending evidence, diagnostics, work tokens/incarnation |

## Six-month boundedness audit

| Structure | Bound/lifecycle |
|---|---|
| Authoritative entities | Current configured scope; hard cap 256; reconfiguration clears prior scope |
| Layers | One logical owner/session/parent slot; one homeowner slot; hard cap 32/entity, fail closed on excess |
| Families | One current session/family; at most 64 families; new session removes old layers and suppression |
| Session history | 128 retired identities; constant per-family sequence/last-ID watermark; explicit sequences scale indefinitely; legacy no-sequence calls fail closed after window exhaustion |
| Group OFF sequences | 64; overlapping intent and boundaries clear; oldest interaction evicted conservatively to new-first semantics |
| Receipt IDs/history | 128 IDs with retired-sequence floor; 12 diagnostic operations; 256 entity watermarks; generation reset |
| Core metadata | Defensive copies; arbitrary metadata excluded from HA attributes and durable Manual parsing |
| Attribution ledger | Existing deque of 12 |
| Correlator | At most 128 event records × 256 members; overflow remains unqualified until quiet/new burst; summaries display at most 32 IDs per list |
| Pending promotion | One per configured entity (configuration capped at 256), pruned by existing correlation window; availability/parent chain invalidate |
| Topology cache | Rebuilt from current HA light states; bounded by live HA entity inventory, old entities removed on refresh |
| Persistence | At most 256 Manual records / 64 suppressed sessions accepted; conflicting records dropped |
| HA diagnostics | 32 entities × 8 layers, 16 sessions/groups, 12 operations × 32 displayed members; scalar counters are not event histories |
| Work | No task/command queue in core; tokens checked and discarded, never persisted |

Capacity limits are fail-closed guardrails, not truncation into invented ownership. Atomic operation
rollback prevents a group from partially applying when a later member exceeds a bound.
Future adapters must use monotonic per-family session sequences and never relabel a stale receipt
with a new identity/sequence. The old convenience API is compatibility infrastructure only.

## HA compatibility and remaining legacy dependencies

Pure modules: model, engine, operations, intent policy, persistence/recovery, shadow runtime,
and topology correlator. They import no HA APIs and have no dispatcher.

HA adapters use `HomeAssistant`, `State`, `Context`, event bus listeners, `callback`,
`hass.states.get/async_all/async_set/async_remove`, `hass.async_create_task`,
`async_track_time_change`, `async_call_later`, `Store.async_load/async_save`, `dt_util.now`, and
Voluptuous/config-validation interfaces. No private Hue/HA entity internals are newly used.
Tests use real HA state/context/Store objects and preserve iterable topology-container regressions.
The Store envelope version is deliberately distinct from the domain payload schema.

The pinned harness remains HA 2025.12.5 / Python 3.13. Live HA 2026.9.2 was not accessed, so unit
success is not a compatibility/commissioning claim. Hosted Hassfest/HACS was not run in this gate.

Only the legacy observer's `manual_precedence` remains a transitional numeric-admission input;
the explicit core API needs no helper, YAML resolver, attribution guard timer, or numeric Manual
configuration. The existing nightly observer is retained, not a new schedule implementation.
All production legacy packages/automations/reconciler/scene monitor remain untouched.

## Attribution evidence limits and future extensibility

Direct HA user reports share context/entity receipt identity: duplicate reports cannot become a
second OFF inside the bounded deduplication window. Arbitrarily replayed old contexts beyond that
evidence window are not a proven source-operation channel; future adapters must preserve receipt
identity and sequence end-to-end.

The commissioned single-leaf heuristic and six-leaf/four-aggregate conservative regression remain.
Topology describes propagation, not proof of exact group intent. State-only observation cannot
establish repeated identical commands without an event or universally distinguish an early
single-leaf prefix of a later multi-leaf burst. It also cannot prove all possible null-context Sync
churn histories. This gate does not claim to solve those deferred adapter-evidence problems or
replace the commissioned classifier. Further qualification/Sync-session evidence is required
before broadening command authority; no source group is inferred from a multi-leaf burst here.

Daily/Holiday map to ordinary system admission and lifecycle validity; Sync to explicit structural
protection/supersession; 49ers/Spa to sequenced families/parents/children; door white and alerts to
functional/overlay layers with explicit completion; liquor repair to a functional OFF layer with
its own meaningful-event lifecycle. Future policy and persistence whitelist extensions are needed
for repair override; no production repair semantics are implemented. None requires replacing the
stack, operation transaction, identity, session eligibility or work-authority model.

## Final verdict and review boundaries

The corrected ownership core is a stable foundation: it separates priority/admission, actual stack
exposure, accepted intent, native desired state, validity, receipt ordering, session identity and
physical evidence. Adversarial sequences exercise those separations. It is not a commissioned
active controller. The owner has settled family eligibility and restart-cleared group interaction
history; neither remains an open contract decision. Adapter evidence limits above remain explicit.

Artifacts from September 16 live outside this repository under the Codex task's `outputs/`, and
are historical. Updated gate artifacts also stay there. Existing `.venv`, pytest/Ruff caches and
`__pycache__` were identified, not deleted; they are not intended commit content. The two synthetic
compatibility fixtures are intentional product tests, not incidental runtime dumps.


## Prior architectural-review offline gate results (before closure pass)

After the final invalid-barrier regression, 196 focused tests and 289 full-suite tests passed;
43 cases are in the new architectural-review test module. Configuration/YAML checks: 18 passed.
Ruff and compilation passed. Text and AST/import-path inspection found no lighting/scene dispatch
in the 11 HLM modules. An ineligible structural barrier cannot keep new Manual below an ordinary
owner. The only pytest warning is the pinned HA dependency's aiohttp inheritance deprecation.
Hosted validation and live HA 2026.9.2 commissioning were not performed.


## Final contract closure pass

The approved clarifications are recorded in the linked contract addendum. Existing exposure and
persistence behavior already matched them. Closure additionally rejects family-layer receipts for
missing/ended sessions, requires an existing session for suppression, and makes duplicate/stale
session-end deliveries inert. These checks prevent lifecycle events from implicitly starting or
disturbing another session. Suppressed hidden-state updates remain allowed, ineligible and in place.

Bounded diagnostics now distinguish eligible/suppressed/ended sessions and restored/rejected
startup ownership. No wire schema change: HA Store v1/domain v2. No physical OFF or unavailable
state is sufficient ownership evidence. Current-state corroboration in the HA adapter remains an
additional conservative check, not the origin of Manual-OFF intent.

`tests/test_phase1_closure.py` proves family scope, hidden updates, stale children, session renewal,
restart-cleared group arming with reconstructed automatic layers, rejection of injected diagnostic
history, durable Manual-OFF expiry, ambiguous recovery, stale pre-restart work, mixed/unavailable
members and independent per-entity OFF semantics. Earlier migration/rollback and observer tests
remain part of the gate. No production family policy or external group inference is implemented.


Closure validation: 213 focused tests, 306 full-suite tests, 18 configuration/YAML tests passed;
17 tests are in the closure module. Ruff, custom-component compilation, diff whitespace checks,
and HLM text/AST command-path scans passed. The pinned HA dependency warning remains unchanged.
These are offline results, not live HA 2026.9.2 commissioning. Both clarified decisions are settled;
no Phase 1 contract blocker remains. Staging/commit, push, PR/CI, review/merge, exact merged-commit
shadow deployment and live shadow commissioning remain separate authorized steps, in that order.
