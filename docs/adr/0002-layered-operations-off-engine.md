# ADR 0002: Explicit operations over the shadow ownership stack

- Status: Implemented for review; no production command authority
- Date: 2026-09-16
- Baseline: `f8f57e35e1be3013a7a003841d19ec6c0eb10978` (PR #22)
- Builds on: ADR 0001 and commissioned attribution PRs #11–#22
- Normative authority: [Ownership Scenario Contract](../OWNERSHIP_SCENARIO_CONTRACT.txt)

## Boundary

HLM remains a shadow observer. There is no command dispatcher, lighting service-call path,
production configuration change, or authority transfer. Legacy YAML, helpers, reconciler, scene
monitor, 49ers, Spa, and liquor automations are untouched. Their known contract contradictions
remain production migration work, not a reason to weaken this model.

The existing contract file is byte-identical to the supplied attachment. Its historical SHA and
commissioning narrative do not override current main or the September 16 evidence.

## State and admission

`OwnershipEngine` retains multiple `OwnershipLayer` records per physical entity. A layer has an
identity, kind, owner, desired appearance, lifecycle boundary, optional family/session, optional
parent-layer identity, originating operation/group, generation, and insertion order.

Resolution traverses an admitted bottom-to-top stack and exposes its last valid layer. System
priority, homeowner recency, session eligibility, and physical delivery are different concepts.
`system_priority` belongs to system admission; it is not the resolver's universal owner score.
`protects_from_manual` represents an explicit structural barrier; `removable` permits generic OFF
pop without pretending the layer is a particular production family.

`activate_automatic(..., priority=..., supersede=...)` admits AUTOMATIC, FUNCTIONAL and OVERLAY
layers. New system layers cannot cross a higher-priority system layer. Ordinary activation also
stays below Manual; permitted session supersession may cross Manual. Same logical
(owner, kind, family, session, parent) updates replace desired state in the original stack slot,
even when a caller supplies a fresh layer ID. They preserve stable identity for child references.
Admission-policy changes require configuration re-resolution, not an accidental desired-state
update. An ID collision across logical owners is rejected. One homeowner slot prevents impossible
Manual/Manual-OFF coexistence.

`admit_manual` places accepted explicit homeowner intent above eligible ordinary layers and below
explicit structural barriers, independently of numeric priority. Transitional `push` and the
observer's configured `manual_precedence` retain PR #22 numeric admission only at the legacy
shadow ingress. Pure explicit `HomeownerOperation` does not require that configuration. Numeric
admission is not a future policy for Daily/Holiday/Sync/49ers/Spa/functional families.

`Appearance` stores native ON/OFF, brightness, Kelvin temperature, XY/RGB/HS values, effect,
color mode, and optional scene identity/evidence. It validates booleans, finite channel ranges,
and the presence of a declared native color representation. It does not derive RGB from XY or
invent a scene from sampled colors. Multiple representations already reported by HA can be
retained; future rendering must choose the reported supported mode. A simple explicit ON is a
valid on/off appearance; restoration still requires independent trust/coherence evidence.
Scene identity is representable but the current light-state adapter cannot prove it on restart.

Manual appearance and Manual-OFF are different layer kinds. Physical OFF, failed commands,
unavailability, and recovery telemetry are never substitutes for a Manual-OFF record.

## Operation and ordering model

`HomeownerOperation` is one immutable receipt: operation ID, configuration generation, monotonic
observation-ingress sequence, kind, evidence, exact requested member outcomes, and optional
explicit group identity. `MemberOutcome.succeeded` means evidence that an external source operation occurred successfully;
it is not HLM dispatch, delivery, or verification status. `mutated` means only an accepted shadow
ownership transaction. Member availability describes receipt-time observation quality. Group identity is not inferred from nearby leaf reports.

`OwnershipOperations.apply` first uses the existing classifier. It rejects ambiguous/failed
intent, stale generations, duplicate operations, and operations older than the newest accepted
intent for their affected members. Available successful members may mutate; unavailable/failed
members are recorded as skipped and are never queued. Invalid appearance in an otherwise
successful member rejects the whole operation rather than inventing partial desired state.
One group operation yields one history record and shared operation provenance on affected layers.
A group older than a newer successful member intent is rejected atomically.

Adapters must assign sequence at ingress before any delayed qualification. The HA observer now
reserves that sequence before classification, and the existing external promotion adapter retains
it. Direct HA user reports also retain context/entity receipt identity, so repeated reports of
one command cannot act as two OFFs inside the bounded receipt window. Thus an old pending leaf qualified by later aggregate propagation cannot replace a newer
HA user intent. The pure API also supports explicit source operation identities for later adapters.
It cannot identify a historical source command from arbitrary late telemetry without such evidence.

Recent operations are bounded to 12; duplicate IDs to 128. Per-entity sequence watermarks persist
for the runtime generation, including skipped members of accepted group receipts, so eviction of
an old ID does not permit replay against a recovered member. A retired-sequence floor rejects receipts whose deduplication evidence has fallen out of the
bounded window, including failed/all-unavailable receipts. IDs and sequences must remain attached
to the same logical source operation; callers cannot relabel a duplicate as fresh. IDs/sequences
are process evidence, not durable executable work. Group members are canonicalized before mutation.
A failed admission/capacity check rolls back the whole ownership transaction.

## OFF state machine

| Exposed state / operation | Result |
|---|---|
| Automatic or no layer + successful individual OFF | Create Manual-OFF above current state |
| Manual appearance + individual OFF | Pop Manual; expose next valid underlying layer |
| Automatic re-exposed + next successful OFF | Create Manual-OFF |
| Generic removable non-family system layer + individual OFF | Pop it; reveal the next valid layer |
| Session-family layer + individual OFF | Suppress exact family/session; expose underlying eligible layer |
| Manual-OFF + further individual OFF | Remain Manual-OFF |
| First explicit group OFF | Remove homeowner exceptions for available successful requested members |
| Second OFF for same group and exact requested membership | Manual-OFF for available successful members |
| Further group OFF | Remain Manual-OFF until an intervening meaningful operation/boundary |

Group sequence identity includes unavailable requested members, while mutations do not. A changed
membership set restarts the first-OFF phase. Successful overlapping member/group intent disarms
that sequence; unrelated intent does not. Failed/ambiguous observations do not advance it.
Nightly/evening reset, session end, or configuration-generation change also disarms it.

This is based on exposed state and operation history, not time-of-day special cases. In shadow,
exposure is logical, not proof that a lower layer was physically reasserted: no renderer exists.
A future command adapter must supply real distinct successful operations; a duplicated OFF report
is not a second OFF.

## Family and child primitives

A `FamilySession` has exact family/session identity and suppression reason. Parent and child
layers share that identity. Children can additionally name `parent_layer_id`; they are eligible
only while that matching parent exists and both belong to the same eligible session. Parent
removal, suppression, expiry, or generation invalidation prevents its child being exposed.

All members of a group operation are resolved against the same pre-operation snapshot, so
suppressing a shared family on one member does not accidentally dismiss a newly exposed lower
family on the next member. Manual replacing a family uses explicit homeowner placement when the family uses explicit admission.

A qualified Manual override of the currently exposed family suppresses that session. Individual
OFF dismisses an exposed family. Second group OFF also suppresses exposed families before placing
Manual-OFF; first group OFF only releases homeowner exceptions. Ending a session removes its layers/children and suppression;
the ended identity cannot be restarted through a stale sequence. Explicit session receipts carry
monotonic per-family sequence, and system layers bind that sequence. One current session per
family, a sequence watermark, and a bounded retired-ID cache replace unbounded lifetime history.
Legacy sequence-less admission fails closed after that cache fills; future adapters must supply
sequence and current work authority. A new independent session is eligible. No 49ers/Spa trigger, scoring, pulse, or blink is converted in this phase.

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

## Lifecycle, generation, and recovery

Ordinary Manual/Manual-OFF use `nightly_0159`; expiry applies even to covered layers. The new
`evening_activation` primitive reclaims Manual-OFF while retaining daytime Manual appearance.
Only the already-existing nightly observer is wired; sunset and session boundaries remain future
adapters. The liquor repair override is deliberately not implemented or treated as ordinary Manual.

`WorkToken(generation, revision, authority_id)` captures configuration authority, runtime state
revision, and an opaque runtime incarnation (never a wall-clock ordering key).
New intent, layers, family changes, boundaries, and group-state changes invalidate old work.
Configuration generation discards prior runtime layers/session eligibility and requires current
truth/recovery reconstruction. System admission, session lifecycle and explicit operations can require a matching token; no
dispatcher exists. Reconfiguration discards old scopes and layers. HA restoration is filtered by
current configured entities. Recovery is a startup-only transaction and cannot run after newer
runtime mutation. Tokens, including incarnation identity, are never restored.

Domain persistence payload is version 2. HA Store's outer envelope remains version 1 so existing
storage can be read; valid domain v1 records migrate without fabricated operation provenance.
Only Manual layers and suppressed sessions are durable evidence. Desired native state and
operation/group identity round-trip. Automatic/functional/overlay layers, open double-OFF phases,
operation history, watermarks, and work tokens are not restored or replayed.

Malformed containers/records, missing appearance, wrong ON/OFF kind, invalid channels, and missing
native representation are rejected. Conflicting records for one entity/family are dropped, not
resolved by list order. Current capabilities can invalidate incomplete legacy ON-only records. Recovery additionally requires temporal validity, trustworthy
desired state, and coherent ownership evidence. Old ordinary Manual lacking a boundary receives
the nightly boundary. Session suppression survives only with independent same-session evidence.
The observer retains current-state corroboration and missed-01:59 checks; ambiguous scene identity
or unavailable current state relinquishes questionable Manual. Valid Manual is not erased just
because unrelated ambiguous telemetry arrives. Restart/outage commissioning remains deferred.

## Observability

The existing health entity retains `command_authority: false`, counters, attribution ledger,
and topology diagnostics. Added attributes expose runtime revision, layer stacks and eligibility,
Manual/Manual-OFF status, family sessions/suppression, pending group-OFF scopes, latest mutation,
latest accepted homeowner operation, and latest rejection reason. `family_sessions` reports explicit
eligibility/suppression; `ended_family_sessions` shows at most 16 retired identities.
`startup_recovery` contains bounded scalar counts of restored Manual/Manual-OFF/suppression,
rejected records, payload rejection and the startup group-history reset. It is an immutable-in-time
startup summary, not the current arming state; `group_off_sequences` reports current runtime arming.
Parse/recovery diagnostic fields are not serialized and do not change payload v2 or Store v1.

Limits: 32 entities, 8 layers per entity, 16 sessions/group sequences, 12 operations, 32 displayed
members per operation/scope. Arbitrary layer metadata is excluded. Counts/truncation expose missing
entity/layer detail without unbounded histories. These are diagnostics, never desired-state input. State and buffer caps, rollback behavior, exact
API inventory and final review findings are recorded in
[the final architecture review](../reviews/phase-1-final-architecture-review.md).

## Preserved evidence and deferred adapters

The classifier is unchanged. HA user context, parent-context exclusion, availability exclusion, live iterable topology discovery,
startup retries, bounded attribution ledger, single-leaf qualification, and the conservative group
burst behavior remain in place. Aggregate user-context reports no longer become fake canonical
per-light layers. Promotion requires fresh aggregate corroboration per retained leaf receipt;
availability/parent-chain changes invalidate retained leaf evidence. Correlation buffers are capped;
overflow stays unqualified until the burst ends, rather than forgetting earlier disqualifying leaves. Regression tests exercise the six-leaf/four-aggregate burst,
qualified first OFF, delayed promotion versus newer intent, and parent-linked/isolated Sync teardown.
This does not claim a general Sync-session inference solution for all possible event ordering.

Exact external group identity, a repeated command without a state change, scene-selection identity,
and late telemetry's source operation require additional read-only evidence in future adapter work.
Those are observability limitations of the current adapter, not permission to invent intent.
Selective scene execution, bounded active reconciliation, alert orchestration, production helper
retirement, full restart/outage commissioning, HACS consolidation, and Siri room membership remain
separate milestones. No next phase is started here.


## Restart interaction decision and rollback

First group OFF → restart → later group OFF is a **new first group OFF**. Group-OFF arming
is transient runtime interaction state, never durable ownership. Recovery cannot reconstruct it
from operation/group provenance, logs, diagnostics, timestamps, physical OFF or persisted Manual.
This is settled by the owner's clarification, consistent with ambiguity → HLM and no historical replay.

Trustworthy Manual-OFF remains independently durable within its lifecycle. Restoring it does not
arm a group interaction. A later explicit first group OFF applies the existing §4 rule: release
homeowner exceptions for available requested members, including restored Manual-OFF. Until that
new operation or a valid lifecycle boundary, restored Manual-OFF continues to own its members.

PR #22's unchanged Store envelope can read payload v2 but its domain parser rejects the unsupported
version and restores no ownership. It then checkpoints an empty v1 payload in its ordinary startup
path. Generation advances; recovery is never homeowner intent. Rollback therefore loses shadow
Manual/suppression evidence conservatively, without manual `.storage` deletion/editing or false
Manual. Forward loading that checkpoint is supported. Tests use the actual PR #22 parser snapshot
and a fixture generated by its serializer; no live storage is accessed.
