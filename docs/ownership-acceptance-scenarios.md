# Ownership contract acceptance matrix — Phase 1

[The supplied contract](OWNERSHIP_SCENARIO_CONTRACT.txt) is normative and unchanged.
This matrix separates executable core behavior, shadow adapter evidence, and deferred production
work. “Core” means deterministic unit tests; it does not claim physical lighting control.

The attachment references 90 reviewed scenarios but enumerates only selected scenario decisions.
This matrix covers every normative section and every explicitly numbered decision; it does not
invent missing individual scenario descriptions. Earlier reconciliation acceptance evidence is
historical legacy evidence, not acceptance of this replacement engine.

## Implemented core and shadow regressions

Test modules below live in `tests/`. Baseline PR #22 tests remain unchanged; the new Phase 1 structural-admission test now supplies explicit system priority.

| Contract / scenario | Behavior | Executable evidence | Status |
|---|---|---|---|
| §1–2, MAN-001, INT-002 | High-confidence exact-light Manual with desired native state | `test_layered_operations.py::test_manual_retains_native_desired_state_for_exact_entity`; existing `test_ha_shadow_observer.py` | Core + shadow |
| §1 ambiguity, scenario 38 | Unknown/drift/recovery/failed evidence never invents Manual | `test_non_intent_does_not_create_manual_or_change_valid_state`; `test_failed_operation_does_not_win_over_successful_intent`; `test_ownership_recovery_intent.py` | Core |
| §3, scenarios 1/2/5/28 | Automatic OFF → Manual-OFF; Manual OFF → pop; later OFF → Manual-OFF | `test_automatic_off_and_manual_first_then_second_off`; existing `test_ownership_layer_engine.py` | Core |
| §1.4, scenario 83 | Repeated OFF peels a representative unsuppressed stack | existing `test_off_peels_manual_then_suppresses_spa_then_creates_manual_off` | Core; settled eligibility distinction below |
| §3/5/6, scenarios 15/21 | Daytime Manual survives ordinary activation; Manual-OFF evening reset; nightly expiry | `test_evening_activation_preserves_daytime_manual_but_reclaims_manual_off`; existing boundary tests | Core; nightly shadow wired, sunset adapter deferred |
| §4, scenario 76, GRP-001/002 | First group OFF releases mixed exceptions; second/further OFF keeps group OFF | `test_mixed_group_off_releases_then_stays_off_on_further_offs` | Core |
| §2/4 | Group appearance is one explicit operation, shared provenance, exact members | `test_group_appearance_is_one_operation_with_shared_provenance` | Core; external group inference deferred |
| §4 | Changed members/intervening overlapping intent reset group sequence | `test_changed_group_membership_restarts_first_off`; `test_intervening_member_intent_disarms_only_overlapping_group` | Core |
| §13/15, scenario 81 | Unavailable/failed group members skipped, no replay/fake OFF | `test_group_skips_unavailable_or_failed_members_and_never_replays`; `test_unavailable_group_off_does_not_create_fake_off`; `test_all_unavailable_does_not_arm_group_off` | Core |
| §16, scenario 82 | Newest successful intent wins; stale/duplicate OFF cannot pop again | `test_newest_successful_intent_wins_and_duplicate_off_does_not_peel_twice`; `test_stale_group_is_rejected_atomically_if_one_member_has_newer_intent` | Core |
| §8, scenarios 55/56 | Structural session can supersede, retain underlying Manual; expired Manual cannot return | `test_structural_session_push_preserves_manual_beneath_and_expiry_prevents_return`; existing structural precedence tests | Core only |
| §9/10, scenarios 59/60/86–89 | Family override suppresses exact session; Manual release cannot resurrect it; new session eligible | `test_family_override_suppresses_parent_and_child_release_cannot_resurrect`; existing family tests | Core primitives only |
| §17, scenario 90 | Child eligibility requires eligible matching parent/session | `test_child_requires_matching_existing_parent_not_just_session`; existing suppressed-child tests | Core primitives only |
| §14/19, GEN-001 | Config generation and runtime revision invalidate older work | `test_stale_work_invalidated_by_new_truth`; `test_new_generation_rejects_old_operation_and_discards_old_layers` | Core; no dispatcher |
| §14, RST-001 | Native Manual and operation/group provenance round-trip | `test_persistence_roundtrip_provenance_but_not_transient_group_arming`; `test_version_one_payload_remains_readable_without_fabricating_provenance`; existing `test_shadow_persistence.py` | Core + existing shadow Store tests |
| §13/14, scenarios 84/85, RST-002 | Missing/invalid/ambiguous persisted desired state relinquishes to HLM | `test_malformed_persisted_manual_drops_even_with_external_trust`; `test_malformed_persistence_containers_fail_closed`; existing recovery/boundary tests | Core; outage commissioning deferred |
| PRs #11–#22 | Context attribution, iterable/live topology, startup retries, availability exclusion | Existing `test_ha_shadow_observer.py`, `test_live_topology_membership.py`, `test_attribution_correlation.py`, `test_external_intent_promotion.py` | Existing regressions retained |
| PR #22 + §16 | Delayed qualified leaf cannot replace newer HA user command | `test_layered_observer_regressions.py::test_late_external_qualification_cannot_replace_newer_direct_user_intent` | Shadow |
| Group commissioning | Six path leaves + four aggregates remain one unpromoted ambiguous burst | `test_six_leaf_hue_group_burst_is_not_six_homeowner_operations` | Shadow; does not claim exact group identity |
| Sync commissioning | Parent-linked churn and isolated null-context teardown do not claim Manual | `test_sync_parent_chain_and_isolated_teardown_do_not_create_manual` | Shadow regression, not universal Sync inference |
| First external OFF | Qualified Manual leaf OFF remains `released_manual` | `test_qualified_external_first_off_still_releases_manual_and_no_service_called` | Shadow |
| Observability | Bounded histories, exposed/underlying layers, operation/rejection evidence | `test_diagnostics_bound_history_and_expose_stack_and_rejection` | Core + published shadow attributes |
| Safety | Entire HLM component has no lighting dispatch; authority false | `test_entire_hlm_component_has_no_service_dispatch_or_command_tokens`; observer service-call trap and existing safety tests | Executable invariant |

