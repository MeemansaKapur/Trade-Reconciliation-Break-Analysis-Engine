"""Controls: pre-checks on inputs and tie-out checks on outputs.

Principle: records in == records out, per side and per currency, by count and by amount.
Any failed control stops the run and NOTHING is published (no partial results).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from recon.common import isin_is_valid

INT_REQUIRED = ["int_trade_id", "ext_ref", "isin", "side", "quantity", "price", "amount", "currency",
                "trade_date", "settle_date", "counterparty", "account"]
EXT_REQUIRED = ["ext_trade_id", "isin", "side", "quantity", "price", "amount", "currency",
                "trade_date", "settle_date", "counterparty", "account"]
KEY_FIELDS = ["isin", "side", "quantity", "amount", "currency", "trade_date", "settle_date", "counterparty"]


class ControlFailure(RuntimeError):
    def __init__(self, results: list[dict]):
        self.results = results
        failed = [r for r in results if r["status"] == "FAIL"]
        super().__init__("; ".join(f"{r['check']}[{r['side']}]: {r['detail']}" for r in failed))


def _res(check, side, ok, detail=""):
    return {"check": check, "side": side, "status": "PASS" if ok else "FAIL", "detail": detail}


def pre_checks(internal: pd.DataFrame, external: pd.DataFrame, currencies: set[str]) -> list[dict]:
    out = []
    for side, df, req, pk in (("INT", internal, INT_REQUIRED, "int_trade_id"),
                              ("EXT", external, EXT_REQUIRED, "ext_trade_id")):
        miss = [c for c in req if c not in df.columns]
        out.append(_res("schema", side, not miss, f"missing columns {miss}" if miss else ""))
        if miss:
            continue
        nulls = {c: int(df[c].isna().sum()) for c in KEY_FIELDS + [pk] if df[c].isna().any()}
        out.append(_res("no_null_keys", side, not nulls, f"nulls: {nulls}" if nulls else ""))
        dups = int(df[pk].duplicated().sum())
        out.append(_res("unique_primary_key", side, dups == 0, f"{dups} duplicate {pk}" if dups else ""))
        bad_isin = int((~df["isin"].map(isin_is_valid)).sum())
        out.append(_res("isin_check_digit", side, bad_isin == 0, f"{bad_isin} invalid ISINs" if bad_isin else ""))
        bad_ccy = sorted(set(df["currency"].dropna()) - currencies)
        out.append(_res("known_currency", side, not bad_ccy, f"unknown {bad_ccy}" if bad_ccy else ""))
        bad_side = int((~df["side"].isin(["BUY", "SELL"])).sum())
        out.append(_res("valid_side", side, bad_side == 0, f"{bad_side} rows" if bad_side else ""))
        bad_qty = int((df["quantity"] <= 0).sum())
        out.append(_res("positive_quantity", side, bad_qty == 0, f"{bad_qty} rows" if bad_qty else ""))
        bad_dt = int((pd.to_datetime(df["settle_date"]) < pd.to_datetime(df["trade_date"])).sum())
        out.append(_res("settle_after_trade", side, bad_dt == 0, f"{bad_dt} rows" if bad_dt else ""))
    return out


def control_totals(df: pd.DataFrame) -> dict:
    g = df.groupby("currency").agg(n=("amount", "size"), qty=("quantity", "sum"), amount=("amount", "sum"))
    return {c: {"n": int(r.n), "qty": round(float(r.qty), 4), "amount": round(float(r.amount), 2)}
            for c, r in g.iterrows()}


def post_checks(internal: pd.DataFrame, external: pd.DataFrame, record_status: pd.DataFrame,
                match_results: pd.DataFrame, breaks: pd.DataFrame, audit_log: pd.DataFrame) -> list[dict]:
    out = []
    for side, df, pk in (("INT", internal, "int_trade_id"), ("EXT", external, "ext_trade_id")):
        rs = record_status[record_status["side"] == side]
        dup = int(rs["record_id"].duplicated().sum())
        out.append(_res("record_in_exactly_one_outcome", side, dup == 0, f"{dup} records with >1 outcome" if dup else ""))
        missing = set(df[pk]) - set(rs["record_id"])
        extra = set(rs["record_id"]) - set(df[pk])
        out.append(_res("records_in_equals_records_out", side, not missing and not extra,
                        f"{len(missing)} dropped, {len(extra)} unexpected" if missing or extra else
                        f"{len(df):,} in = {len(rs):,} out"))
        n_rec = int(rs["status"].isin(["MATCHED", "TOLERATED"]).sum())
        n_brk = int((~rs["status"].isin(["MATCHED", "TOLERATED"])).sum())
        out.append(_res("matched_plus_breaks_equals_in", side, n_rec + n_brk == len(df),
                        f"{n_rec:,} reconciled + {n_brk:,} in breaks = {len(df):,}"))
        tin = control_totals(df)
        tout = control_totals(rs.rename(columns={"record_amount": "amount", "record_quantity": "quantity"}))
        bad = [c for c in tin if c not in tout or abs(tin[c]["amount"] - tout[c]["amount"]) > 0.005
               or tin[c]["n"] != tout[c]["n"] or abs(tin[c]["qty"] - tout[c]["qty"]) > 1e-6]
        out.append(_res("control_totals_tie_out_per_currency", side, not bad,
                        f"mismatch in {bad}" if bad else ", ".join(f"{c} {v['amount']:,.2f}" for c, v in tin.items())))
    refs = set(match_results["match_id"]) | set(breaks["break_id"])
    dangling = int((~record_status["ref_id"].isin(refs)).sum())
    out.append(_res("every_outcome_has_a_parent", "ALL", dangling == 0, f"{dangling} dangling refs" if dangling else ""))
    used = set(record_status["ref_id"])
    orphan_rows = len(refs - used)
    out.append(_res("no_empty_match_or_break", "ALL", orphan_rows == 0, f"{orphan_rows} rows with no records" if orphan_rows else ""))
    unaudited = int((~record_status["record_id"].isin(audit_log["record_id"])).sum())
    out.append(_res("every_decision_audited", "ALL", unaudited == 0, f"{unaudited} decisions without audit" if unaudited else ""))
    return out


def assert_all_pass(results: list[dict]) -> None:
    if any(r["status"] == "FAIL" for r in results):
        raise ControlFailure(results)


@dataclass
class ControlReport:
    pre: list[dict]
    post: list[dict]

    @property
    def passed(self) -> bool:
        return all(r["status"] == "PASS" for r in self.pre + self.post)
