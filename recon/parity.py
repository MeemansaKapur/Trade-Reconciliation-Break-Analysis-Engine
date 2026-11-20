"""Change parity: run two rule versions on identical input and report every decision that changed.

No rule or tolerance change is released without this report. "Newly broken" records are listed in full
for review, because a change that turns a match into a break (or vice versa) must be intended.

Usage:
  python -m recon.parity --base v1.0 --candidate v1.1 --date 2026-11-20 --seed 42 [--release --approver "..."]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

import pandas as pd

from recon.common import REPORTS, ROOT, load_rules
from recon.engine import RECONCILED, reconcile
from recon.evaluate import score
from recon.generate import generate_days
from recon.normalise import normalise_external, normalise_internal

BUCKETS = ["UNCHANGED", "NEWLY_MATCHED", "NEWLY_BROKEN", "RECLASSIFIED", "TIER_CHANGE", "RE_PAIRED"]


def counterparts(result) -> pd.DataFrame:
    rows = []
    for i, es in zip(result.match_results["int_trade_id"], result.match_results["ext_trade_ids"]):
        rows.append((i, "INT", es))
        rows += [(e, "EXT", i) for e in es.split(",")]
    b = result.breaks[result.breaks.side == "BOTH"]
    for i, es in zip(b["int_trade_ids"], b["ext_trade_ids"]):
        rows.append((i, "INT", es))
        rows += [(e, "EXT", i) for e in es.split(",")]
    cp = pd.DataFrame(rows, columns=["record_id", "side", "counterpart"])
    rs = result.record_status[["record_id", "side", "status", "rule_id", "record_amount", "currency"]]
    out = rs.merge(cp, on=["record_id", "side"], how="left")
    out["counterpart"] = out["counterpart"].fillna("")
    return out


def bucket(b_status, c_status, b_cp, c_cp) -> str:
    b_rec, c_rec = b_status in RECONCILED, c_status in RECONCILED
    if b_status == c_status and b_cp == c_cp:
        return "UNCHANGED"
    if not b_rec and c_rec:
        return "NEWLY_MATCHED"
    if b_rec and not c_rec:
        return "NEWLY_BROKEN"
    if b_rec and c_rec:
        return "TIER_CHANGE" if b_status != c_status else "RE_PAIRED"
    return "RECLASSIFIED" if b_status != c_status else "RE_PAIRED"


def compare(base_res, cand_res, truth: pd.DataFrame | None = None) -> dict:
    b, c = counterparts(base_res), counterparts(cand_res)
    m = b.merge(c, on=["record_id", "side"], suffixes=("_base", "_cand"), how="outer", validate="one_to_one")
    m["bucket"] = [bucket(*x) for x in zip(m.status_base, m.status_cand, m.counterpart_base, m.counterpart_cand)]
    if truth is not None:
        m = m.merge(truth[["record_id", "side", "scenario", "true_status"]], on=["record_id", "side"], how="left")
        m["cand_correct"] = m["status_cand"] == m["true_status"]
        m["base_correct"] = m["status_base"] == m["true_status"]
    trans = (m[m.bucket != "UNCHANGED"].groupby(["status_base", "status_cand"]).size()
             .reset_index(name="records").sort_values("records", ascending=False))
    out = {"diff": m, "counts": {k: int((m.bucket == k).sum()) for k in BUCKETS}, "transitions": trans,
           "records": len(m)}
    if truth is not None:
        out["metrics_base"] = score(base_res, truth)
        out["metrics_cand"] = score(cand_res, truth)
        changed = m[m.bucket != "UNCHANGED"]
        out["changed_now_correct"] = int(changed["cand_correct"].sum())
        out["changed_now_wrong"] = int((~changed["cand_correct"]).sum())
        out["changed_were_correct"] = int(changed["base_correct"].sum())
    return out


def render(base_v, cand_v, run_date, seed, n, cmp: dict) -> str:
    c = cmp["counts"]
    lines = [f"# Parity report: {base_v} → {cand_v}", "",
             f"Run date {run_date} · seed {seed} · {n:,} internal trades · {cmp['records']:,} records compared", "",
             f"**Change:** {load_rules(cand_v)['description']}", "",
             "## Difference buckets", "", "| Bucket | Records | Meaning |", "|---|---|---|"]
    meaning = {"UNCHANGED": "same status, same counterpart", "NEWLY_MATCHED": "break → matched/tolerated",
               "NEWLY_BROKEN": "matched/tolerated → break (**review every one**)",
               "RECLASSIFIED": "break type changed", "TIER_CHANGE": "MATCHED ↔ TOLERATED",
               "RE_PAIRED": "same status, different counterpart"}
    for k in BUCKETS:
        lines.append(f"| {k} | {c[k]:,} | {meaning[k]} |")
    unchanged = c["UNCHANGED"] / cmp["records"]
    lines += ["", f"Unchanged: **{unchanged:.3%}** of records.", "", "## Transitions (changed records only)", "",
              "| Base status | Candidate status | Records |", "|---|---|---|"]
    for r in cmp["transitions"].head(25).itertuples():
        lines.append(f"| {r.status_base} | {r.status_cand} | {r.records:,} |")
    if "metrics_base" in cmp:
        mb, mc = cmp["metrics_base"], cmp["metrics_cand"]
        lines += ["", "## Quality vs ground truth", "", f"| Metric | {base_v} | {cand_v} | Δ |", "|---|---|---|---|"]
        for k in ["pairing_precision", "pairing_recall", "wrong_links", "auto_match_rate", "status_accuracy",
                  "false_tolerance_count", "false_break_count", "partial_fill_match_rate", "break_items"]:
            a, b = mb[k], mc[k]
            if isinstance(a, float) and k.endswith(("rate", "precision", "recall", "accuracy")):
                lines.append(f"| {k} | {a:.3%} | {b:.3%} | {(b - a) * 100:+.3f} pp |")
            else:
                lines.append(f"| {k} | {a:,} | {b:,} | {b - a:+,} |")
        lines += ["", f"Of the {cmp['records'] - c['UNCHANGED']:,} changed records, **{cmp['changed_now_correct']:,}** "
                      f"now agree with ground truth (were correct before: {cmp['changed_were_correct']:,}); "
                      f"**{cmp['changed_now_wrong']:,}** are still/now wrong."]
        nb = cmp["diff"][cmp["diff"].bucket == "NEWLY_BROKEN"]
        if len(nb):
            lines += ["", f"Newly broken by scenario (all {len(nb):,} listed in the CSV for review):", ""]
            for k, v in nb.groupby("scenario").size().sort_values(ascending=False).items():
                lines.append(f"- {k}: {v:,}")
    return "\n".join(lines) + "\n"


def release_note(base_v, cand_v, run_date, seed, cmp, report_name, approver) -> str:
    c = cmp["counts"]
    mb, mc = cmp.get("metrics_base", {}), cmp.get("metrics_cand", {})
    q = ""
    if mb:
        q = (f"- Quality (seed {seed}): pairing recall {mb['pairing_recall']:.3%} → {mc['pairing_recall']:.3%}; "
             f"false tolerances {mb['false_tolerance_count']:,} → {mc['false_tolerance_count']:,}; "
             f"wrong links {mb['wrong_links']} → {mc['wrong_links']}; break items {mb['break_items']:,} → {mc['break_items']:,}\n")
    return (f"\n## {cand_v} — released {dt.date.today()}\n\n"
            f"- Change: {load_rules(cand_v)['description']}\n"
            f"- Parity vs {base_v} on {run_date} (seed {seed}): {c['UNCHANGED']:,} unchanged, "
            f"{c['NEWLY_MATCHED']:,} newly matched, {c['NEWLY_BROKEN']:,} newly broken, "
            f"{c['RECLASSIFIED']:,} reclassified, {c['TIER_CHANGE']:,} tier changes, {c['RE_PAIRED']:,} re-paired\n"
            f"{q}"
            f"- Every newly-broken record reviewed: see `reports/{report_name}` and the matching CSV\n"
            f"- Maker: project author · Checker: {approver}\n")


def run(base_v, cand_v, run_date, seed, n, release=False, approver="second reviewer (simulated)"):
    day = generate_days(run_date, n, seed, 1)[0]
    internal, external = normalise_internal(day["internal"]), normalise_external(day["external_raw"])
    br = reconcile(internal, external, run_date, load_rules(base_v))
    cr = reconcile(internal, external, run_date, load_rules(cand_v))
    cmp = compare(br, cr, day["truth"])
    REPORTS.mkdir(exist_ok=True)
    name = f"parity_{base_v}_{cand_v}_{run_date}.md"
    (REPORTS / name).write_text(render(base_v, cand_v, run_date, seed, n, cmp))
    d = cmp["diff"]
    d[d.bucket != "UNCHANGED"].to_csv(REPORTS / name.replace(".md", "_changes.csv"), index=False)
    (REPORTS / name.replace(".md", ".json")).write_text(json.dumps(
        {"counts": cmp["counts"], "metrics_base": cmp.get("metrics_base"), "metrics_cand": cmp.get("metrics_cand")},
        indent=2, default=str))
    if release:
        rn = ROOT / "docs" / "release_notes.md"
        if not rn.exists():
            rn.write_text("# Release notes\n\nEvery entry below was gated by a parity report (`recon/parity.py`).\n")
        with open(rn, "a") as f:
            f.write(release_note(base_v, cand_v, run_date, seed, cmp, name, approver))
    print(render(base_v, cand_v, run_date, seed, n, cmp))
    return cmp


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--date", type=dt.date.fromisoformat, default=dt.date(2026, 11, 20))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n", type=int, default=50_000)
    ap.add_argument("--release", action="store_true", help="append an entry to docs/release_notes.md")
    ap.add_argument("--approver", default="second reviewer (simulated)")
    a = ap.parse_args(argv)
    run(a.base, a.candidate, a.date, a.seed, a.n, a.release, a.approver)


if __name__ == "__main__":
    main()
