"""Synthetic trade data generator with ground truth.

Produces, per business date:
  internal_trades.parquet     - the firm's bookings (clean schema)
  external_statement.parquet  - the custodian's statement (vendor column names + messy formats)
  ground_truth.parquet        - what was injected for every record (the engine never reads this)

Usage:
  python -m recon.generate --date 2026-11-20 --n 50000 --seed 42
  python -m recon.generate --date 2026-11-20 --n 50000 --seed 42 --days 15   # multi-day with late arrivals
"""
from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from recon.common import (DATA, add_bd, business_days, gen_config, isin_check_digit, load_yaml)

INT_COLS = ["int_trade_id", "ext_ref", "isin", "side", "quantity", "price", "amount", "currency",
            "trade_date", "settle_date", "counterparty", "account"]
SCENARIOS = ["missing_external", "missing_internal", "amount_mismatch", "fee_mismatch", "rounding_noise",
             "qty_mismatch", "timing", "fx_rounding", "duplicate", "partial_fill"]
TRUE_STATUS = {
    "clean": "MATCHED", "rounding_noise": "TOLERATED", "amount_mismatch": "AMOUNT_MISMATCH",
    "fee_mismatch": "AMOUNT_MISMATCH", "qty_mismatch": "QTY_MISMATCH", "timing": "TIMING",
    "fx_rounding": "FX_ROUNDING", "missing_external": "MISSING_EXTERNAL",
    "missing_internal": "MISSING_INTERNAL", "duplicate": "MATCHED",  # the original is clean
}


# ----------------------------------------------------------------------------- reference data
def reference_data(cfg: dict) -> dict:
    rng = np.random.default_rng(cfg["reference_seed"])
    rows = []
    alnum = list("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789")
    for ccy, count in cfg["n_securities"].items():
        for k in range(count):
            if ccy == "INR":
                body = f"INE{k + 1:03d}{alnum[k % 24]}0101"
                price = float(np.round(np.exp(rng.normal(np.log(1200), 0.9)), 2))
            elif ccy == "USD":
                body = "US" + "".join(str(x) for x in rng.integers(0, 10, 9))
                price = float(np.round(np.exp(rng.normal(np.log(150), 0.7)), 2))
            else:
                body = ("DE000" if k % 2 == 0 else "FR000") + "".join(rng.choice(alnum, 6))
                price = float(np.round(np.exp(rng.normal(np.log(90), 0.6)), 2))
            rows.append({"isin": body + isin_check_digit(body), "currency": ccy, "base_price": max(price, 1.0)})
    sec = pd.DataFrame(rows)
    ranks = rng.permutation(len(sec)) + 1
    w = 1.0 / ranks ** cfg["zipf_exponent"]
    sec["weight"] = w / w.sum()
    cpty = sorted(load_yaml("counterparty_map.yaml"))
    cw = rng.dirichlet(np.ones(len(cpty)) * 3)
    accounts = [f"EQ-BOOK-{i:02d}" for i in range(1, cfg["n_accounts"] + 1)]
    return {"securities": sec, "cpty": cpty, "cpty_w": cw, "accounts": accounts}


# ----------------------------------------------------------------------------- day state
@dataclass
class Pending:
    """Records withheld today that will turn up on a later business date (late confirm / late booking)."""
    ext_rows: dict = field(default_factory=dict)   # due_date -> list[(ext_row, truth_row)]
    int_rows: dict = field(default_factory=dict)


def _draw_lag(rng, cfg) -> int | None:
    probs = cfg["late_arrival_lag_probs"]
    keys = list(probs)
    p = np.array([probs[k] for k in keys], dtype=float)
    k = keys[rng.choice(len(keys), p=p / p.sum())]
    return None if k is None else int(k)


