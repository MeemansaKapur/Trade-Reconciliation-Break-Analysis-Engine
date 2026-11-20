"""Engine behaviour. Test names reference user stories in docs/user_stories.md (US-xx)."""
import copy

import pandas as pd
import pytest

from conftest import RUN_DATE, T1, ext, frames, trade
from recon import controls
from recon.common import add_bd, load_rules
from recon.engine import reconcile


def status_of(res, rid, side):
    rs = res.record_status
    return rs[(rs.record_id == rid) & (rs.side == side)].iloc[0]


# --------------------------------------------------------------------------- US-01 nothing silently dropped
def test_us01_every_record_has_exactly_one_outcome(small_day, rules):
    _, I, E = small_day
    res = reconcile(I, E, RUN_DATE, rules)
    rs = res.record_status
    assert len(rs[rs.side == "INT"]) == len(I) and len(rs[rs.side == "EXT"]) == len(E)
    assert not rs.duplicated(["record_id", "side"]).any()


def test_us01_no_record_in_more_than_one_match(small_day, rules):
    _, I, E = small_day
    res = reconcile(I, E, RUN_DATE, rules)
    ext_ids = res.match_results["ext_trade_ids"].str.split(",").explode()
    assert ext_ids.is_unique and res.match_results["int_trade_id"].is_unique


def test_us01_rerun_is_identical(small_day, rules):
    _, I, E = small_day
    a, b = reconcile(I, E, RUN_DATE, rules), reconcile(I, E, RUN_DATE, rules)
    pd.testing.assert_frame_equal(a.match_results, b.match_results)
    pd.testing.assert_frame_equal(a.breaks, b.breaks)
    assert a.run_id == b.run_id


# --------------------------------------------------------------------------- US-02 tolerance
def test_us02_rounding_difference_is_tolerated_and_audited(rules):
    I, E = frames([trade(1)], [ext(1, amount=250000.40)])
    res = reconcile(I, E, RUN_DATE, rules)
    m = res.match_results.iloc[0]
    assert m.tier == "TOLERATED" and m.rule_id == "P2_ECON" and abs(m.amount_diff - 0.40) < 1e-9
    a = res.audit_log[res.audit_log.record_id == "INT-T-0001"].iloc[0]
    assert a.rule_id == "P2_ECON" and a.rule_version == "v1.3" and a.decision == "TOLERATED"


def test_us02_exact_amount_is_matched(rules):
    I, E = frames([trade(1, ext_ref="CST-T-0001")], [ext(1)])
    m = reconcile(I, E, RUN_DATE, rules).match_results.iloc[0]
    assert m.tier == "MATCHED" and m.rule_id == "P1_REF"


def test_us02_tolerance_change_in_config_changes_result_without_code_change(rules):
    I, E = frames([trade(1)], [ext(1, amount=250000.40)])
    strict = copy.deepcopy(rules)
    strict["tolerances"]["tolerated_abs"]["INR"] = 0.10
    strict["tolerances"]["tolerated_pct"] = 0.0
    assert reconcile(I, E, RUN_DATE, rules).match_results.iloc[0].tier == "TOLERATED"
    assert reconcile(I, E, RUN_DATE, strict).breaks.iloc[0].break_type == "AMOUNT_MISMATCH"


def test_us02_fee_difference_on_large_trade_is_not_hidden_in_v13():
    # ₹30 fee on a ₹1 crore trade: 0.3 bp, inside a 1 bp relative tolerance (v1.0) but above the ₹2 cap (v1.3)
    I, E = frames([trade(1, qty=4000, price=2500.0)], [ext(1, qty=4000, price=2500.0, amount=10_000_030.00)])
    assert reconcile(I, E, RUN_DATE, load_rules("v1.0")).match_results.iloc[0].tier == "TOLERATED"
    assert reconcile(I, E, RUN_DATE, load_rules("v1.3")).breaks.iloc[0].break_type == "AMOUNT_MISMATCH"


