# Ownership acceptance scenario foundation

This matrix is the executable-test bridge for the approved Ownership Scenario Contract. The
contract remains normative. Scenario IDs here are stable regression-test identifiers and group the
approved behavior into implementation families.

Each executable scenario follows:

`initial layers/session -> event -> resulting layers/session -> effective state -> command eligibility`

| ID | Family | Initial state | Event | Required result |
|---|---|---|---|---|
| MAN-001 | Manual | Daily exposed | homeowner sets purple | Manual purple becomes exposed above Daily for only the commanded entity |
| MAN-002 | Manual | Daily -> Manual purple | homeowner OFF | remove Manual appearance; expose Daily; do not create Manual-OFF yet |
| MAN-003 | Manual | Daily re-exposed after MAN-002 | homeowner OFF | create Manual-OFF; effective state OFF |
| GRP-001 | Group OFF | mixed Manual/automatic members | first explicit group OFF | release homeowner exceptions in commanded group and resolve every member back to HLM |
| GRP-002 | Group OFF | HLM state exposed after GRP-001 | second explicit group OFF | commanded group becomes explicit Manual-OFF |
| F49-001 | 49ers | Evening -> 49ers(session G1) | homeowner overrides participating entity | suppress 49ers family G1 and expose homeowner/underlying state as contract requires |
| F49-002 | 49ers | 49ers family G1 suppressed | score/reassert child event G1 | child is ineligible; no 49ers command may be planned |
| F49-003 | 49ers | G1 ended/suppressed | independent game session G2 starts | G2 is eligible; G1 suppression does not cross sessions |
| SPA-001 | Spa Gauge | Evening -> Spa Gauge(session S1) | homeowner overrides/dismisses spa light | suppress Spa Gauge family S1 |
| SPA-002 | Spa Gauge | family S1 suppressed | target-temperature blink S1 | child is ineligible; no gauge command may be planned |
| SPA-003 | Spa Gauge | S1 ended/suppressed | independent spa session S2 | S2 is eligible |
| OUT-001 | Availability | any valid layers | Hue entity becomes unavailable | preserve logical ownership; unavailable is not OFF and creates no Manual layer |
| OUT-002 | Recovery | Hue unavailable across 01:59 | entity recovers | expire ordinary Manual lifecycle, resolve current-time rightful owner, do not replay missed commands |
| RST-001 | Restart | coherent still-valid Manual state | HA/integration restarts | preserve only if desired state and lifecycle evidence remain trustworthy |
| RST-002 | Restart | ambiguous/stale Manual reconstruction | HA/integration restarts | relinquish questionable Manual state and default to HLM |
| SCN-001 | Selective scene | Holiday eligible with one Manual member | Holiday presentation requested | protected Manual member receives no Holiday action, including no transient whole-scene recall |
| LIQ-001 | Liquor | underlying owner + door opens | door OPEN | push functional 255/4000K layer and preserve underlying state |
| LIQ-002 | Liquor | functional door layer active | final door closes | remove functional layer and reveal current valid underlying layer |
| LIQ-003 | Liquor repair | door reports OPEN | first homeowner OFF | record first repair-OFF evidence; forced white remains |
| LIQ-004 | Liquor repair | LIQ-003, no door transition | second homeowner OFF | persistent repair override suppresses forced white and keeps liquor light OFF |
| LIQ-005 | Liquor repair | repair override active | door changes OR homeowner manually turns light ON | clear repair override and resume normal door behavior |
| GEN-001 | Generation | command plan generation N | generation N+1 established before dispatch | stale generation N work is ineligible |
| INT-001 | Attribution | HLM-owned command consequence | matching state telemetry | do not create Manual ownership |
| INT-002 | Attribution | no HLM attribution, high-confidence homeowner command | state/scene command succeeds | create/update Manual ownership for exact commanded scope |
| INT-003 | Attribution | unexplained physical drift only | state differs from HLM desired state | reconciliation may repair; drift alone does not create Manual ownership |

## Test-layer policy

The majority of ownership semantics belong in fast pure-Python tests. Home Assistant integration
tests are reserved for event attribution, service-call planning, persistence/restart, Hue evidence,
and adapter behavior. Physical commissioning remains required after a production command path is
introduced.

## Foundation milestone exit criteria

- Contract exists in repository-owned documentation.
- ADR records the per-entity layered architecture and migration boundary.
- Scenario IDs exist for the major approved behavior families.
- Pure ownership core can represent layers, Manual-OFF, family sessions/suppression, and stale
  generation rejection without importing Home Assistant.
- Representative tests prove those primitives.
- No production event listener or command path is added.
