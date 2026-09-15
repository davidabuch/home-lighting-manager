# ADR 0001: Layered per-entity ownership engine

- Status: Proposed foundation
- Date: 2026-09-15
- Scope: Home Lighting Manager ownership redesign

## Context

The commissioned Home Lighting Manager currently combines a large Home Assistant YAML package,
per-light and per-surface helper booleans, separate Hue scene-monitor and reconciliation custom
integrations, and independent 49ers, Spa Gauge, and liquor-cabinet automations.

The approved Ownership Scenario Contract requires behavior that cannot be represented reliably by
one surface-level owner plus Boolean Manual exceptions. In particular, ownership must preserve
underlying state, OFF must sometimes remove an exposed layer rather than simply force a bulb off,
Manual state must include a desired appearance, and automation families require session-scoped
suppression that applies to their child effects.

## Decision

Home Lighting Manager will evolve toward one HACS-managed integration with domain
`home_lighting_manager`.

The canonical ownership state is **per managed entity**. Surfaces and groups are policy/targeting
scopes, not the canonical ownership record.

Each managed entity can have an ordered set of valid ownership layers. A layer records at least:

- layer identity
- owner/family identity
- layer kind
- desired appearance, when applicable
- session identity, when applicable
- creation generation/order
- lifecycle/expiry metadata

The effective visible state is the highest currently valid layer after policy resolution.

The first implementation stages are deliberately separated:

1. a pure deterministic ownership core with no Home Assistant side effects;
2. Home Assistant event/intent attribution;
3. rendering/execution planning;
4. reconciliation and verification;
5. persistence/restart reconstruction;
6. migration of special automation families;
7. production authority transfer.

## Core invariants

1. High-confidence homeowner intent may create or change Manual ownership for exactly the entities
   actually commanded.
2. Ambiguous telemetry never invents Manual ownership; ambiguity defaults to HLM ownership.
3. `unknown`/`unavailable` is not OFF.
4. OFF is interpreted by the ownership engine before any physical light command is planned.
5. Manual appearance OFF semantics support release/pop followed by explicit Manual-OFF on the next
   deliberate OFF when the automatic layer has been re-exposed.
6. Automation-family suppression is session-scoped and suppresses both parent and child effects.
7. Child effects cannot resurrect a suppressed parent family.
8. Whole-scene execution is legal only when every affected entity currently authorizes that
   automatic scene. Otherwise scene actions must be applied selectively.
9. Reconciliation consumes resolved per-entity desired state. Reconciliation does not create
   ownership from drift.
10. Work produced under a stale generation/session must not execute after a newer generation wins.
11. Startup/reload/outage recovery resolves what should be true now rather than replaying missed
    historical events.
12. Ordinary Manual and Manual-OFF expire at the contractual nightly/evening lifecycle boundary;
    the liquor-cabinet repair override has its own explicit lifecycle.

## Automation family model

A family has an independent session ID and suppression state.

Examples:

- `49ers`: parent game theme; child score flash and periodic reassertion.
- `spa_gauge`: parent temperature gauge; child pulse and target-temperature blink.

A homeowner dismissal of the exposed family during its active session suppresses that family for
that session. Later child events from the same session are ineligible. A new independent session is
eligible again.

## Execution boundary

This ADR does not authorize the new engine to control production lights. During the foundation and
shadow phases, current production YAML remains authoritative. No old and new command paths may
control the same behavior concurrently.

## Consequences

The design makes mixed ownership ordinary rather than exceptional. For example, a Holiday layer can
exist on all Main Area members while one Kitchen cabinet has a newer Manual layer. The engine
resolves six Holiday members and one Manual member without a surface-level special case.

It also moves selective-scene protection ahead of reconciliation: a protected Manual entity must
never receive an unauthorized whole-scene recall even momentarily.

## Migration direction

The existing Hue V2 evidence, selective scene actions, attribution guards, bounded reconciliation,
semantic verification, and current-time reconstruction should be preserved and moved behind the new
resolved-state interface. The existing `home_hue_scene_monitor` and
`home_lighting_reconciliation` domains can be retired only after equivalent behavior is internal to
`home_lighting_manager` and physically commissioned.
