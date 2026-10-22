# Cabinet ownership recovery — engineering candidate

Status: review candidate, **not universally deployment-ready**. No production changes or
physical commands were made. The contract and October 21 audit govern; this document does
not amend them. Read the [provenance report](2026-10-21-cabinet-provenance.md) and
[identity manifest](2026-10-21-cabinet-deployment-manifest.json) first.

## Demonstrated defects and correction

The captured live parallel automation was exercised offline through HA's Script engine
with fake device services. Its existing Daily release was retained verbatim in that
reproduction. Eleven assertions produced **8 failures, 3 positive controls**:
Holiday, 49ers, Off, Manual appearance, Manual-OFF restoration failed; suspended old
close/new open lost interval evidence; suspended Manual restore overwrote a newer open;
whole-scene restoration turned on a sibling with accepted Manual-OFF. These are software
reproductions, not physical commissioning or evidence that the observer caused sunset's
physical incident. The reproduction harness belongs to the supplied audit evidence;
new committed tests exercise the replacement production path rather than reproduce a
broken automation and label its failures compliance passes.

A01: release now reads current per-leaf HLM projection and current automatic presentation.
Daily uses the loaded cabinet baseline, not copied brightness/color constants. Off and
Manual-OFF issue cabinet OFF. Native Manual fields preserve their reported color model.
Static Holiday/49ers use only the cabinet's Hue action after membership/capability checks.
Missing projection, unknown controls, pending homeowner classification and unsupported
ownership fail closed. No full Main Area evaluator is invoked.

A02: one runtime `DoorInterval` captures canonical pre-open intent once; it never captures
physical white as a new Manual snapshot. Input ingress advances an epoch before acquiring
the command lock. Every awaited planning/metadata boundary is followed by current input,
projection authority/generation, per-leaf ownership, automatic presentation, loaded script
and availability validation. The lock orders submitted commands. Old work cannot clear a
new interval. Transport acceptance is not release success: matching observed telemetry,
with the same interval epoch/ownership signature, completes release. Failure, cancellation,
rejection or ambiguity retains the interval and diagnostic reason. No unbounded retry loop
or delayed command replay is introduced. Reconciliation protects just the unresolved
functional member; unrelated eligible peers remain eligible.

A03: whole-scene Manual restoration is removed. A cabinet release never commands peers,
creates ghost scenes or uses surface Manual booleans as future ownership truth.

A13 (focused): HLM accepts a metadata-only `track_command_consequence` lifecycle:
prepare -> submitted -> complete, or cancel. Its bounded runtime receipts describe exact
native payload, entity, dispatch context and generation. The observer collects this as
renderer consequence evidence for the existing canonical classifier. Missing Hue context
can match payload; independent affirmative contexts and materially different payloads do
not match. Duplicate delayed matching reports remain attributable. An unsent cancellation
removes only that receipt, not older submitted evidence. Receipt cap is 128; prepared and
completed lifetime is 8 seconds, in-flight lifetime is 30 seconds. Existing commissioned
operation/guard receipts are unchanged. HLM still has no physical dispatch path.

## State and concurrency boundaries

Automation `parallel` mode now only delivers short notifications. It does not authorize
parallel lighting writes. Door, Sync, startup/recovery and ownership changes enter the
same executor. An ownership notification while white is already submitted does not fight
independent homeowner intent. Final close resolves the latest owner, not saved old intent.
Unknown sensors never mean closed; either positively open door is sufficient for opening.
Sync ON yields; unknown Sync blocks. Restart discards functional interval and receipts,
then waits for current trusted HLM projection and current inputs. Nightly generation and
ownership changes invalidate old release plans; no ownership reset or scheduler is added.

The epoch cannot retract a command already accepted by HA/Hue. The lock orders the newer
submission after the earlier acknowledgement. Physical bridge execution/late packet order
and final verification still require commissioning; this is not a guarantee of radio-level
atomicity. A blocked release deliberately remains protected and degraded, rather than
letting an unrelated approximate renderer silently finish it. This may leave white visible
until the capability/ownership problem is explicitly resolved. It is a review blocker for
universal scene behavior, not successful restoration.

## Native dynamic scene capability — unresolved deployment blocker

