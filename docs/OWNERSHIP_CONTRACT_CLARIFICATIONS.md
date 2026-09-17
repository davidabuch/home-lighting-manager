# Ownership contract — settled Phase 1 clarifications

Authority: the project owner's explicit final Phase 1 closure instructions. Read together with
[the original contract](OWNERSHIP_SCENARIO_CONTRACT.txt), which remains unchanged. These decisions
settle two interpretations and do not replace unrelated contract rules.

## Generic pop and family eligibility

OFF/release removes an exposed removable layer and resolves the next **currently eligible** layer.
Scenario 83 demonstrates this generic mechanism; it does not override specific family/session
suppression rules. Where the contract specifies qualified homeowner intervention suppresses an
active family, that **family + parent session** remains ineligible for the remainder of its session.

Suppression applies to the parent and all its children, across participating members. It does not
suppress unrelated families or future independent sessions. Useful hidden desired state/provenance
may remain, but hidden updates cannot unsuppress or expose the family or re-enable children.
Manual release exposes the next eligible non-suppressed owner. Without suppression, an otherwise
eligible underlying family can still be exposed. Session end retires its state; a new independent
parent session is evaluated independently. A stale child must never create or replace a parent.

## Group-OFF arming and restart

First explicit group OFF releases homeowner exceptions for available requested members and arms
a transient current-runtime first/second-OFF interaction. Restart discards that interaction.
A later group OFF starts a new interaction: it is a **new first group OFF**. No operation ledger,
count, timestamp, provenance, diagnostic record or physical state may reconstruct old arming.

Manual-OFF is different: accepted explicit desired ownership may survive restart when trustworthy,
coherent and within its lifecycle. It does not itself arm a group interaction. It remains effective
until a valid ownership transition. A new first group OFF applies the unchanged §4 release rule
to the requested members, including any restored Manual-OFF exceptions. Physical OFF by itself
cannot manufacture Manual-OFF; unavailable is never OFF. Recovery never replays historical commands.

## Implementation boundary

These are settled Phase 1 core semantics, not production automation wiring. External Hue group
identity inference and Spa/49ers policies remain later adapter work. HLM remains observation-only.
Store envelope v1/domain payload v2 need no change: suppressed session evidence and durable Manual
already have representations; group-OFF arming and diagnostics are excluded from persistence.
See [ADR 0002](adr/0002-layered-operations-off-engine.md) and the
[acceptance matrix](ownership-acceptance-scenarios.md) for implementation/test traceability.
