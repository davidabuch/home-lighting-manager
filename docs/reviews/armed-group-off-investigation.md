# Armed group OFF investigation — v0.2.15

Baseline: current main `772a446` (release v0.2.15). The supplied ownership contract and its
settled clarifications govern. This is an observation-only diagnostic change, **not a claimed
fix for the physical failure**. No command authority, release/version bump or production changes.

## Proven facts versus unknown cause

The supplied live summary places Main Area OFF at 21:30:58.142756 and Kitchen Cabinet OFF at
21:30:58.172192, after seven leaf OFF reports starting around 21:30:56.93. Guard and Sync were OFF
at Main Area receipt. Therefore Kitchen-first ordering is not a valid explanation for that run.
No full event payloads, prior burst state, pending receipts, timer execution trace or instantaneous
arming state were supplied. Those missing inputs prevent proof of the failed predicate and actual
arming-removal site. Final diagnostic state alone cannot supply that proof.

The new real-HA-state tests emit parent-before-subgroup with the reported ~1.213s aggregate delay.
With complete retained evidence, that ordering creates parent Manual-OFF. A controlled alternative
filters one leaf while a guard is ON, then turns the guard OFF before the parent: identical parent
receipt guard state now rejects for missing pending evidence. This demonstrates an observability
gap; it does **not** claim the house experienced that guard transition.

## Complete pipeline and gates

`_async_state_changed` filters configured entities, absent/new non-State values, non-light and
unsupported state observations. `observation_from_state_change` separates availability and context.
Membership classifies aggregates as unknown evidence. The adapter reserves sequence/generation,
then calls ordinary `runtime.observe` **before** topology promotion. Unknown context-less evidence
cannot mutate ownership, advance successful-intent watermarks, or disarm groups; a genuine direct
HA user receipt can. Aggregate membership comes from cached topology, event attributes, then current
canonical state; it is not simply the event's raw attributes.

Promotion removes pending leaves and cancels singles for availability/parent context. Non-external
attribution then exits. Cross-surface external leaves can reset the burst. Topology snapshots refresh
at a gap >2s or reversed clock. Automatic filtering drops pending leaf evidence for scene-settling,
nightly settling, post-boundary OFF epoch, non-OFF Hue dynamics, or active matching/fallback guard.
OFF bypasses only the dynamics filter, not the other filters. Retained same-kind receipts replace
sequence/time without restarting an existing single timer. Different kinds cancel that timer.
Pending leaves expire by **age from each receipt**, whereas the correlator resets by **gap between
successive events**; these are different lifetimes. The full topology cache is refreshed afterward.

| Stage | Gate / possible outcome |
|---|---|
| Routing | Missing burst/candidate exits. A qualified single candidate calls `_promote_single_candidate` and returns **before armed handling**, including when single promotion itself rejects/defers. |
| Armed | Observation must be OFF and have aggregate members. |
| Armed | Current aggregate ID must exist in `group_off_sequences()` at that exact point. |
| Armed | Normalized aggregate members must equal the armed member set. |
| Armed | Burst leaf set must equal that same set (not merely contain it). Diagnostic summaries cap leaves at 32; this existing inference limitation is unchanged. |
| Armed | For configured scene-recall sources, guard and optional Sync must exist and be OFF. Other aggregates do not use this gate. |
| Armed | Every armed member must retain pending evidence; every retained operation must be OFF. |
| Armed | All retained generations must be identical and of integer type. Current-generation equality is deferred to core. |
| Armed | Sequence is the minimum retained sequence. Existing code converts these to integers; unlike the owned/exact paths it has no explicit pre-conversion type guard. Normal adapter ingress supplies integers. The diagnostic captures originals before conversion. |
| Armed submission | Cancels singles and removes pending members **before** core acceptance. A rejected core operation still consumes those pending receipts and returns True from armed handling. |
| Core | Classifier must allow intent; generation, identity, scope, operation kind, dedup ID/window, optional work token, member availability/success and per-member successful sequence watermarks must pass. Armed/exact adapters currently supply no expected work token. |
| Core | Group OFF calls `apply_group_off`; first releases exceptions, second creates per-member Manual-OFF. |
| Armed success | Resets correlator, published burst and frozen topology; schedules ordinary persistence/diagnostic publication. No distinct armed promotion-key check exists; core operation ID dedup applies. |
| Armed rejection | Falls through to owned-Manual group handling, then uniquely exact-group correlation. Owned path requires all exposed Manual layers carry the same configured group ID. |
| Exact fallback | Requires multi-leaf aggregate topology, unique exact direct membership in the burst-start snapshot, and current event equals that exact aggregate. Nested veto only applies if a different armed superset is contained in current burst leaves. Partial/reset bursts may not meet that veto. |
| Exact submission | Requires all pending observations, homogeneous kinds, coherent sequences/generation and receipt-time spread <=2s. Deduplicates group/kind/member sequence pairs; uses earliest sequence against core watermarks. |
| Single callback | Deferred timer can later apply retained same-kind evidence. It uses latest pending receipt and does not require that the burst still be a qualified single candidate when firing. This is an existing possible disarm path, not proven live cause. |

A legacy Manual helper is not an ownership input to these promotion gates. Legacy restoration can
indirectly matter by producing HA parent contexts, guards, scene recall events and additional bridge
telemetry. Guard OFF at aggregate time does not establish its state at every preceding leaf event.

