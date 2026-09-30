# Unified intent audit — September 29, 2026

Baseline: `900eda4dcadabe59f594b628cbf462a0133efe5f` (v0.2.31), clean worktree.
The Ownership Scenario Contract and settled clarifications govern intended behavior.
The before-map was recorded before behavioral changes. Closure findings below reflect
the resulting implementation; physical commissioning is still required.

## Before: authority and evidence map

Eight policy-bearing adapter mechanisms can veto otherwise qualified homeowner evidence:
(1) context/availability normalization, (2) guard-based HA attribution rewrite,
(3) scene/Sync recall gates, (4) external automatic-evidence filtering,
(5) Front Eve-only single-member veto, (6) topology/exact-group qualification,
(7) retained receipt and delayed promotion validation, (8) nightly/reset OFF epoch.
Within (4), scene settling, boundary settling, dynamics and renderer markers independently
reject leaves. Core `intent_policy` is the ninth classification boundary.
`OwnershipOperations` separately enforces transaction integrity: generation, identity,
deduplication, capacity, success, availability and newer-intent order. These are not
alternative homeowner policies. Recovery validates durable ownership, never new intent.

`ha_observer` derives direct-user versus parent versus context-less evidence, rewrites HA
attribution under a surface guard, observes it through `ShadowRuntime`/`OwnershipOperations`,
then passes the same event to `promotion_observer`. Promotion filters leaves, correlates
burst topology, retains exact receipts, selects leaf/group/scene scope and observes again.
The pure classifier only sees the resulting evidence kind, not the reason leaves disappeared.
Scene recalls enter as separate exact group operations. Group selection has bounded overlap
and armed-parent precedence; it must not become N leaf commands.

Projection reads engine layers. The legacy renderer/verifier consumes that projection and
legacy automatic underlay; it does not classify homeowner intent. Its guard and attribution
service feed back into the observer. Reconciliation can inspect while correlation remains
provisional: a 30-second debounce is not a transaction barrier. Projection currently exposes
no pending intent, so an already-running repair can race delayed qualification.

## Proven causal defects

`_handle_nightly_boundary` and reset put every managed leaf into
`_post_boundary_off_entities`. `async_save` persists that set and startup restores it.
`_automatic_external_evidence_reason` rejects membership regardless of elapsed time,
operation, current automatic restoration or renderer receipt. Only direct-user and selected
scene/group evidence clear it. Thus Daily can reappear at sunset while the leaf remains
in an overnight OFF epoch; qualified context-less OFF is removed before correlation can
promote it. This proves the reported rejection path; no live trace is available to prove
which earlier restoration receipt arrived at the bridge.

A surface guard alone also rewrites direct HA user intent as HLM consequence, even without
an exact renderer target/operation. Conversely, the Front Eve-only veto applies different
leaf ownership policy to the same qualified topology. Scene settling blocks OFF as well as
appearance. Excluded automatic leaves still enter the shared burst, poisoning later
correlation. A completed single promotion also leaves its consumed burst alive, allowing
previous light A to poison a new action on B.

## Correction design

Evidence acquisition retains topology, scene, availability, exact target/operation marker,
Sync and ordering facts. The pure policy receives normalized causal evidence and selects
one reason. Guards without exact renderer receipts do not override high-confidence user
intent. Renderer markers retain the existing operation scope and two-second TTL.

The boundary OFF epoch is runtime quarantine, not ownership or durable homeowner evidence.
It closes per leaf on a witnessed current renderer appearance consequence or accepted
homeowner operation. A mere ON state does not close it. The existing short settling window
still rejects immediate late shutdown reports. No new elapsed-time escape is introduced.
Old persisted epoch fields are ignored; durable Manual recovery remains unchanged.

Retire consumed single-intent evidence and exclude known consequences from burst scope.
Pending correlation is exposed separately from ownership: the renderer defers those leaves
without manufacturing Manual. Scene/group scope and the core OFF/family engine remain intact.