Additional Phase 1 regressions cover group-wide pre-operation family resolution
(`test_group_override_evaluates_all_members_against_same_exposed_family`), second group OFF family
suppression, atomic rejection of invalid member admission, incomplete native color evidence,
normal expiry of migrated legacy Manual, and entity/layer/session/member diagnostic limits.

## Intentionally deferred contract behavior

| Contract sections / scenarios | Deferred work and reason |
|---|---|
| §7/18, SCN-001 | Selective Hue scene action execution and repair: requires future renderer; never simulate success with a whole-zone command |
| §8 | Actual Sync session start/end wiring, session-boundary qualification across all event orderings; retain current conservative attribution |
| §9, F49-001–003, 55/56/86/87 | Convert game start/end, scoring and periodic reassertion to family engine; current automation unchanged |
| §10, SPA-001–003, 59/60/88/89 | Convert temperature, pulse/blink and session orchestration; current automation unchanged |
| §11/12, LIQ-001–005 | Liquor functional layer wiring, actual native scene rejoin, persistent two-OFF repair override and door/ON reset |
| §13/14, OUT-002, 84 | Full offline-across-sunset/01:59/outage commissioning and current-truth adapter reconstruction; no missed-event replay added |
| §17 | Actual alert overlay duration/lifecycle and family-child scheduling; core can retain/remove overlays but no alert conversion |
| §19 | Active reconciler migration, selective commands, guards, retries and dispatch token checks; no dispatcher permitted |
| §20, scenario 25 | Separate Apple Home/Siri membership audit |
| §22/23/25 | Production helper/YAML retirement, integration consolidation and HACS distribution |
| §24/26 | PR/merge/deployment and live household commissioning require later authorization |
| §27/28 | Entity inventory and workflow context retained; no production entity/config migration |

## Settled eligibility rules and adapter evidence limits

Scenario 83 demonstrates generic popping through eligible layers. Specific family suppression
makes a family ineligible for its current parent session; popping Manual cannot restore it.
Unsuppressed families remain eligible. Group-OFF arming is transient across restart; trustworthy
Manual-OFF may restore independently. These are settled decisions, recorded in the
[contract clarifications](OWNERSHIP_CONTRACT_CLARIFICATIONS.md).

A state-only observer cannot establish a second identical OFF command without an event, exact
external group identity, or a historical command ID for arbitrary late telemetry. Core operation
semantics are implemented now; those adapter capabilities are not claimed as commissioned.


## Final architectural review traceability (2026-09-17)

All tests in this table live in `test_phase1_architectural_review.py`, unless stated otherwise.
“Implemented/tested” is offline core or shadow evidence, never production lighting acceptance.

