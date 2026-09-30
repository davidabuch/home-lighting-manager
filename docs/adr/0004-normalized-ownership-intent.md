# ADR 0004: Normalized intent evidence and ownership publication

Status: implementation for review; physical commissioning required.
Baseline: v0.2.31 / `900eda4dcadabe59f594b628cbf462a0133efe5f`.
The Ownership Scenario Contract and settled clarifications remain authoritative.

## Decision

The adapter acquires context, availability, topology, scene and command receipts.
`IntentEvidence` carries causal facts to `intent_policy.classify_intent`: exact renderer
consequence, aggregate propagation, structural activity, boundary settling/quarantine,
scene appearance settling, scene guard, unknown structural session state and Hue dynamics. The same pure policy handles
initial observation and subsequently qualified external evidence. A provisional UNKNOWN
observation and its later correlated operation are stages of evidence acquisition, not
competing ownership decisions. `OwnershipOperations` remains the sole transaction entry.

The classifier rejects availability/recovery first, then exact command consequences and
unscoped aggregate telemetry. Affirmative direct-user provenance outranks temporal
quarantine; qualified context-less evidence must clear causal exclusions. Aggregate
reports do not turn a direct leaf operation into a group command. Explicit scene/group
scope still requires the existing topology/recall/session evidence and exact-member
transaction checks. The group correlation, ordering, deduplication and family/OFF engines
are retained, not replaced with physical-state comparisons.

A surface guard supplies execution context, never blanket ownership authority over sibling
leaves. Exact renderer markers remain target- and operation-scoped with the commissioned
TTL; they apply even if Hue strips HA context. Reconciliation repairs register the same
markers as primary rendering, including native scenes and selective actions. HLM itself
still dispatches no lighting commands.

Known automatic leaves are excluded from homeowner burst scope. Consumed single-intent
receipts retire their correlation burst, so a completed operation on A cannot poison the
next command on B. Broad unresolved multi-leaf bursts remain conservative. Front Eve
uses the same qualified single-leaf policy as other surfaces; its historical special veto
is removed. Known renderer rebound is now excluded by causal evidence instead.

## Boundary lifecycle

01:59 expires ordinary Manual/Manual-OFF through the unchanged core boundary operation.
The existing short settle window excludes immediate shutdown/rebound telemetry. Runtime
shutdown quarantine remains until a witnessed current renderer appearance receipt or
accepted affirmative homeowner operation closes it for that leaf. Raw ON, recovery,
marker registration alone, an expired marker, and a diagnostic query cannot close it.
An unrelated leaf's restoration does not close or block this leaf's operation.

This is a causal checkpoint, not an overnight ownership state or a new elapsed-time policy.
It is neither serialized nor reconstructed. Old `post_boundary_off_entities` payload fields
are ignored. Store envelope v1/domain payload v2 remain unchanged; durable Manual recovery,
Manual-OFF lifecycle and runtime-only group arming retain their existing semantics.

An isolated context-less rebound while shutdown causality remains unresolved is still
ambiguous. Current state/topology cannot prove it is a person rather than a late bridge
report. Do not claim this architecture can distinguish physically identical receipts
without additional trustworthy command/session evidence. A witnessed current automatic
restoration closes that ambiguity and permits subsequent qualified homeowner OFF.

## Stable publication and rendering barrier

Publish current engine ownership synchronously before disk checkpoint awaits and before
queuing deferred saves. Rendering must not consume an old projection while a newer accepted
intent is waiting for storage. A regression stalls storage while the real renderer runs.

Provisional external receipts expose bounded `pending_intent_entities`, separately from
ownership. The renderer and verifier defer those exact leaves; they do not make them Manual.
Currentness checks include pending scope, so awaited plans cannot cross a new provisional
interaction. Expiry uses the existing correlation deadline, publishes without another
physical event and cancels on reset/boundary/shutdown. It is housekeeping, not a new intent
qualification rule. Accepted scope releases the barrier; unavailable recovery queues no replay. Unattributed initial discovery is recovery,
not a command; scene recalls skip missing/unknown/unavailable members. Physical OFF
never supplies ownership evidence by itself.

The layered per-entity projection remains the authority for protection. Legacy automatic
policy still supplies the underlay and existing execution paths. This change does not migrate
Spa/49ers families, liquor snapshots or transient alerts, and does not enable HLM dispatch.

## Observability and evidence

The bounded evidence ledger records normalized causal facts, canonical decision/reason,
mutation and before/after exposed owner, layer IDs and protection for leaf operations.
Existing exact-group gate diagnostics, scene operation records, generation/revision,
recovery summary and effective projection remain available. Pending scope and verifier
skip reasons distinguish deferment from durable Manual-OFF. Renderer/reconciler diagnostics
record execution and repair; they never establish ownership.

See [the preceding audit](../reviews/unified-intent-audit.md) for the authority map,
root cause and harness limitations. `test_unified_intent_pipeline.py` runs the real HA
observer, real timers, operation engine and legacy renderer with simulated devices across
Main Area, Backyard, Front Eve and Path. It asserts layers, protection, operation reason,
causality and state, not state alone. No offline result is physical commissioning proof.

## Review and physical acceptance

1. Main Area Manual scene: peel leaves individually; unaffected siblings remain Manual.
2. Backyard/Outdoor Kitchen: current Daily restored, Manual -> OFF -> Daily -> OFF -> Manual-OFF.
3. Repeat rapid successive leaves while restoration guards are still active. Correlation may
   defer context-less intent for the existing group window; test after each visible restoration.
4. Same leaf OFF after automatic restoration must not be swallowed by an appearance marker.
5. Recall a Manual Hue scene, then peel individual leaves; scene identity stays on siblings.
6. At 01:59 verify expiry and late telemetry rejection. After witnessed current automatic
   restoration, a legitimate context-less OFF must again be accepted. Capture the causal flags.

Also commission same-operation homeowner actions within a renderer marker window, delayed Hue
scene/resource reports, and unavailable members. Target/operation/time markers alone cannot
prove causality for two otherwise identical competing commands. No deployment is authorized here.
