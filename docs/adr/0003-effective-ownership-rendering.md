# ADR 0003: One effective ownership contract for legacy rendering

Status: implementation for review and offline validation; physical commissioning pending.
Baseline: origin/main b35c925 (v0.2.23). The Ownership Scenario Contract and its settled
clarifications govern. No observation/correlation policy changes are part of this milestone.

## Proven seam and inspected callers

The v0.2.23 path was:

1. Promotion observer accepts the exact managed group; `OwnershipOperations` / layered engine
   creates Manual-OFF. The sensor publishes protected members, not a renderer plan.
2. `home_lighting_resolve_owners` still returns Daily underneath Backyard Manual-OFF. This is
   legitimate automatic eligibility, not authority to overwrite the exposed Manual-OFF layer.
3. `home_lighting_evaluate_backyard` dispatches the baseline script; that recalls the entire
   Forest Adventure scene without checking per-member HLM ownership.
4. The reconciliation adapter separately merges HLM protection into `manual_entities`.
   Its verifier skips protected lights, so the physical ON state can be reported healthy.

Path's PR #65 bridge checked full protection but, for partial protection, recalled the whole
scene and turned protected leaves OFF afterward. That violates both selective rendering and
Manual appearance semantics. Main's static baseline had seven duplicated protection templates,
while its Holiday/49ers recalls bypassed them. Front Eve also lacked the per-member gate.

Inspected #59 diagnostics; #61 subgroup deferral; #63 reset; #65 Path bridge; #67/#69 displaced
scene evidence; #72 managed subsets; #74 slow canonical OFF; #76 exact scene-monitor corroboration.
Those commissioned attribution/correlation paths and PR #59 diagnostics are unchanged.

Caller coverage:

| Entry | Physical execution after this change |
|---|---|
| Main / Front Eve evaluator | Shared legacy renderer for static, OFF and Holiday/49ers scenes |
| Path state applier | Same renderer; no recall-then-OFF cleanup |
| Backyard evaluator | Same renderer, with existing Sync / Spa yielding |
| Direct baseline and OFF scripts / combined scripts | Same renderer; obsolete requested automatic owner rejected |
| Daily schedules, startup window reconstruction, Holiday transitions, Sync restoration | Existing evaluator callers; now reach shared gate |
| 49ers reassert / game-end | Existing evaluator; same gate |
| 49ers scoring flash | Exact eligible Main/Front members via shared gate, not broad Celebration overwrite |
| Spa temperature/pulse/target writes | Shared gate; current Spa owner required, protected members excluded |
| Reconciliation repair | Same effective projection; currentness rechecked per selective Hue write |
| Pillars | Independent, unchanged; outside four commissioned ownership surfaces |
| Liquor functional overlay / snapshot restoration | Existing policy, unchanged; limitations below |
| Pool-ready / Powerwall snapshots | Existing transient lifecycle, unchanged; limitations below |

## Decision: compact effective per-entity projection (Option B)

`effective_ownership` is derived from the engine whenever the shadow diagnostic is published.
It contains version 1, runtime authority ID, generation, revision, configured managed group subsets,
and per-entity owner, kind, protected flag, desired native appearance, layer/group/family/session
identity, and eligible automatic layer provenance. Native color/scene fields are preserved without
conversion. Scope is bounded by the existing 256 configured entities / 32-layer engine limits.

`automatic_authority: legacy_resolver` explicitly identifies where current automatic policy still
lives. The resolver returns this projection alongside its current automatic owners. An exposed
Manual-OFF does not replace Daily with a fake surface-level Manual owner. For HLM-configured groups,
old aggregate Manual helpers no longer suppress the rightful automatic underlay after release.
Unmigrated helpers remain compatibility inputs. The new projection overrides older protection
lists for entities it covers; a protection-only older sensor never becomes evidence of desired OFF.

This is a read-only view, not a second ownership engine or an executable persisted command queue.
It does not classify OFF, infer intent from physical state, choose family/session precedence,
or mutate engine layers. Reset, expiry and recovery naturally change the next view. The store
envelope/domain persistence schema is unchanged; no projection is serialized or replayed.

## Shared legacy dispatch

`home_lighting_reconciliation.render` is the migration execution boundary. The HLM component
still has no lighting dispatch. Declarative surface aliases identify aggregate requests; raw Hue
membership and HLM's managed intersection establish exact scope. No entity-specific OFF policy
exists in the gate. Adding a surface requires declarations / its presentation, not another OFF
state machine.

For each request the renderer:

- reads current effective ownership and resolves current automatic policy;
- rejects a stale requested owner and yields to structural Sync / legacy Manual / Spa policy
  (Spa's own writes explicitly require current Spa ownership);
- reads authoritative Hue membership/actions;
- excludes exposed protected layers, unmanaged leaves from selective writes, unavailable members,
  and the open-door functional liquor member from automatic rendering;
- preserves future exposed automatic family layers that differ from the legacy automatic owner;
- rechecks projection, owners and loaded presentation definitions after awaited work;
- re-arms the existing HA guard, checks again, then dispatches only eligible targets;
- rechecks currentness and availability before each selective Hue action.

A full managed Manual-OFF surface receives no automatic recall or cleanup write. A Manual
appearance receives neither automatic appearance nor OFF. The same eligibility applies to
Holiday, Daily, OFF, game reassertion, and the guarded family writes. Native scene recall is used
only when it cannot overwrite a protected or unavailable action target. Otherwise only the
eligible native Hue action objects are sent; the scene is never recalled first.

An already accepted physical request cannot be withdrawn from Hue. Newer ownership cancels
remaining work at the next dispatch boundary; no delayed retry queue replays it after recovery.

## Reconciliation and observability

The adapter projects `effective_entities`, `manual_off_entities`, excluded unmanaged scope and an
ownership token into the same owner response used for verification. Manual-OFF is checked as OFF;
ON drift is no longer silently healthy. Manual appearance is protected from automatic comparison.
Automatic eligibility remains underneath. A wholly protected scene does not require unrelated
latest-scene metadata to be healthy. Unknown/unavailable remains an availability problem, never OFF.
A new accepted first-group-OFF release schedules immediate verification even if the group had
no prior protected exceptions; repeated diagnostic updates for that receipt do not reschedule it.
Reconciliation remains bounded and subordinate to primary rendering, not an ON/OFF correction loop.

Commissioning can inspect:

- `sensor.home_lighting_manager_shadow_health.effective_ownership` for desired ownership / provenance;
- the owner resolver response for underlying legacy eligibility;
- `sensor.home_lighting_reconciliation_health.last_render` for the latest suppression/action reason
  and up to 32 affected entities;
- all existing PR #59 and ownership/correlation diagnostics.

No unbounded event history is added. Missing or malformed HLM state prevents new renderer dispatch.

## Regression evidence

`tests/test_effective_rendering.py` runs real HA scripts and the actual legacy adapter with simulated
Hue resources/device services. It uses the real ownership operations and projection, then checks
physical command intent, resulting simulated device state, and reconciliation. It does not stop
at an HLM mutation assertion.

| Case | Main | Path | Backyard (11 leaves) | Front Eve (one leaf) |
|---|---|---|---|---|
| Full Manual -> OFF #1 -> automatic -> OFF #2, Daily and Holiday | yes | yes | yes | yes |
| Unmanaged extra absent from ownership | yes | yes | yes | yes |
| Partial Manual-OFF, Manual appearance preserved, eligible peers render | yes | yes | yes | single member |
| Individual release / second OFF / reset | yes | yes | yes | yes |
| Stale legacy aggregate helper cannot block release | yes | yes | yes | yes |
| Nightly projection expiry and Manual-OFF ON drift verification | yes | yes | yes | yes |
| Unavailable excluded, no recovery replay | yes | yes | yes | yes |
| Structural Sync | yes | n/a | yes | n/a |

Additional tests cover stale generation/reset/expiry during Hue reads, new intent during guard
activation / selective writes, malformed/missing projection, family writes, future family exposure,
and fresh-runtime projection identity. Existing dynamic telemetry, persistence, session suppression,
child eligibility and external correlation regressions remain mandatory.

The new Daily/Backyard full-sequence regression was run with the unchanged origin/main package
loaded by a temporary test plugin. It failed precisely after second OFF: the real old evaluator
recalled Forest Adventure and turned the managed members ON. The same regression passes with the
shared gate. This is offline reproduction of the reported renderer seam, not radio commissioning.

## Bounded limitations / review points

- Selective Hue actions establish the native per-light action state. They do **not** claim to start
  native dynamic palette animation independently on a subset. Full safe recalls retain Hue native
  playback. Mixed dynamic playback still needs physical commissioning / later Hue capability work.
- 49ers and Spa session suppression policy is not migrated. The dispatch gate prevents writes to
  currently protected members, but does not implement the future remainder-of-session dismissal
  policy. Score flashes now address eligible leaves rather than the broad Celebration aggregate;
  physical synchronization of those writes requires commissioning.
- Liquor functional door behavior, persistent repair override policy, and dynamic Manual scene
  snapshot restoration are not redesigned. Its legacy whole-scene restoration can still affect
  other members and needs a separate functional-layer migration. Do not claim mixed-ownership
  safety through that legacy restoration path from this automatic-renderer acceptance matrix.
- Pool-ready / Powerwall overlays keep their existing flash/snapshot/restore lifecycle. They remain
  unmigrated transient policies; this milestone does not claim newer Manual intent during a snapshot
  alert is fully reconciled through the layered engine.
- Missing/inconsistent Hue action membership fails closed with a renderer diagnostic. There is no
  broad recall fallback. No bridge key, registry dump, or runtime-only desired state is embedded.
- Service registration lives in the legacy reconciliation integration. A reviewed deployment must
  install its matching code along with the package and HLM projection. Updating only the HACS HLM
  directory does not migrate the package. No deployment is performed by this change.

## Failure-mode checklist from the request

1–2: shared gate; no Backyard-only policy or per-zone Manual-OFF helper.
3: Manual appearance and Manual-OFF remain different records; protection never implies OFF.
4–6: underlay preserved; all-surface first/second OFF rendered sequence covered.
7: migrated automatic scenes filter before dispatch; functional legacy limitation above remains.
8–10: no HLM light dispatch, no unmanaged ownership, no correlation/dynamic filter changes.
11: existing Sync gate preserved and tested.
12: exposed family state is not flattened; direct family writes gated; session policy remains deferred.
13–15: reset/expiry fresh projection, no physical-state recovery inference, unavailable excluded.
16–17: primary rendering obeys ownership; no YAML OFF/precedence/group/session state machine added.
18–19: reusable cross-surface tests execute actual evaluator and legacy executor.
20: no observer redesign, UI work, dependency changes or full family/functional migration.

## Physical acceptance after a separately approved deployment

Start with Backyard: Daily -> Manual scene -> OFF #1 -> Daily returns -> OFF #2. Verify all 11
managed leaves stay OFF, the automatic owner remains Daily, projection says Manual-OFF, no Forest
Adventure dispatch follows OFF #2, reconciliation is healthy, and Festavia gains no HLM ownership.
Then Reset and verify current automatic presentation returns. Capture full diagnostics and the
exact deployed commit. Only then commission partial leaves, Front Eve, mixed/Holiday, family
writes and Sync. No offline pass establishes physical commissioning success.