The currently established API does not provide a verified per-light scene-ID/palette rejoin.
The [aiohue light controller](https://github.com/home-assistant-libs/aiohue/blob/main/aiohue/v2/controllers/lights.py)
and [feature models](https://github.com/home-assistant-libs/aiohue/blob/main/aiohue/v2/models/feature.py)
expose light updates and dynamics speed/duration, not an established member scene rejoin.
This is a limit of the demonstrated supported path, not a universal claim about Hue.

Manual scene identity is retained and release reports
`native_manual_scene_rejoin_requires_capability`. Automatic scenes with unknown dynamics,
`auto_dynamic` other than false, or active dynamic palette similarly block. A known static
native automatic scene may use its exact member action. Manual scene restoration remains
conservative even if that scene might be static because canonical scene evidence alone
does not establish that capability. No sampled static replacement or whole-scene recall is
presented as dynamic compliance. The old unsafe whole-scene assertion was replaced by a
scene-identity/protected-sibling safety assertion; native rejoin is explicitly **not passed**.

Smallest next step: establish a supported, isolated per-member playback capability with
read-only API evidence and separately authorized physical proof, or obtain an explicit
contract policy for a functional override that preserves native playback. Do not deploy
this candidate as a complete dynamic-scene repair before that review.

## Cabinet acceptance matrix

PASS below means deterministic offline code evidence, not physical verification.

| Required case | Evidence | Result / boundary |
|---|---|---|
| 1. Either door independently | six owners x both first doors | PASS |
| 2. Two-door interval, partial close | same identity/snapshot; white until final close | PASS |
| 3. Rapid close/reopen | ordered submissions and final white | PASS offline |
| 4. Old close suspended/new open | controlled owners, metadata, native-read awaits | PASS |
| 5. Stale snapshot clearing | old epoch cannot clear new interval | PASS |
| 6. Capture while previous white remains | canonical desired appearance retained | PASS |
| 7. Daily | loaded baseline, cabinet only | PASS |
| 8. Holiday | static native action cabinet only | PASS static; dynamic BLOCKED |
| 9. 49ers | static native action cabinet only | PASS static; dynamic BLOCKED |
| 10. Off | cabinet OFF | PASS |
| 11. Per-light Manual appearance | current native desired fields | PASS |
| 12. Manual-OFF | OFF retained, ownership/protection asserted | PASS |
| 13. Manual dynamic scene/protected sibling | identity retained; zero peer commands | safety PASS; rejoin BLOCKED |
| 14. Sync at opening | zero physical dispatch | PASS |
| 15. Sync during release | stale plan rejected, interval retained | PASS |
| 16. Owner/scene changes while open | latest Manual/Manual-OFF/Off; fresh scene action | PASS static; dynamic BLOCKED |
| 17. Internal white/context preserved | actual observer + canonical classifier | PASS |
| 18. Internal white/context missing | payload receipt + aggregate feedback | PASS tested evidence |
| 19. Independent homeowner ON/OFF/color | direct HA and delayed contextless callback completion | PASS tested distinct payloads |
| 20. Failed/rejected/delayed/cancelled render | retained pending interval, no false success | PASS offline; transport order UNVERIFIED physically |
| 21. Sensors unavailable/recovery | unknown not closed; current owner on recovery | PASS |
| 22. Restart/nightly | no old snapshot replay; new generation fences | PASS offline |
| 23. No sibling commands | every fake physical destination asserted; whole-scene denied | PASS |
| 24. Ordinary Daily latency | no full evaluator or sleep; short service path | PASS architecture; physical latency UNVERIFIED |

## Regression evidence and remaining uncertainty

New tests assert ownership layers/protection as well as exact dispatch destination/payload.
Controlled await barriers reproduce stale work without timing-sensitive sleeps. External
promotion callbacks are driven through completion. Existing OFF1/OFF2, four-surface rendering,
Manual-OFF, family/session suppression, restart/nightly, and PR95/97/108/109/F01/F02 suites
are retained. No promotion_observer or global cross-surface invalidation change is included.

A05 is not globally solved: older operation-only markers still have their commissioned
fallback behavior. Contextless homeowner intent identical to an outstanding automatic
native payload is intrinsically indistinguishable from its duplicate response with current
evidence. Late responses outside bounded receipts may also become ambiguous. Do not claim
universal A13 proof for those cases; a generic receipt-lifecycle follow-up needs its own
mixed-producer regression/commissioning scope. No cabinet-specific exception was added to
the classifier. Legacy Manual helpers remain transitional and are not updated by this path.

Local suite uses the repository-pinned HA 2025.12.5/Python 3.13; production's newer HA/Python
compatibility and actual loaded mixed Python revision remain unverified. No standalone type
checker is configured. Hassfest and HACS validation run in hosted PR CI.

## Deployment set and rollback (plan only; not authorized)

1. Before approval, verify installed file identities/imported revision by a supported safe
   method, preserve the exact mixed-install backup and live configurations. Resolve the
   dynamic-member blocker and review A05 evidence limits.
2. Use one reviewed commit's HLM metadata files and matching legacy executor files. Legacy
   source paths map from `legacy/custom_components/home_lighting_reconciliation/` to the
   installed `custom_components/home_lighting_reconciliation/`. HACS HLM-only update does
   not install the legacy integration or YAML.
3. Apply only the cabinet automation and `home_lighting_liquor_white` script changes after
   separate deployment authorization. Preserve live Spa startup recovery, thermostat aliases,
   marked stop OFF/Temperature Color attribution and unrelated package/scripts. Do not
   replace live automations or the package wholesale with repository snapshots. Confirm
   metadata and cabinet services exist before enabling their callers. A coordinated approved
   activation/restart plan is required; none occurred in this task.
4. Roll back that coherent component/caller set from the exact backup, not merely the two
   reported PR110 files or an old HACS label. Do not edit storage, reset ownership or replay
   historical commands. Retain the previous live automation backup for operator review;
   it contains known defects, so rollback is an operational decision, not compliance proof.
5. Commission under explicit future authorization: both door orders; fast reopen during
   close; six supported owners; independent intent while open; protected sibling; Sync
   before/during release; scene changes; unknown recovery; restart open; nightly boundary;
   missing-context receipts. Record canonical ownership, exact destinations, interval status,
   observed restoration and latency, not only whether the bulb appears on/off. Dynamic rejoin
   must have its own proof before being marked compliant.

## Product file map

- HLM `command_receipts.py`: bounded native-payload causal metadata.
- HLM `__init__.py`, `ha_observer.py`, `services.yaml`: metadata service and evidence collection.
- Legacy `cabinet.py`: interval executor, current owner resolver and observed completion.
- Legacy `__init__.py`: service, synchronous input invalidation, diagnostics/repair coordination.
- Legacy `engine.py`: explicit functional white and pending-member verification exclusion.
- Legacy `rendering.py`: cabinet-only functional protection, including last-await fencing.
- Legacy `hue.py`: scene dynamics capability evidence.
- Legacy `services.yaml`: executor service description.
- Cabinet automation and package white script: notification-only callers.
- `test_cabinet_recovery.py`, `test_cabinet_feedback.py`, `test_command_receipts.py`: new regressions.
- `test_ownership.py`, `test_engine.py`: adapt existing functional tests; preserve assertions
  except explicitly replacing unsafe whole-scene behavior with retained-identity safety.
- Three cabinet review/provenance documents: source/installed distinction, matrix and manifest.

Deferred: broader A05 receipt lifecycle; PR110 cross-surface invalidation/incident attribution;
Spa/49ers migration; alerts; unrelated audit findings. No dependency/version changes, contract
changes, generated outputs, local workstation paths or audit bundles belong in the PR.

## Validation record

- Captured live implementation reproduction: 8 contract failures, 3 positive controls.
- New cabinet/feedback/receipt tests: 80 passing; combined with configuration tests:
  104 passing (24 configuration/YAML cases).
- Ruff: passed across HLM, legacy custom components and tests.
- Python compilation: passed across both component trees, cache outside the checkout.
- HLM forbidden-command text and structural AST review: 13 modules, no dispatch paths;
  diagnostic `command_authority` remains literal false.
- Full repository pytest: **728 passed**, one existing warning, 128.29 seconds.
- `git diff --check`: passed. Hosted Hassfest/HACS/regression results are recorded in the PR.
- One existing aiohttp/HA deprecation warning; no suppressed or weakened test policy.

The engineering candidate is suitable for code/CI review. It is not permission to merge or
deploy, and native dynamic scene restoration remains a review blocker as described above.