## Exactly locating arming disappearance

Every existing removal site now records `group_off_removals`, bounded to 32 records:

- `apply_group_off:overlap` — another accepted overlapping group OFF; records triggering group ID.
- `apply_group_off:capacity` — oldest interaction evicted at the existing group cap.
- `reset_group_off_for_members` — accepted appearance or individual OFF on overlapping members.
- `reset_group_off_sequence` — explicit reset; also used by session end, evening and recovery paths.
- `expire_boundary:<name>` — named lifecycle boundary clears arming.
- `next_generation` — configuration generation clears arming.

Records contain serial, generation/revision, removed group, member count and up to 32 members.
They neither advance revisions nor authorize work, and are not persisted. Operation transactions
roll back their diagnostic removals on atomic failure, so only committed removal evidence remains.
Restart creates a new ledger; existing startup-recovery diagnostics distinguish that event.
Use the serial captured in attempt records and existing latest/recent operation IDs to correlate
removal with the accepted operation. Until the next trace exists, **which removal occurred in the
reported physical failure remains unproven**.

## Added observation fields

`armed_group_off_attempts` retains 32 immutable snapshots. It records timestamp, event operation,
aggregate/group, ingress/runtime generation and revision, stage and exact rejection/core reason,
armed IDs/members, aggregate/burst members, burst timestamps/topology, frozen/current-cache members,
canonical aggregate state, pending IDs and per-member operation/sequence/generation/time, pending
single keys, guard/Sync values, candidate qualification/basis, context provenance flags, and latest
removal serial. Collections are capped at 16 groups/32 members and include relevant total counts.

`off_leaf_evidence` retains 64 retention/filter/invalidation/expiry decisions, with ingress identity
and timestamps. Together these show why a pending member was absent rather than just its absence.
These fields are output-only. No new scheduling, HA service call, ownership admission, persistence
field, extra state publication, or inference heuristic was added. The legacy reconciler ignores
HLM diagnostic changes when managed scope and protected members are unchanged.

## Why previous tests were insufficient

The previous seven-member regression fixes `dt_util.now()` for an entire OFF sequence, supplies
complete membership on each aggregate, drains HA after each event, synthesizes parent-context Daily
restoration, and explicitly sends Kitchen before Main Area. It does not replay the new physical
trace. It cannot exercise per-leaf age differences, leaf-time guard transitions, attribute-only
reports, absent/sparse membership, concurrent callback backlog, or actual bridge context/dynamic
propagation during Daily restoration. Mocked wall time does not advance HA timer callbacks.

The new tests cover three- and seven-member parent-first shapes, a controlled missing-pending
counterexample, exact gate diagnostics, all arming removal sites, unrelated groups, boundedness,
copy isolation and per-member reconciliation protection. Existing subgroup, mixed/unavailable,
individual OFF, lifecycle/restart, scene identity, family suppression and stale-intent regressions
remain mandatory. This is not an exact replay of unavailable raw live payloads.

## Next physical capture / acceptance sequence

After explicit review and authorized deployment of this diagnostic commit in shadow mode:

1. Capture the HLM diagnostic sensor attributes before the operation: generation, revision,
   group arming, pending/latest ownership and protection. Record installed commit/version.
2. Recall a known exact parent Manual scene. Confirm one operation with exactly its members.
3. OFF #1. Confirm `released_to_hlm`, parent arming and removal of per-member protection. Let the
   existing automatic renderer restore Daily/Holiday. Capture attributes again.
4. OFF #2 once. Immediately capture the **full attributes**, particularly the three new ledgers,
   recent operations, burst, startup recovery and protected entities. Capture raw `state_changed`
   data from before restoration through at least 5s after OFF #2, including old/new states,
   attributes, contexts, guards and Sync. Do not reduce it to aggregate timestamps alone.
5. Expected eventual fix acceptance: exact parent `created_group_manual_off`, all eligible members
   protected from reconciliation and remain OFF; no nested operation replaces that interaction.
   This instrumentation commit may still reproduce the defect; preserve the failed-gate evidence.
6. In separate interactions test an independently commanded subgroup, an intervening subgroup after
   parent arming, unrelated groups, unavailable/recovered members and restart. A genuine later
   subgroup must remain valid intent; no permanent parent lock is acceptable. Do not trigger
   restart or device outages solely for this capture without separate authorization.

## Evidence boundary and next design decision

Topology alone cannot universally distinguish one parent command from coincident subgroup commands
with the same state fan-out. An armed transaction provides prior intent but not proof that every
future subset report belongs to it. Before changing policy, use the trace to identify receipt loss,
early promotion, burst reset, core rejection or overlap invalidation. If transaction state is then
needed, minimally bind exact group/members to generation and first-OFF receipt sequence/identity,
and use explicit later accepted intent to invalidate it. Evidence must also establish completion
of the current operation, so duplicate propagation cannot be mistaken for an independent subgroup
command. Missing source command identity may require stronger bridge/command evidence; widening
ambiguous promotion or permanently vetoing subsets is not an acceptable substitute.

The existing per-entity protection API includes both exposed Manual and Manual-OFF. The missing
ownership result, rather than a missing boolean bridge, prevents protection in the reported case.
No broad ownership-engine redesign or unrelated contract work is justified before that cause is known.