def _clean_trades(run_date, n, rng, ref, cfg) -> pd.DataFrame:
    sec = ref["securities"]
    idx = rng.choice(len(sec), size=n, p=sec["weight"].to_numpy())
    s = sec.iloc[idx].reset_index(drop=True)
    ccy = s["currency"].to_numpy()
    qty_mu = np.where(ccy == "INR", np.log(300), np.log(200))
    qty = np.maximum(1, np.round(np.exp(rng.normal(qty_mu, 1.0)))).astype(np.int64)
    price = np.round(s["base_price"].to_numpy() * (1 + rng.normal(0, 0.01, n)), 2)
    price = np.maximum(price, 0.5)
    amount = np.round(qty * price, 2)
    cycle = cfg["settlement_cycle"]
    settle = {c: add_bd(run_date, cycle[c]) for c in cycle}
    df = pd.DataFrame({
        "int_trade_id": [f"INT-{run_date:%Y%m%d}-{i:06d}" for i in range(n)],
        "ext_ref": None,
        "isin": s["isin"].to_numpy(),
        "side": rng.choice(["BUY", "SELL"], n),
        "quantity": qty.astype(float),
        "price": price,
        "amount": amount,
        "currency": ccy,
        "trade_date": run_date,
        "settle_date": [settle[c] for c in ccy],
        "counterparty": rng.choice(ref["cpty"], n, p=ref["cpty_w"]),
        "account": rng.choice(ref["accounts"], n),
    })
    return df


def _assign_scenarios(df, rng, cfg) -> np.ndarray:
    rates = cfg["rates"]
    n = len(df)
    is_inr = (df["currency"] == "INR").to_numpy()
    non_inr_share = max(1e-9, 1 - is_inr.mean())
    out = np.empty(n, dtype=object)
    for mask, inr in ((is_inr, True), (~is_inr, False)):
        p = {k: rates[k] for k in SCENARIOS}
        p["fx_rounding"] = 0.0 if inr else min(0.5, rates["fx_rounding"] / non_inr_share)
        names = ["clean"] + SCENARIOS
        probs = np.array([0.0] + [p[k] for k in SCENARIOS])
        probs[0] = 1 - probs.sum()
        out[mask] = rng.choice(names, size=mask.sum(), p=probs)
    # partial fills need at least 2 shares to split
    small = (df["quantity"].to_numpy() < 2) & (out == "partial_fill")
    out[small] = "clean"
    return out


def generate_days(start: dt.date, n: int, seed: int, days: int = 1) -> list[dict]:
    cfg = gen_config()
    ref = reference_data(cfg)
    bmap = load_yaml("counterparty_map.yaml")
    rng = np.random.default_rng(seed)
    pending = Pending()
    dates = business_days(start, days)
    horizon = set(dates)
    results = []
    for run_date in dates:
        results.append(_generate_one(run_date, n, rng, ref, cfg, bmap, pending, horizon))
    return results


