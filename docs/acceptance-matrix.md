# Acceptance matrix and commissioning plan

Automated evidence below is offline. It uses Home Assistant's real Script/state-trigger engine with simulated device services, synthetic state changes, and short test delays. It does not claim physical Hue delivery, palette fidelity, visual timing, or a live HA configuration check. All physical cases remain pending review-approved commissioning.

| Case | Offline evidence | Physical commissioning still required |
|---|---|---|
| 1. Daily cabinet dropout | Individual right-cabinet repair at baseline values; unrelated commands absent | Drop only right cabinet around +20 s; verify repair around +30 s and no visible change elsewhere |
| 2. Correct state | Zero commands; spontaneous recovery during guard also cancels repair | Observe service traces across a healthy scheduled pass |
| 3. Liquor open | White accepted under Daily/Holiday/49ers/Manual; no surrounding-scene repair | Check 100%/4000 K with either door open |
| 4. Liquor Manual close | Both door orders; XY/CT/OFF fallback, known native scene restore, retained snapshot on service failure | Confirm exact static appearance or native dynamic palette resumes |
| 5. Living Room Sync | Engine issues no Main Area commands under Sync; new Sync invalidates old work | Start/stop Entertainment and inspect commands during stream/restoration |
| 6. Backyard Sync | Engine yields; Sync STOP uses current PoolOS thermostat and gauge path | Test active/inactive gauge and Manual on Sync release |
| 7. Spa Gauge | Manual-at-start blocks all commands; Manual mid-sequence stops further commands; normal five blinks and final ON preserved | Check heating pulse and release settling on real bridge |
| 8. 49ers live | Independent Manual exceptions in priority matrix; native scene targets verified | PRE→IN, minute reassert, regulation/overtime IN, IN→POST |
| 9. 49ers score | Actual score script: seven point increase produces seven flashes then scene restoration; both surfaces suppressed during flashes | Verify original 15 s delay and 1.5 s spacing with TV/bridge |
| 10. Manual scene while ON | Actual state triggers assert Manual on timestamp change for all four surfaces; guards prevent HA attribution; equivalent-group coordinator coverage tested | Recall alternate scenes in every documented equivalent group while already ON |
| 11. Manual OFF release | OFF invalidates old generation before helper release; current Manual is never repaired | Observe original two-second settle then lower-owner restoration |
| 12. Holiday | Priority matrix includes Holiday; current scene actions define participation; XY/brightness variation ignored; references resolve | Recall every mapped holiday scene and observe native dynamics |
| 13. Holiday end | Actual 01:59 script releases helpers and resolves OFF; no master-zone command | Test overlaps with game, Sync, and open liquor doors |
| 14. Rapid changes | Coalescing and invalidation during awaited work; new Manual/Sync and loaded-baseline changes block old commands | Exercise close-spaced Daily→Manual→game and Daily→Sync |
| 15. Unavailable member | Distinct unknown/unavailable; no blind unavailable commands; at most two repairs then final check | Disconnect/reconnect one member and inspect degraded/recovery diagnostics |
| 16. HA startup | Actual startup action reconstructs evening/Holiday and preserves ON Manual surface; startup-generation pass covered | Restart only after approval; observe integration initialization and current owners |
| A. Spa + Manual | No command when Manual already owns; per-command check interrupts running sequence | Verify no residual transition after homeowner scene takes effect |
| B. PoolOS reference | Sync release selects `climate.poolos_native_intellicenter_hot_tub_thermostat` path | Confirm active entity and thermostat behavior in current installation |
| C. Pool-ready overlay | Actual imported script tested under Daily/Holiday/49ers/Manual; every light/scene service stage sees suppression; snapshot restoration retained | Verify visible alert and interaction with existing immediate automations |
| D. Powerwall overlay | Same four-owner lifecycle tests, including snapshot restore | Verify grouped-light alert visually without causing a real power outage |
| E. Two doors | First capture survives second opening, one closure stays white, final closure restores; overlapping capture cannot be cancelled by second opening | Exercise rapid doors and both physical orders |
| F. Every transient | Score, pool-ready, Powerwall, Spa-release, and liquor lifecycle suppression/generation tests; missing lifecycle evidence fails closed | Force a pending check to overlap each real sequence |

Additional tests cover duplicate YAML keys/IDs, removed liquor ghost-scene absence, HA action schemas, normalized loaded script parsing, baseline reload during guard, scene action/member mapping, newer equivalent-group recall ambiguity, diagnostics retry counts, and unchanged Holiday Calendar semantics.

For physical tests, collect the triggering event, generation, owner/skip diagnostics, exact service calls, and final member states. Use baseline/scene definitions as expectations, never a sampled runtime snapshot for automatic owners. Record PASS/FAIL per case; do not infer successful delivery from a successful HA service return alone. Revert any deliberate simulated states immediately after each case using the existing authoritative evaluator.

## Local result summary

- Lighting regression suite: **64 passed** (including 64 ownership input combinations within one matrix test).
- Unchanged Holiday Calendar suite: **9 passed**.
- HA Script/automation schema and duplicate-key/ID checks: passed within the suite.
- Registry reference audit: no unknown references among 130 referenced entities/services, accounting for declared helpers/scripts and existing alert snapshots.
- Ruff and Python compilation: passed.
- Official local Hassfest at the offline compatibility baseline: both integrations passed, zero invalid integrations.
- Full live HA configuration validation: **not run**; complete live configuration/version was not available locally.
- Hosted HACS/CI and physical Hue acceptance: **not run**; branch has not been pushed or deployed.

See the delivered validation log for tool versions and command outcomes. A Home Assistant dependency emits one deprecation warning in offline tests; it is not a test failure.
