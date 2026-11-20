# User stories and acceptance criteria

Written before the code, then used as the UAT script (see [uat_log.md](uat_log.md)).
Format: *As a [role], I want [capability], so that [benefit]*, with acceptance criteria in Given / When / Then.
Each criterion names the automated test that checks it.

---

## US-01 — Nothing is silently dropped
**As a** controller, **I want** every internal trade and every custodian record to end up either matched or listed as a break, **so that** nothing disappears between the two books.

| AC | Given / When / Then | Test |
|---|---|---|
| 01.1 | **Given** N internal and M external records **When** the run completes **Then** the output has exactly N + M record outcomes, one per record | `test_us01_every_record_has_exactly_one_outcome` |
| 01.2 | **Given** any input **When** matching runs **Then** no record appears in more than one match | `test_us01_no_record_in_more_than_one_match` |
| 01.3 | **Given** the same input and rule version **When** the run is repeated **Then** matches and breaks are identical | `test_us01_rerun_is_identical` |

## US-02 — Rounding noise is auto-accepted, real differences are not
**As a** controller, **I want** small rounding differences auto-accepted within a configured tolerance, **so that** I only review real breaks.

| AC | Given / When / Then | Test |
|---|---|---|
| 02.1 | **Given** an internal and external trade with the same ISIN, quantity and settle date **And** an amount difference of ₹0.40 **And** the tolerance is ₹1.00 **When** the reconciliation runs **Then** the pair is TOLERATED **And** the audit log records `rule_id=P2_ECON` and the difference | `test_us02_rounding_difference_is_tolerated_and_audited` |
| 02.2 | **Given** identical amounts **When** the run completes **Then** the pair is MATCHED | `test_us02_exact_amount_is_matched` |
| 02.3 | **Given** a tolerance changed in `config/rules.yaml` **When** the run repeats **Then** the result changes with no code change | `test_us02_tolerance_change_in_config_changes_result_without_code_change` |
| 02.4 | **Given** a genuine fee difference (₹5–₹50) on a large trade **When** the run completes **Then** it is a break, never TOLERATED (false tolerances = 0 on evaluation) | `test_us02_fee_difference_on_large_trade_is_not_hidden_in_v13`, `reports/eval_*.md` |

## US-03 — Breaks grouped by root cause
**As a** controller, **I want** each break labelled with one root cause from a documented taxonomy, **so that** I can fix many breaks with one action.

| AC | Given / When / Then | Test |
|---|---|---|
| 03.1 | **Given** one injected example of each of the 7 break types **When** the run completes **Then** each gets the correct `break_type` | `test_us03_break_types` (8 cases) |
| 03.2 | **Given** two identical internal bookings and one custodian record **When** classification runs **Then** the later booking is DUPLICATE (not MISSING_EXTERNAL) | `test_us03_duplicate_checked_before_missing` |
| 03.3 | **Given** one internal trade filled in 3 pieces **When** the run completes **Then** it matches as one group, even if an unrelated fill shares its key | `test_us03_partial_fill_group_matches_in_pass3`, `test_us03_partial_fill_subset_search_ignores_unrelated_fill` |
| 03.4 | **Given** a record with a reference match **When** a looser pass also finds a candidate **Then** the strict pass wins | `test_us03_strict_pass_wins_over_loose_pass` |

## US-04 — Ageing and escalation
**As a** team lead, **I want** to see breaks by age bucket, with old breaks flagged, **so that** I can escalate before month-end.

| AC | Given / When / Then | Test |
|---|---|---|
| 04.1 | **Given** a break opened on day 1 and resolved on day 3 **When** ages refresh **Then** age = 2 business days with a resolution reason | `test_us04_age_counts_business_days_open_to_resolved` |
| 04.2 | **Given** an open break aged ≥ the escalation threshold (5 bd) **Then** it is flagged escalated | `test_us04_escalation_flag_at_threshold` |

## US-05 — Plain-English explanation and next step
**As a** controller, **I want** a short explanation and suggested action for each break, **so that** I can triage faster.

| AC | Given / When / Then | Test |
|---|---|---|
| 05.1 | **Given** any LLM output **When** explanations are added **Then** no engine field (status, type, match) changes | `test_us05_explainer_never_changes_engine_fields` |
| 05.2 | **Given** no API key **When** explanations run **Then** every break still gets a template explanation | `test_us05_works_without_api_key` |
| 05.3 | **Given** invalid JSON, an unknown cause, or text naming a different break type **Then** the output is rejected and replaced | `test_us05_guardrails_reject_bad_output` |
| 05.4 | **Given** a break **When** a prompt is built **Then** it contains no trade IDs, accounts or counterparty codes | `test_us05_payload_is_minimised` |

## US-06 — Every decision is traceable
**As an** auditor, **I want** to see which rule and rule version produced every decision, on exactly which input values, **so that** every decision is traceable.

| AC | Given / When / Then | Test |
|---|---|---|
| 06.1 | **When** a run completes **Then** every record has an audit row with `rule_id`, `rule_version` and an input hash | `test_us06_every_decision_has_audit_row_with_rule_and_version` |
| 06.2 | **Given** an output row is deleted **When** tie-out runs **Then** the run fails | `test_us06_tie_out_fails_if_an_output_row_is_deleted` |
| 06.3 | **Given** bad input (invalid ISIN, duplicate key) **Then** the run stops and publishes nothing | `test_us06_bad_input_stops_run_and_publishes_nothing`, `test_us06_duplicate_primary_key_fails_precheck` |

## US-07 — Rule changes are gated by parity
**As a** change manager, **I want** any rule change compared against the current version before release, **so that** changes don't silently alter results.

| AC | Given / When / Then | Test |
|---|---|---|
| 07.1 | **Given** identical versions **When** parity runs **Then** 100% of records are UNCHANGED | `test_us07_parity_identical_versions_is_100pct_unchanged` |
| 07.2 | **Given** a tolerance change **When** parity runs **Then** every NEWLY_BROKEN record is listed and explained | `test_us07_parity_detects_tolerance_change` |
| 07.3 | At least two real rule changes are released with parity reports | `docs/release_notes.md` (3 releases) |

## US-08 — Daily status in one minute
**As a** team lead, **I want** a daily summary of new, resolved and ageing breaks, **so that** I can report status in one minute.

| AC | Given / When / Then | Test |
|---|---|---|
| 08.1 | **Given** a missing record arrives on a later day **When** that day runs **Then** the earlier break auto-resolves (LATE_ARRIVAL_MATCHED) | `test_us08_late_arrivals_clear_earlier_breaks` |
| 08.2 | **When** each day completes **Then** a 5-line summary reports open / new / resolved / escalated / exposure | `daily_summaries.md` per multi-day run |
| 08.3 | Resolutions need a second person: a maker can't approve their own proposal | `test_maker_cannot_approve_own_proposal` |