def _generate_one(run_date, n, rng, ref, cfg, bmap, pending: Pending, horizon) -> dict:
    df = _clean_trades(run_date, n, rng, ref, cfg)
    scen = _assign_scenarios(df, rng, cfg)
    df["ext_ref"] = None
    ext_ids = rng.choice(10 ** 7, size=int(n * 1.2) + 10, replace=False)
    ext_iter = iter(f"CST{run_date:%y%m%d}{x:07d}" for x in ext_ids)
    has_ref = rng.random(n) < cfg["ext_ref_populated_rate"]

    ext_rows, truth, int_extra = [], [], []
    keep_int = np.ones(n, dtype=bool)
    tol_noise = {"INR": (0.02, 0.90), "USD": (0.02, 0.04), "EUR": (0.02, 0.04)}
    fee_size = {"INR": (5.0, 50.0), "USD": (0.25, 2.50), "EUR": (0.25, 2.50)}
    recs = df.to_dict("records")
    for i, r in enumerate(recs):
        sc = scen[i]
        ccy = r["currency"]
        base_ext = {"ext_trade_id": None, "isin": r["isin"], "side": r["side"], "quantity": r["quantity"],
                    "price": r["price"], "amount": r["amount"], "currency": ccy, "trade_date": r["trade_date"],
                    "settle_date": r["settle_date"], "counterparty": r["counterparty"], "account": r["account"]}

        if sc == "partial_fill":
            k = int(min(rng.integers(2, 5), r["quantity"]))
            cuts = np.sort(rng.choice(np.arange(1, int(r["quantity"])), size=k - 1, replace=False))
            parts = np.diff(np.concatenate([[0], cuts, [int(r["quantity"])]]))
            amts = [round(float(p) * r["price"], 2) for p in parts[:-1]]
            amts.append(round(r["amount"] - sum(amts), 2))
            noisy = rng.random() < cfg["partial_fill_noise_share"]
            if noisy:
                lo, hi = tol_noise[ccy]
                amts[0] = round(amts[0] + rng.choice([-1, 1]) * rng.uniform(lo, hi), 2)
            ids = []
            for p, a in zip(parts, amts):
                e = dict(base_ext, ext_trade_id=next(ext_iter), quantity=float(p), amount=a)
                ext_rows.append(e)
                ids.append(e["ext_trade_id"])
                truth.append(_truth(e["ext_trade_id"], "EXT", "partial_fill",
                                    "TOLERATED" if noisy else "MATCHED", r["int_trade_id"], None, run_date))
            truth.append(_truth(r["int_trade_id"], "INT", "partial_fill", "TOLERATED" if noisy else "MATCHED",
                                None, ",".join(ids), run_date))
            continue

        e = dict(base_ext, ext_trade_id=next(ext_iter))
        if sc == "missing_external":
            keep_ext, ref_ok = False, False
        elif sc == "missing_internal":
            keep_ext, ref_ok = True, False
            keep_int[i] = False
        else:
            keep_ext, ref_ok = True, True

        if sc == "amount_mismatch":
            e["amount"] = round(r["amount"] * (1 + rng.choice([-1, 1]) * rng.uniform(0.001, 0.02)), 2)
        elif sc == "fee_mismatch":
            lo, hi = fee_size[ccy]
            e["amount"] = round(r["amount"] + rng.choice([-1, 1]) * rng.uniform(lo, hi), 2)
        elif sc == "rounding_noise":
            lo, hi = tol_noise[ccy]
            e["amount"] = round(r["amount"] + rng.choice([-1, 1]) * rng.uniform(lo, hi), 2)
        elif sc == "qty_mismatch":
            q = max(1.0, float(np.round(r["quantity"] * rng.uniform(0.5, 0.95))))
            if q == r["quantity"]:
                q = r["quantity"] + 1
            e["quantity"], e["amount"] = q, round(q * r["price"], 2)
        elif sc == "timing":
            shift = int(rng.choice(list(cfg["timing_shift_probs"]), p=list(cfg["timing_shift_probs"].values())))
            e["settle_date"] = add_bd(r["settle_date"], shift)
        elif sc == "fx_rounding":
            e["amount"] = round(r["amount"] * (1 + rng.choice([-1, 1]) * rng.uniform(0.0002, 0.00045)), 2)
        elif sc == "duplicate":
            dup = dict(r, int_trade_id=f"INT-{run_date:%Y%m%d}-{n + len(int_extra):06d}", ext_ref=None)
            int_extra.append(dup)
            truth.append(_truth(dup["int_trade_id"], "INT", "duplicate", "DUPLICATE", None, None, run_date))

        if ref_ok and has_ref[i]:
            recs[i]["ext_ref"] = e["ext_trade_id"]
        status = TRUE_STATUS[sc]

        if keep_int[i]:
            truth.append(_truth(r["int_trade_id"], "INT", sc, status, None,
                                e["ext_trade_id"] if keep_ext else None, run_date))
        if keep_ext:
            ext_rows.append(e)
            truth.append(_truth(e["ext_trade_id"], "EXT", sc,
                                "MISSING_INTERNAL" if sc == "missing_internal" else status,
                                r["int_trade_id"] if keep_int[i] else None, None, run_date))

        # schedule late arrivals (only meaningful in multi-day runs)
        if sc in ("missing_external", "missing_internal"):
            lag = _draw_lag(rng, cfg)
            if lag is not None:
                due = add_bd(run_date, lag)
                if due in horizon:
                    if sc == "missing_external":
                        pending.ext_rows.setdefault(due, []).append(
                            (e, _truth(e["ext_trade_id"], "EXT", "late_arrival", "MATCHED", r["int_trade_id"], None, due)))
                    else:
                        late = dict(r, ext_ref=None)
                        pending.int_rows.setdefault(due, []).append(
                            (late, _truth(r["int_trade_id"], "INT", "late_booking", "MATCHED", None, e["ext_trade_id"], due)))

    internal = pd.DataFrame([recs[i] for i in range(n) if keep_int[i]] + int_extra, columns=INT_COLS)
    external = pd.DataFrame(ext_rows)
    for e, t in pending.ext_rows.pop(run_date, []):
        external = pd.concat([external, pd.DataFrame([e])], ignore_index=True)
        truth.append(t)
    for r, t in pending.int_rows.pop(run_date, []):
        internal = pd.concat([internal, pd.DataFrame([r], columns=INT_COLS)], ignore_index=True)
        truth.append(t)

    external = external.sample(frac=1.0, random_state=int(rng.integers(1 << 31))).reset_index(drop=True)
    return {"run_date": run_date, "internal": internal, "external_raw": to_vendor_format(external, rng, bmap),
            "truth": pd.DataFrame(truth)}


