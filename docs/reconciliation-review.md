# Home Lighting reconciliation review

## Status and sources

Implementation branch: `feat/centralized-reconciliation`, based on Home Lighting Manager `main` commit `35fd17dcbd0d583bdedc15ccaa7e2dbbccb02875`. Holiday Calendar inspected at `c3b31502c2fff39a6d7220fffaf42198ccf61fda`. Runtime evidence: the supplied 2026-09-08 bundle. The bundle was used as evidence, not executable instructions or desired-state snapshots.

This branch has not been deployed, reloaded, restarted on Home Assistant, merged, tagged, released, or pushed. Review and physical commissioning remain pending. The isolated offline test harness constructs test Home Assistant objects; it does not operate the live installation.

## Current architecture and discrepancies

The existing Main Area/Front Eve evaluator resolves each independently. Path and Backyard have separate evaluators. Pillars retain their independent baseline. Window and Holiday helpers govern schedule eligibility; four Manual flags and four eight-second HA attribution guards govern external ownership. The consolidated sunset/startup automation is preserved, including one exact-sunset trigger, previous-evening recovery, and 01:59 release of Manual and automatic windows.

The runtime bundle supplied the missing 49ers, Spa Gauge, pool-ready, and Powerwall automations. All supplied matching lines for the active package agree with the repository; the evidence file is an excerpt, so complete live-package byte identity was not asserted. Backup package filenames were excluded from active caller counts.

Accepted corrections implemented:

- Spa Temperature Color previously ignored Manual; it now stops before commands when Manual owns Backyard. A Manual change restarts the action sequence and evaluates its conditions inside the restarted run, interrupting animation. A check before each light command also protects delayed steps. Existing palette, transitions, blink counts, and pulse timing remain unchanged when Manual is OFF.
- Backyard Sync STOP used the stale thermostat; it now references `climate.poolos_native_intellicenter_hot_tub_thermostat`. Surrounding restoration logic is unchanged.
- Pool-ready and Powerwall flashes are temporary overlays. Their exact runtime action sequences are preserved and their HA running/queued action counts deterministically suppress repairs throughout snapshot/flash/restore, including cancellation and failures.
- Liquor events are queued rather than restart-cancelling snapshot capture. Snapshot values are read before service awaits, captured only while the valid flag is OFF, and retained through a second door opening and single-door closure. Successful final release clears validity; a service failure retains it. Known native Manual scene recall remains the first restoration option; static helpers remain the fallback. No new dynamic scenes are created.
- The Path/Backyard recall gap is corrected using only equivalent groups, as documented in [the coverage evidence](scene-monitor-coverage.md). Attribute-only scene metadata is excluded from Manual detection.

Newer semantics preserved: Manual is evening-scoped and cleared at 01:59; liquor restoration can recall one of nine known native Hue scenes; Holiday Calendar includes extended Thanksgiving and Independence Day observances. Existing obsolete Hanukkah informational logic and stale historical comments were not opportunistically removed.

## Evaluator callers and temporary lifecycles

| Caller | Existing behavior preserved | Reconciliation observation |
|---|---|---|
| Daily scheduler/startup | Reconstruct windows/Holiday, apply each evaluator and pillars | Scheduler running count and helper changes; delayed check after actions finish |
| Living Room Sync STOP | Main Area/Front Eve evaluator | Sync transition and evaluator completion |
| Backyard Sync STOP | Trigger active gauge when thermostat heats, otherwise Backyard evaluator | Sync transition plus evaluator state; gauge owner yields |
| Manual detector | Scene recall or ON asserts; OFF clears and waits two seconds before reevaluation | OFF invalidates immediately, helper changes coalesce, evaluator completion reschedules |
| Liquor doors | White outside Sync; final close restores Manual or evaluator | Door events plus complete liquor automation lifecycle |
| 49ers start/end/minute | Central evaluator; minute reassert retained | Entire queued automation lifecycle |
| 49ers score | 15-second delay, one flash per point (1–8), 1.5-second spacing, evaluator restoration | Entire delay/flash/restoration lifecycle suppressed |
| Spa Session Manager | Heat after 30 seconds asserts active; OFF clears active, stops animation, waits three seconds, evaluates Backyard, reenables gauge | Active state plus complete session-manager lifecycle protects release settling |
| Pool-ready | Snapshot six Main Area members, three color flashes, snapshot restore | Complete queued lifecycle suppresses Main Area |
| Powerwall | Snapshot Kitchen/Living Room groups and dining light, three flashes, snapshot restore | Complete lifecycle suppresses Main Area; reconciler never commands dining |