## Limits requiring physical evidence

Context-less same-operation telemetry within a matching renderer marker is intrinsically
ambiguous with current receipts (operation/target/time, no bridge command identity). A
single leaf plus aggregate is the commissioned correlation policy, not proof of human agency
in every possible Hue trace. This change does not claim to infer arbitrary external group
identity, migrate family/functional policies or fix mixed native dynamic scene capability.
Physical acceptance must include rapidly interleaved renderer and homeowner events.

## Additional end-to-end findings

Reconciliation repairs bypassed the marker registration used by primary rendering.
Those repairs now acquire the same exact consequence receipts and recheck current
ownership after the metadata await. Ownership publication previously followed storage
awaits; accepted intent now publishes before checkpoint I/O. Accepted group operations
also consume retained provisional receipts so their own barrier cannot postpone the
rightful automatic restoration.

First-discovery reports (`old_state` absent without affirmative user context) could be
qualified by initial aggregate fanout. They are recovery telemetry, excluded from the
homeowner correlator. Scene operations previously treated absent/unavailable members
as available; now they skip those members without replay on recovery. Missing structural
session state is normalized as unknown, consistently with the existing conservative
scene/group gates; it is not silently considered inactive.

## After: authority map

1. The HA/scene adapters acquire normalized causal facts and candidate scope.
2. `classify_intent` decides homeowner eligibility for initial and qualified evidence.
   Topology acquisition supplies evidence, not a second ownership policy.
3. `OwnershipOperations` validates generation, ordering, successful outcomes and scope.
4. The layered engine deterministically performs release, Manual or Manual-OFF transitions.
5. The synchronous projection exposes resolved ownership plus a separate provisional
   evidence barrier. The barrier is runtime-only and never creates Manual ownership.
6. Rendering/reconciliation consume that projection, recheck currentness and register
   exact command consequences. Resulting telemetry returns to the same classifier.

No new surface/entity policy was added. Existing declarative scene, group and structural
sensor mappings remain evidence-acquisition configuration. Family/session eligibility,
OFF semantics, persistence recovery and exact-group transaction integrity remain core
responsibilities. Their production-family migration is outside this correction.

## Regression harness fidelity

The new matrix uses real HA state changes, real observer callbacks and promotion timers,
actual ownership operations and the legacy renderer, with simulated device execution.
Aggregate ON reflects physical member states; updates may be attribute-only. Restoration
emits leaf and aggregate propagation. Assertions cover layers, effective protection,
canonical reasons and mutation as well as physical/logical state. Storage can be blocked
while rendering proceeds. Ordinary fixtures explicitly seed pre-existing devices instead
of treating discovery as a command; dedicated tests exercise unattributed bootstrap and
unavailable/missing recovery. Older mechanism-local tests remain, with causal renderer
receipts made explicit rather than inferred from a whole-surface guard.

## Offline closure validation

- Full suite: 540 passed; focused affected pipeline/configuration suite: 247 passed.
- New end-to-end matrix: 80 cases included in those runs.
- One existing HA/aiohttp deprecation warning, no suppressed test failures.
- Ruff, integration compileall and diff whitespace check passed.
- Eleven tracked YAML files parsed; the 24 configuration tests passed.
- Twelve HLM Python modules parsed structurally: no service-dispatch calls and no
  forbidden lighting/scene command text. Two existing metadata-service registrations remain.
- Current-main archive failed the new Backyard boundary/current-renderer regression
  before the correction; the corrected branch passes it.
- Test dependencies pin HA 2025.12.5, while production is HA 2026.9.x. Live compatibility
  and physical timing remain commissioning requirements. No configured standalone type
  checker was run. Hosted Hassfest/HACS checks were not run locally (Docker unavailable);
  no push/PR is authorized to trigger them.

No production configuration, dependencies or manifests changed. Work remains unstaged
and uncommitted on the review branch; no deployment or live service calls were performed.