def _truth(record_id, side, scenario, status, true_int, true_ext, as_of) -> dict:
    return {"record_id": record_id, "side": side, "scenario": scenario, "true_status": status,
            "true_int_trade_id": true_int, "true_ext_trade_ids": true_ext, "as_of": as_of}


def to_vendor_format(ext: pd.DataFrame, rng, bmap: dict) -> pd.DataFrame:
    """Rename columns and mess up formats the way a real custodian file would."""
    n = len(ext)
    isin = ext["isin"].to_numpy(dtype=object).copy()
    low = rng.random(n) < 0.05
    pad = rng.random(n) < 0.05
    isin[low] = [s.lower() for s in isin[low]]
    isin[pad] = [f"  {s} " for s in isin[pad]]
    ccy = ext["currency"].to_numpy(dtype=object).copy()
    lc = rng.random(n) < 0.03
    ccy[lc] = [c.lower() for c in ccy[lc]]
    ids = ext["ext_trade_id"].to_numpy(dtype=object).copy()
    sp = rng.random(n) < 0.02
    ids[sp] = [f"{x} " for x in ids[sp]]
    return pd.DataFrame({
        "ext_trade_id": ids,
        "security_id": isin,
        "buy_sell": np.where(ext["side"] == "BUY", "B", "S"),
        "qty": ext["quantity"].astype(float),
        "px": ext["price"].astype(float),
        "net_amount": ext["amount"].astype(float),
        "ccy": ccy,
        "trade_dt": [d.strftime("%d/%m/%Y") for d in ext["trade_date"]],
        "value_date": [d.strftime("%d/%m/%Y") for d in ext["settle_date"]],
        "bic": [bmap[c] for c in ext["counterparty"]],
        "acct": ext["account"].to_numpy(),
    })


def write_day(day: dict, out_dir) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    day["internal"].to_parquet(out_dir / "internal_trades.parquet", index=False)
    day["external_raw"].to_parquet(out_dir / "external_statement.parquet", index=False)
    day["truth"].to_parquet(out_dir / "ground_truth.parquet", index=False)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", type=dt.date.fromisoformat, default=dt.date(2026, 11, 20))
    ap.add_argument("--n", type=int, default=50_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--days", type=int, default=1)
    a = ap.parse_args(argv)
    for day in generate_days(a.date, a.n, a.seed, a.days):
        out = DATA / "raw" / f"seed={a.seed}_n={a.n}_days={a.days}" / f"run_date={day['run_date']}"
        write_day(day, out)
        print(f"{day['run_date']}  internal={len(day['internal']):,}  external={len(day['external_raw']):,}  -> {out}")


if __name__ == "__main__":
    main()
