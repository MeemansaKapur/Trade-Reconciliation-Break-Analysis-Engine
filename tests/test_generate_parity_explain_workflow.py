import datetime as dt
import json

import pandas as pd
import pytest

from conftest import RUN_DATE
from recon.common import add_bd, gen_config, isin_is_valid, load_rules
from recon.engine import reconcile
from recon.explain import EXPLAIN_COLS, LLM, explain_breaks, payload, taxonomy, validate
from recon.generate import generate_days
from recon.normalise import normalise_external
from recon.parity import compare
from recon.workflow import (InvalidTransition, MakerCheckerViolation, REGISTER_COLS, approve, assign, propose,
                            refresh_ages, run_workflow)


# --------------------------------------------------------------------------- generator
def test_isin_check_digit():
    assert isin_is_valid("US0378331005") and isin_is_valid("INE002A01018") and not isin_is_valid("US0378331006")


def test_add_bd_skips_weekend():
    assert add_bd(dt.date(2026, 11, 20), 1) == dt.date(2026, 11, 23)


def test_same_seed_same_data():
    a = generate_days(RUN_DATE, 3_000, 9, 1)[0]
    b = generate_days(RUN_DATE, 3_000, 9, 1)[0]
    for k in ("internal", "external_raw", "truth"):
        pd.testing.assert_frame_equal(a[k], b[k])


def test_injected_rates_match_config():
    n = 100_000
    t = generate_days(RUN_DATE, n, 5, 1)[0]["truth"]
    sc = t[t.side == "INT"]["scenario"].value_counts()
    sc["missing_internal"] = (t[(t.side == "EXT")]["scenario"] == "missing_internal").sum()
    sc["duplicate"] = (t["true_status"] == "DUPLICATE").sum()
    for k, rate in gen_config()["rates"].items():
        assert abs(sc.get(k, 0) / n - rate) <= 0.001, (k, sc.get(k, 0) / n, rate)


def test_vendor_file_normalises():
    raw = generate_days(RUN_DATE, 2_000, 3, 1)[0]["external_raw"]
    e = normalise_external(raw)
    assert e["isin"].map(isin_is_valid).all() and e["counterparty"].notna().all()
    assert set(e["side"]) == {"BUY", "SELL"} and not e["ext_trade_id"].str.endswith(" ").any()


# --------------------------------------------------------------------------- US-07 parity
def test_us07_parity_identical_versions_is_100pct_unchanged(small_day):
    _, I, E = small_day
    a = reconcile(I, E, RUN_DATE, load_rules("v1.2"))
    b = reconcile(I, E, RUN_DATE, load_rules("v1.2"))
    c = compare(a, b)
    assert c["counts"]["UNCHANGED"] == c["records"]


def test_us07_parity_detects_tolerance_change(small_day):
    day, I, E = small_day
    c = compare(reconcile(I, E, RUN_DATE, load_rules("v1.2")), reconcile(I, E, RUN_DATE, load_rules("v1.3")), day["truth"])
    nb = c["diff"][c["diff"].bucket == "NEWLY_BROKEN"]
    assert len(nb) > 0 and (nb["scenario"] == "fee_mismatch").all()


# --------------------------------------------------------------------------- US-05 explanations
class FakeLLM(LLM):
    def __init__(self, reply):
        self.provider, self.model, self.client, self.reply = "fake", "fake-1", object(), reply

    def complete(self, system, user):
        return self.reply


def _breaks(small_day):
    _, I, E = small_day
    return reconcile(I, E, RUN_DATE, load_rules("v1.3")).breaks


def test_us05_explainer_never_changes_engine_fields(small_day, tmp_path, monkeypatch):
    monkeypatch.setattr("recon.explain.CACHE", tmp_path / "c.jsonl")
    b = _breaks(small_day)
    out = explain_breaks(b, FakeLLM('{"explanation":"x","suggested_action":"y","cause_category":"Z"}'))
    pd.testing.assert_frame_equal(out.drop(columns=EXPLAIN_COLS), b)


def test_us05_works_without_api_key(small_day, tmp_path, monkeypatch):
    monkeypatch.setattr("recon.explain.CACHE", tmp_path / "c.jsonl")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = explain_breaks(_breaks(small_day).head(20))
    assert (out["explainer"] == "template").all() and out["explanation"].notna().all()


def test_us05_guardrails_reject_bad_output(small_day):
    b = _breaks(small_day)
    p = payload(b[b.break_type == "TIMING"].iloc[0], taxonomy())
    ok = {"explanation": "Settle date differs by 1 day.", "suggested_action": "Amend value date.",
          "cause_category": "SETTLEMENT_DATE_DIFFERENCE"}
    assert validate(json.dumps(ok), p)[0] is not None
    assert validate("not json", p)[1] == "invalid_json"
    assert validate(json.dumps({**ok, "cause_category": "MADE_UP"}), p)[1] == "cause_out_of_enum"
    assert validate(json.dumps({**ok, "explanation": "This is an AMOUNT_MISMATCH."}), p)[1] == "contradicts_break_type"


def test_us05_payload_is_minimised(small_day):
    p = payload(_breaks(small_day).iloc[0], taxonomy())
    text = json.dumps(p)
    assert "INT-" not in text and "CST" not in text and "EQ-BOOK" not in text and "CP0" not in text


# --------------------------------------------------------------------------- US-04 / US-08 workflow
def _reg():
    r = pd.DataFrame([{c: None for c in REGISTER_COLS}]).astype(object)
    r.loc[0, ["break_id", "break_type", "status", "opened_on", "side"]] = ["BRK-1", "AMOUNT_MISMATCH", "OPEN", RUN_DATE, "BOTH"]
    return r.set_index("break_id", drop=False)


def test_us04_age_counts_business_days_open_to_resolved():
    reg = _reg()
    d3 = add_bd(RUN_DATE, 2)  # Fri -> Tue: two business days later
    assign(reg, "BRK-1", "controller_a", RUN_DATE)
    propose(reg, "BRK-1", "controller_a", "BOOKING_CORRECTED", RUN_DATE)
    approve(reg, "BRK-1", "controller_b", d3)
    refresh_ages(reg, add_bd(RUN_DATE, 6), threshold=5)
    r = reg.loc["BRK-1"]
    assert r.status == "RESOLVED" and r.age_days == 2 and r.resolution_reason == "BOOKING_CORRECTED" and not r.escalated


def test_us04_escalation_flag_at_threshold():
    reg = _reg()
    refresh_ages(reg, add_bd(RUN_DATE, 5), threshold=5)
    assert bool(reg.loc["BRK-1", "escalated"])


def test_maker_cannot_approve_own_proposal():
    reg = _reg()
    assign(reg, "BRK-1", "controller_a", RUN_DATE)
    propose(reg, "BRK-1", "controller_a", "BOOKING_CORRECTED", RUN_DATE)
    with pytest.raises(MakerCheckerViolation):
        approve(reg, "BRK-1", "controller_a", RUN_DATE)


def test_cannot_skip_workflow_states():
    reg = _reg()
    with pytest.raises(InvalidTransition):
        approve(reg, "BRK-1", "controller_b", RUN_DATE)


def test_us08_late_arrivals_clear_earlier_breaks(tmp_path):
    r = run_workflow(RUN_DATE, 4, 3_000, 77, "v1.3", out_root=tmp_path)
    m = r["metrics"]
    assert m["late_arrivals_injected"] > 0
    assert m["late_arrivals_auto_matched"] / m["late_arrivals_injected"] >= 0.95
    assert m["maker_checker_self_approvals"] == 0 and m["controls_passed_all_days"]