| Contract rule / review concern | Executable tests | Status |
|---|---|---|
| General OFF / recency, A–G adversarial sequences | `test_sequence_a_updates_manual_then_two_distinct_offs` through `test_sequence_g_recovery_does_not_replay_skipped_group_member` | Implemented/tested |
| Priority, admission, exposure are separate | `test_priority_never_reordered_by_hidden_owner_update_or_low_priority_supersession`, `test_priority_and_manual_exposure_are_independent`, `test_structural_protection_is_explicit_not_a_magic_numeric_rank`, `test_ineligible_structural_barrier_cannot_keep_manual_below_ordinary_owner` | Implemented/tested |
| Deterministic stack and member order | `test_exhaustive_short_sequences_are_deterministic_and_one_exposed_owner`, `test_group_member_permutation_has_identical_state_and_diagnostics`, `test_skipped_group_diagnostics_are_member_order_independent` | Implemented/tested |
| Duplicate receipts do not become repeated OFF | `test_duplicate_does_not_increment_homeowner_count_or_revision`, `test_duplicate_direct_ha_off_context_cannot_become_second_off` | Implemented/tested; HA adaptation awaits live shadow commissioning |
| Stale receipt / runtime / configuration / recovery isolation | `test_evicted_failed_receipt_cannot_replay_on_recovery`, `test_recovery_after_newer_runtime_intent_cannot_win`, `test_stale_tokens_reject_operation_and_system_admission`, `test_fresh_runtime_rejects_old_token_even_when_generation_revision_match`, `test_reconfigure_prunes_removed_entities_and_rejects_old_generation` | Implemented/tested |
| Atomic groups / owner identity / defensive state | `test_capacity_failure_is_atomic_across_group_members`, `test_logical_owner_cannot_replace_another_owner_by_reusing_layer_id`, `test_returned_layer_metadata_cannot_mutate_authoritative_state` | Implemented/tested |
| Session, group and correlation boundedness | `test_six_month_session_simulation_is_bounded_and_stale_sequence_is_rejected`, `test_group_interaction_is_bounded`, `test_correlation_is_bounded_and_overflow_cannot_qualify_partial_tail` | Implemented/tested |
| Active parent required | `test_family_child_without_parent_identity_cannot_be_exposed`, sequence F | Implemented/tested primitives; production family wiring deferred |
| Real prior schema migration and rollback | `test_actual_pr22_storage_migration_uses_current_configuration_and_evidence`, `test_actual_pr22_parser_drops_v2_safely_on_rollback`, `test_pr22_recovery_edge_cases_fail_closed`, `test_conflicting_persisted_manual_records_drop_instead_of_incidental_last_wins`, `test_legacy_on_only_is_not_complete_for_brightness_capable_current_light` | Implemented/tested; live restart/rollback commissioning deferred |
| Fast external ON/OFF/appearance and availability | `test_rapid_external_operations_each_require_fresh_aggregate_and_latest_wins`, `test_availability_change_invalidates_previously_retained_external_leaf` | Implemented/tested; awaits live shadow commissioning |
| Aggregate telemetry is not exact group intent | `test_aggregate_user_context_does_not_create_fake_canonical_group_layer`; existing six-leaf regression | Implemented/tested; exact external group inference deferred |
| Durable OFF vs transient group conversation | `test_durable_manual_off_survives_without_restoring_group_interaction`, `test_group_first_off_restart_next_off_is_conservative_new_first` | Implemented/tested; runtime group arming never survives restart |
| Literal scenario 83 vs §10/scenarios 59/88 | Generic removable-layer sequence B; qualified family suppression sequence F and existing family tests | Implemented/tested generic eligibility and suppression; production Spa deferred |

[Final review](reviews/phase-1-final-architecture-review.md) records migration, rollback, bounds,
API compatibility inventory, and settled contract clarifications. The 2025.12.5 harness does not establish
2026.9.2 live compatibility. Repeated identical external commands without reports and arbitrary
late telemetry without source identity remain adapter evidence limitations.


## Final clarification/closure evidence

All following tests live in `tests/test_phase1_closure.py`. Each is implemented/tested offline;
actual Spa/49ers wiring, external group inference and live HA commissioning remain deferred.

| Clarified rule | Regression |
|---|---|
| Family + parent session scope; hidden updates retain provenance but remain ineligible; Manual release exposes Evening; new session independent | `test_family_scope_hidden_updates_release_and_new_independent_session` |
| Generic pop may expose an unsuppressed family | `test_unsuppressed_underlying_family_remains_available_after_generic_manual_pop` |
| No child/suppression receipt can invent a parent; stale end cannot disturb new session/group state | `test_missing_parent_cannot_be_created_by_suppression_or_child_receipt`, `test_stale_session_end_cannot_disarm_new_group_interaction` |
| First group OFF → restart → reconstruct automatic ownership → new first OFF; logs cannot restore arming | `test_restart_discards_group_arming_even_with_persisted_provenance_and_fake_history` |
| Valid Manual-OFF restores without physical-state input and expires normally | `test_durable_manual_off_restores_without_physical_state_then_expires` |
| Incomplete/ambiguous/unavailable/expired evidence relinquishes ownership | `test_questionable_persisted_manual_off_relinquishes_to_hlm` |
| Durable group provenance and family suppression cannot reconstruct arming or homeowner events | `test_restored_group_manual_off_provenance_cannot_arm_future_off`, `test_restored_suppression_neither_arms_group_nor_creates_homeowner_intent` |
| Old generation/work authority cannot mutate new runtime | `test_pre_restart_group_receipt_and_family_work_have_no_authority` |
| Unavailable membership, newer individual intent and individual double-OFF remain separate | `test_unavailable_group_member_and_newer_individual_intent_remain_separate` |
| Bounded eligible/suppressed/ended family and recovery diagnostics; Store v1/payload v2 unchanged | `test_bounded_diagnostics_distinguish_family_end_and_recovery_from_live_arming` |
| Physical OFF/unavailable without persisted intent cannot create Manual-OFF | `test_physical_state_without_persisted_intent_cannot_restore_manual_off` |
