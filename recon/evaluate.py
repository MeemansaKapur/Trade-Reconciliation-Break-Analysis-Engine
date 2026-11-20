"""Evaluation harness: score the engine against the generator's ground truth.

Metrics
  pairing precision  = correct links / links made           (a wrong link hides two problems -> critical)
  pairing recall     = correct links / true links
  auto-match rate    = records MATCHED or TOLERATED / all records
  status accuracy    = records whose final status equals the true status
  false tolerance    = true breaks that ended MATCHED/TOLERATED  (the most dangerous error: a hidden problem)
  false break        = truly reconcilable records left as breaks  (noise for controllers)

Usage:
  python -m recon.evaluate --seeds 11 22 33 44 55 --n 50000 --versions v1.0 v1.1 v1.2 v1.3
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics as st

import pandas as pd

from recon.common import REPORTS, load_rules
from recon.engine import RECONCILED, reconcile
from recon.generate import generate_days
from recon.normalise import normalise_external, normalise_internal

BREAK_TYPES = ["AMOUNT_MISMATCH", "QTY_MISMATCH", "TIMING", "FX_ROUNDING", "MISSING_EXTERNAL",
               "MISSING_INTERNAL", "DUPLICATE"]
STATUSES = ["MATCHED", "TOLERATED"] + BREAK_TYPES


def predicted_links(result) -> set[tuple[str, str]]:
    links = set()
    for df in (result.match_results.rename(columns={"int_trade_id": "i", "ext_trade_ids": "e"}),
               result.breaks[result.breaks.side == "BOTH"].rename(columns={"int_trade_ids": "i", "ext_trade_ids": "e"})):
        for i, es in zip(df["i"], df["e"]):
            for e in str(es).split(","):
                links.add((i, e))
    return links


def score(result, truth: pd.DataFrame) -> dict:
    rs = result.record_status[["record_id", "side", "status"]]
    t = truth.merge(rs, on=["record_id", "side"], how="left", validate="one_to_one")
    assert t["status"].notna().all(), "every truth record must have an engine outcome"
    true_links = {(i, e) for i, e in zip(truth.loc[truth.side == "EXT", "true_int_trade_id"],
                                         truth.loc[truth.side == "EXT", "record_id"]) if isinstance(i, str)}
    pred = predicted_links(result)
    correct = len(pred & true_links)
    true_rec = t["true_status"].isin(RECONCILED)
    pred_rec = t["status"].isin(RECONCILED)
    true_brk = ~true_rec
    pf = t[(t.side == "INT") & (t.scenario == "partial_fill")]
    pf_ok = 0
    if len(pf):
        mr = result.match_results.set_index("int_trade_id")["ext_trade_ids"].to_dict()
        tru = truth.set_index("record_id")["true_ext_trade_ids"].to_dict()
        pf_ok = sum(1 for rid in pf["record_id"] if rid in mr and set(mr[rid].split(",")) == set(tru[rid].split(",")))
    n_int = int((truth.side == "INT").sum())
    return {
        "records": len(t),
        "links_made": len(pred), "true_links": len(true_links), "correct_links": correct,
        "pairing_precision": correct / len(pred) if pred else 1.0,
        "pairing_recall": correct / len(true_links) if true_links else 1.0,
        "wrong_links": len(pred) - correct,
        "auto_match_rate": float(pred_rec.mean()),
        "auto_match_ceiling_rate": float(true_rec.mean()),
        "status_accuracy": float((t["status"] == t["true_status"]).mean()),
        "break_classification_accuracy": float((t.loc[true_brk, "status"] == t.loc[true_brk, "true_status"]).mean()),
        "false_tolerance_count": int((true_brk & pred_rec).sum()),
        "false_tolerance_rate": float((true_brk & pred_rec).sum() / max(1, true_brk.sum())),
        "false_break_count": int((true_rec & ~pred_rec).sum()),
        "false_break_rate": float((true_rec & ~pred_rec).sum() / max(1, true_rec.sum())),
        "partial_fill_match_rate": pf_ok / len(pf) if len(pf) else 1.0,
        "partial_fills": len(pf),
        "break_items": len(result.breaks),
        "break_items_per_1k_trades": 1000 * len(result.breaks) / max(1, n_int),
        "per_class_recall": {s: float((t.loc[t.true_status == s, "status"] == s).mean())
                             for s in STATUSES if (t.true_status == s).any()},
        "confusion": pd.crosstab(t["true_status"], t["status"]).reindex(index=STATUSES, columns=STATUSES,
                                                                         fill_value=0).fillna(0).astype(int).to_dict(),
        "fee_mismatch_hidden": int(((t.scenario == "fee_mismatch") & pred_rec).sum()),
        "fee_mismatch_records": int((t.scenario == "fee_mismatch").sum()),
        "duration_s": result.run_log["duration_s"],
    }


def run_eval(seeds, n, versions, run_date=dt.date(2026, 11, 20)) -> dict:
    out: dict = {v: [] for v in versions}
    for seed in seeds:
        day = generate_days(run_date, n, seed, 1)[0]
        internal, external = normalise_internal(day["internal"]), normalise_external(day["external_raw"])
        for v in versions:
            res = reconcile(internal, external, run_date, load_rules(v))
            s = score(res, day["truth"])
            s["seed"] = seed
            out[v].append(s)
            print(f"seed={seed} {v}: precision={s['pairing_precision']:.4%} recall={s['pairing_recall']:.4%} "
                  f"auto={s['auto_match_rate']:.2%} acc={s['status_accuracy']:.3%} "
                  f"false_tol={s['false_tolerance_count']} breaks={s['break_items']} {s['duration_s']}s")
    return out


def _agg(rows, key):
    vals = [r[key] for r in rows]
    return st.mean(vals), min(vals), max(vals)


def _fmt(key, m, lo, hi):
    pct = key.endswith(("rate", "precision", "recall", "accuracy"))
    if pct:
        p = ".3%" if key.startswith("pairing") else ".2%"
        return f"{m:{p}} ({lo:{p}} – {hi:{p}})" if lo != hi else f"{m:{p}}"
    if key == "duration_s":
        return f"{m:.2f} ({lo:.2f} – {hi:.2f})"
    if isinstance(m, float) and not m.is_integer():
        return f"{m:,.1f} ({lo:,.1f} – {hi:,.1f})"
    return f"{m:,.0f} ({lo:,.0f} – {hi:,.0f})" if lo != hi else f"{m:,.0f}"


def write_report(results: dict, seeds, n, run_date) -> str:
    keys = ["pairing_precision", "pairing_recall", "wrong_links", "auto_match_rate", "auto_match_ceiling_rate",
            "status_accuracy", "break_classification_accuracy", "false_tolerance_count", "false_tolerance_rate",
            "false_break_count", "partial_fill_match_rate", "break_items", "break_items_per_1k_trades", "duration_s"]
    versions = list(results)
    lines = [f"# Evaluation report — {run_date}", "",
             f"Synthetic data, **{n:,} internal trades per run**, seeds {', '.join(map(str, seeds))}. "
             "Each cell: mean across seeds (min – max). Generated by `python -m recon.evaluate`.", "",
             "| Metric | " + " | ".join(versions) + " |", "|---|" + "---|" * len(versions)]
    for k in keys:
        lines.append(f"| {k} | " + " | ".join(_fmt(k, *_agg(results[v], k)) for v in versions) + " |")
    last = versions[-1]
    lines += ["", f"## Per-class recall ({last}, mean across seeds)", "", "| True status | Recall |", "|---|---|"]
    for s in STATUSES:
        vals = [r["per_class_recall"][s] for r in results[last] if s in r["per_class_recall"]]
        if vals:
            lines.append(f"| {s} | {st.mean(vals):.2%} |")
    lines += ["", f"## Confusion matrix ({last}, seed {results[last][0]['seed']}) — rows = truth, columns = engine", ""]
    conf = pd.DataFrame(results[last][0]["confusion"]).reindex(index=STATUSES, columns=STATUSES)
    lines.append("| truth \\ engine | " + " | ".join(conf.columns) + " |")
    lines.append("|---|" + "---|" * len(conf.columns))
    for idx, row in conf.iterrows():
        lines.append(f"| {idx} | " + " | ".join(str(int(x)) for x in row) + " |")
    lines += ["", "## Fee mismatches hidden by tolerance", "", "| Version | hidden / injected |", "|---|---|"]
    for v in versions:
        h = sum(r["fee_mismatch_hidden"] for r in results[v]); t = sum(r["fee_mismatch_records"] for r in results[v])
        lines.append(f"| {v} | {h:,} / {t:,} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, nargs="+", default=[11, 22, 33, 44, 55])
    ap.add_argument("--n", type=int, default=50_000)
    ap.add_argument("--versions", nargs="+", default=["v1.0", "v1.1", "v1.2", "v1.3"])
    ap.add_argument("--date", type=dt.date.fromisoformat, default=dt.date(2026, 11, 20))
    a = ap.parse_args(argv)
    res = run_eval(a.seeds, a.n, a.versions, a.date)
    REPORTS.mkdir(exist_ok=True)
    md = write_report(res, a.seeds, a.n, a.date)
    (REPORTS / f"eval_{a.date}.md").write_text(md)
    (REPORTS / f"eval_{a.date}.json").write_text(json.dumps(res, indent=2, default=str))
    print(md)


if __name__ == "__main__":
    main()