# --------------------------------------------------------------------------- US-03 classification
@pytest.mark.parametrize("ints,exts,rid,side,expected", [
    ([trade(1, ext_ref="CST-T-0001")], [ext(1, qty=80, amount=200000.0)], "INT-T-0001", "INT", "QTY_MISMATCH"),
    ([trade(1, ext_ref="CST-T-0001")], [ext(1, settle=add_bd(T1, 1))], "INT-T-0001", "INT", "TIMING"),
    ([trade(1)], [ext(1, settle=add_bd(T1, 1))], "INT-T-0001", "INT", "TIMING"),
    ([trade(1)], [ext(1, amount=252500.0)], "INT-T-0001", "INT", "AMOUNT_MISMATCH"),
    ([trade(1, isin="US0378331005", ccy="USD", price=200.0)],
     [ext(1, isin="US0378331005", ccy="USD", price=200.0, amount=20006.0)], "INT-T-0001", "INT", "FX_ROUNDING"),
    ([trade(1)], [], "INT-T-0001", "INT", "MISSING_EXTERNAL"),
    ([], [ext(1)], "CST-T-0001", "EXT", "MISSING_INTERNAL"),
    ([trade(1), trade(2)], [ext(1)], "INT-T-0002", "INT", "DUPLICATE"),
])
def test_us03_break_types(rules, ints, exts, rid, side, expected):
    if not ints:
        ints = [trade(99, isin="INE009A01021")]  # unrelated record so the frame has a schema
    if not exts:
        exts = [ext(99, isin="INE009A01021")]
    I, E = frames(ints, exts)
    assert status_of(reconcile(I, E, RUN_DATE, rules), rid, side).status == expected


def test_us03_duplicate_checked_before_missing(rules):
    """Priority order: the later of two identical bookings is a DUPLICATE, not MISSING_EXTERNAL."""
    I, E = frames([trade(1), trade(2)], [ext(1)])
    res = reconcile(I, E, RUN_DATE, rules)
    b = res.breaks.iloc[0]
    assert b.break_type == "DUPLICATE" and b.duplicate_of == "INT-T-0001"


def test_us03_partial_fill_group_matches_in_pass3(rules):
    I, E = frames([trade(1, qty=1000)], [ext(1, qty=400), ext(2, qty=350), ext(3, qty=250)])
    m = reconcile(I, E, RUN_DATE, rules).match_results
    assert len(m) == 1 and m.iloc[0].rule_id == "P3_GROUP" and m.iloc[0].n_legs == 3


def test_us03_partial_fill_subset_search_ignores_unrelated_fill(rules):
    # an unrelated fill of 70 shares in the same security pollutes the group; v1.2+ finds the right subset
    legs = [ext(1, qty=400), ext(2, qty=350), ext(3, qty=250), ext(4, qty=70, price=2510.0)]
    I, E = frames([trade(1, qty=1000)], legs)
    assert reconcile(I, E, RUN_DATE, load_rules("v1.1")).match_results.empty
    m = reconcile(I, E, RUN_DATE, load_rules("v1.2")).match_results
    assert m.iloc[0].rule_id == "P3_SUBSET" and set(m.iloc[0].ext_trade_ids.split(",")) == {"CST-T-0001", "CST-T-0002", "CST-T-0003"}


def test_us03_strict_pass_wins_over_loose_pass(rules):
    """Pass 1 (reference) claims its pair before Pass 2 (economics) can steal it."""
    I, E = frames([trade(1, ext_ref="CST-T-0002"), trade(2)], [ext(1), ext(2)])
    res = reconcile(I, E, RUN_DATE, rules)
    m = res.match_results.set_index("int_trade_id")
    assert m.loc["INT-T-0001", "ext_trade_ids"] == "CST-T-0002" and m.loc["INT-T-0001", "rule_id"] == "P1_REF"


# --------------------------------------------------------------------------- US-06 audit + controls
def test_us06_every_decision_has_audit_row_with_rule_and_version(small_day, rules):
    _, I, E = small_day
    res = reconcile(I, E, RUN_DATE, rules)
    assert len(res.audit_log) == len(I) + len(E)
    assert res.audit_log["rule_id"].notna().all() and (res.audit_log["rule_version"] == "v1.3").all()
    assert res.audit_log["inputs_hash"].str.len().eq(32).all()


def test_us06_tie_out_fails_if_an_output_row_is_deleted(small_day, rules):
    _, I, E = small_day
    res = reconcile(I, E, RUN_DATE, rules)
    tampered = res.record_status.drop(res.record_status.index[0])
    post = controls.post_checks(I, E, tampered, res.match_results, res.breaks, res.audit_log)
    failed = {r["check"] for r in post if r["status"] == "FAIL"}
    assert "records_in_equals_records_out" in failed and "control_totals_tie_out_per_currency" in failed


def test_us06_bad_input_stops_run_and_publishes_nothing(small_day, rules):
    _, I, E = small_day
    bad = I.copy()
    bad.loc[0, "isin"] = "INE002A01019"  # wrong check digit
    with pytest.raises(controls.ControlFailure, match="isin_check_digit"):
        reconcile(bad, E, RUN_DATE, rules)


def test_us06_duplicate_primary_key_fails_precheck(small_day, rules):
    _, I, E = small_day
    with pytest.raises(controls.ControlFailure, match="unique_primary_key"):
        reconcile(pd.concat([I, I.head(1)]), E, RUN_DATE, rules)