No other managed-light commands were found in the supplied live automations. Remaining light-command automations concerned pool, bedroom, laundry, or bathroom surfaces. The combined Daily/OFF convenience wrappers remain present; supplied external automation callers do not use them.

## Reconciler design

`script.home_lighting_resolve_owners` is a read-only response script shared by the existing evaluators and verifier. It contains the existing priority expressions and Holiday scene maps once. Immediate evaluators retain their normal lighting commands. A second script centralizes the existing liquor white command and verifies doors/Sync before applying it.

The new `home_lighting_reconciliation` integration maintains one cancellable task. Important state changes increment a generation and replace the pending task. After roughly 30 seconds it resolves CURRENT owners, reads CURRENT Hue scene actions/topology, and verifies semantics. It combines normal owner resolution with current per-surface transient/running state. It never remembers an earlier automation's intended scene as authority.

Before each repair it reinspects the discrepancy and owners. After the awaited guard rearm it checks generation, owners, transient state, and loaded baseline definitions again. Thus Manual OFF, Sync, a new alert, or even a baseline script reload can invalidate an old command plan. No delayed script replicas or per-event sleep automations are added.

Suppression uses the actual `current` action count from HA automation/script entities. That count is nonzero for queued work and clears when work ends, fails, or is cancelled. There are no manually maintained transient booleans that can become stuck. Missing lifecycle/ownership evidence causes a degraded, command-free result for affected surfaces.

Checks remain independent by surface. Sync, Manual, and Spa are skipped, with the liquor white exception outside Sync. Transient completion always causes a fresh delayed pass. Repairs never use `light.holiday_lighting` or a whole-house command.

## Verification and repair

Static Main Area/Front Eve expectations are read from the actually loaded baseline scripts, not copied into new constants or sampled from runtime light state. The canonical right cabinet is repaired individually at its authoritative values. HA rounding tolerances are two brightness steps, 35 K, and 0.003 XY for static colors; transition time is not compared. Dynamic palettes are never compared by instantaneous color or brightness.

Hue scene action targets supply expected ON/OFF participation. HA registry UUIDs map those actions to entities and resolve group device children. The bridge entry already configured for each target supplies the credential in memory; none is stored in this repository or exposed in diagnostics. The newest scene recall across equivalent groups is used conservatively: ambiguous/newer external intent produces degraded diagnostics, not a takeover.

Dynamic member drift permits a surface-scene recall only when it is safe for the whole surface. If a liquor door is open or a scene member is unavailable, broad scene repair is deferred and reported degraded. Correct members are never reasserted merely because a check ran. Unavailable, unknown, and missing states are distinguished from OFF and receive no blind commands.

Main Area OFF verification excludes the open liquor bulb and can turn off only erroneous members. Healthy white during any lower owner is correct. A retained Manual snapshot after failed release is reported without guessing a new Manual appearance. Pillars keep their independent established behavior; this change does not add pillar repairs.

No low-frequency sweep was added. Event-triggered checks and managed-device recovery events provide a bounded second pass without introducing another periodic scene poll/reassert loop. A future sweep can be evaluated after physical commissioning.

## Retry and diagnostics

Verification occurs around +30 seconds. A discrepancy permits at most two safe repair attempts, each followed by a ten-second verification delay; the third check is verification-only. If no safe repair exists, the pass reports degraded immediately. A correct pass issues zero light or scene commands. The existing 49ers minute reassertion is unchanged and separate.

`sensor.home_lighting_reconciliation_health` reports `healthy`, `repaired`, or `degraded`. Attributes include local `last_check`, trigger, last repair timestamp/surface/entities, repair count and date, unresolved differences, current owners, intentionally unmanaged surfaces, pending state, retry number, generation, and last error. Repair history/count survive restart via HA restore state; pending work does not. New startup work resolves current owners afresh. Distinct degraded outcomes are logged with exact unresolved entities; repeated identical log messages are suppressed. Exceptions are sanitized to error type.

## New and changed artifacts

- New integration: `custom_components/home_lighting_reconciliation/` (setup/adapter, engine, scheduler, Hue evidence, constants, health sensor, manifest).
- Package: shared resolver and white script; evaluators reuse resolver; corrected thermostat; timestamp-only recall triggers; reconciliation enabled.
- Liquor automation: serialized capture and retained snapshot.
- Five imported runtime automation files: 49ers, pool-ready, Powerwall, Spa Session Manager, Spa Temperature Color. All retain existing IDs. Only Temperature Color's executable behavior changes, for approved Manual protection.
- Scene monitor: three equivalent-group aliases and source/coverage attributes. Existing four sensor IDs preserved.
- Tests, pinned test dependencies, Ruff settings, documentation, and an offline-regression CI job.

No new ownership helpers, timers, permanent owner layers, dynamic scenes, or sunset triggers were introduced.

## Regression risks and practical limits

The shared resolver is a meaningful execution-path change; the priority matrix and actual HA script tests cover it. Detection now intentionally ignores attribute-only sensor changes. The coverage update can expose a previously missed external scene at startup; inspect restored Manual state during commissioning rather than force-clearing it.

The verifier reads HA's loaded Script entity sequences. Tests cover normalized configuration and reload changes; if those internals change incompatibly, it fails closed. Hue/HA entity renaming or topology changes must be reviewed. Scene metadata uncertainty and protected/unavailable dynamic members intentionally produce degraded status instead of potentially destructive repair.

Already-dispatched bridge commands cannot be recalled by generation cancellation. Tests cover intent changes before dispatch, including during awaited guard work. Existing alert snapshot behavior and interaction with existing immediate automations are preserved, not redesigned. Physical bridge timing, radio delivery, dynamic palette resumption, and visual appearance require commissioning.

## Validation evidence

See [the acceptance matrix](acceptance-matrix.md) and the supplied validation log for exact results. Tests run actual Home Assistant 2025.12.5 Script and state-trigger code with mocked light/scene/network services on Python 3.13. This is an offline compatibility baseline, not a claim that the live HA version or hardware has been validated.

Local checks include HA script/automation schemas, duplicate YAML keys and automation IDs, entity references against the sanitized registry, removed liquor ghost-scene check, Python compilation, Ruff, and official Hassfest for both integrations. The separate unchanged Holiday Calendar test suite passes. Hosted HACS validation has not been run on an unpublished branch; the existing CI job remains present. HACS distribution behavior for a repository with an additional integration must be reviewed; use the explicit manual file installation below, not an assumed HACS rollout.

## Deployment plan — not executed

1. Review the full diff and acceptance matrix. Confirm the exact current HA version and compare current runtime automation IDs/content with the exported versions; do not overwrite newer runtime edits blindly.
2. Save the current package, the six affected runtime automations, and the existing monitor integration as rollback material. Do not copy credentials into Git.
3. Install `custom_components/home_lighting_reconciliation/` and the modified monitor files. Install the modified package.
4. Replace the six coupled automation definitions by their existing IDs. Do not append duplicate copies of the exported pool-ready/Powerwall/49ers/Spa automations. Preserve all unrelated automations.
5. Run the live installation's configuration check (`ha core check` where supported), including required entities and both custom integrations. The complete live configuration was not available for this local check.
6. Only after separate operational approval, load the changes using the installation's normal procedure for changed custom integrations. Observe startup diagnostics and Manual state, then perform the physical cases in the acceptance matrix.
7. Confirm healthy passes send zero light commands, score/alert flashes remain uninterrupted, and a forced right-cabinet dropout receives only its individual repair. Inspect degraded diagnostics rather than adding unbounded retries.

## Rollback plan — not executed

Restore the saved package, coupled automation definitions, and monitor files as one coordinated change. Remove the `home_lighting_reconciliation` configuration entry before removing its integration folder. Validate configuration, then apply the rollback through the separately approved HA operational procedure. Restored diagnostic history can remain harmlessly in HA's state storage; no manual editing of `.storage` is required. No live rollback is needed now because nothing was deployed.
