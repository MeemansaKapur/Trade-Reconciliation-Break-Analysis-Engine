"""Multi-day operation: carry-forward, break ageing, workflow states and maker-checker.

Each business day:
  1. today's new records + yesterday's still-unpaired records (carried forward) are reconciled together,
     so a late confirmation or late booking automatically clears the earlier break (LATE_ARRIVAL_MATCHED)
  2. break identity is stable across days (break_id = hash of the record IDs), so a carried break keeps
     its opened_on date and ages; its original classification is kept until it pairs
  3. open breaks move OPEN -> INVESTIGATING -> PROPOSED (maker) -> RESOLVED (checker, a different user)
  4. age is counted in business days; breaks at/over the escalation threshold are flagged

Controller behaviour (who picks up what, when) is SIMULATED with a seeded RNG and labelled as such.

Usage:
  python -m recon.workflow --start 2026-11-20 --days 15 --n 50000 --seed 42 --version v1.3
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

from recon.common import DATA, REPORTS, bd_index, load_rules
from recon.engine import RECONCILED, publish, reconcile
from recon.explain import daily_summary, summary_stats
from recon.generate import generate_days
from recon.normalise import normalise_external, normalise_internal

STATES = ["OPEN", "INVESTIGATING", "PROPOSED", "RESOLVED"]
RESOLUTION_BY_TYPE = {
    "AMOUNT_MISMATCH": "BOOKING_CORRECTED", "QTY_MISMATCH": "BOOKING_CORRECTED",
    "TIMING": "VALUE_DATE_AMENDED", "FX_ROUNDING": "WRITE_OFF_WITHIN_POLICY",
    "DUPLICATE": "DUPLICATE_CANCELLED", "MISSING_EXTERNAL": "CONFIRMED_WITH_CUSTODIAN",
    "MISSING_INTERNAL": "BOOKED_LATE",
}
# Simulated daily probability that an INVESTIGATING break gets a proposed resolution
PROPOSE_P = {"AMOUNT_MISMATCH": 0.35, "QTY_MISMATCH": 0.35, "TIMING": 0.5, "FX_ROUNDING": 0.6,
             "DUPLICATE": 0.7, "MISSING_EXTERNAL": 0.25, "MISSING_INTERNAL": 0.25}
WAIT_FOR_LATE_ARRIVAL_BD = 3  # controllers chase missing records only after this many days
CONTROLLERS = ["controller_a", "controller_b", "controller_c"]
AGE_BUCKETS = [(0, 1, "0-1"), (2, 5, "2-5"), (6, 10, "6-10"), (11, 10 ** 6, "10+")]
REGISTER_COLS = ["break_id", "break_type", "side", "int_trade_ids", "ext_trade_ids", "isin", "currency",
                 "counterparty", "amount", "amount_diff", "exposure_inr", "rule_version", "opened_on", "status",
                 "assigned_to", "proposed_by", "proposed_on", "proposed_reason", "approved_by", "resolved_on",
                 "resolution_reason", "age_days", "escalated"]


class MakerCheckerViolation(PermissionError):
    pass


class InvalidTransition(ValueError):
    pass


def age_bucket(age: int) -> str:
    return next(lbl for lo, hi, lbl in AGE_BUCKETS if lo <= age <= hi)


# ----------------------------------------------------------------------------- state transitions
# Each transition accepts one break_id or a list (batched for speed) and validates every row first.
def _ids(reg: pd.DataFrame, break_ids, expected: str, target: str) -> list[str]:
    ids = [break_ids] if isinstance(break_ids, str) else list(break_ids)
    missing = [i for i in ids if i not in reg.index]
    if missing:
        raise KeyError(missing[:5])
    bad = reg.loc[ids, "status"] != expected
    if bad.any():
        raise InvalidTransition(f"{bad.sum()} break(s) not {expected}; cannot move to {target}: {list(bad[bad].index[:5])}")
    return ids


def assign(reg, break_ids, users, on):
    ids = _ids(reg, break_ids, "OPEN", "INVESTIGATING")
    reg.loc[ids, "status"] = "INVESTIGATING"
    reg.loc[ids, "assigned_to"] = users


def propose(reg, break_ids, makers, reasons, on):
    ids = _ids(reg, break_ids, "INVESTIGATING", "PROPOSED")
    reg.loc[ids, "status"] = "PROPOSED"
    reg.loc[ids, "proposed_by"] = makers
    reg.loc[ids, "proposed_on"] = on
    reg.loc[ids, "proposed_reason"] = reasons


def approve(reg, break_ids, checkers, on):
    """Checker approval. Maker-checker: nobody may approve their own proposal."""
    ids = _ids(reg, break_ids, "PROPOSED", "RESOLVED")
    checkers = pd.Series(checkers if not isinstance(checkers, str) else [checkers] * len(ids), index=ids)
    same = checkers == reg.loc[ids, "proposed_by"]
    if same.any():
        raise MakerCheckerViolation(f"self-approval attempted on {list(same[same].index[:5])}")
    reg.loc[ids, "status"] = "RESOLVED"
    reg.loc[ids, "approved_by"] = checkers
    reg.loc[ids, "resolved_on"] = on
    reg.loc[ids, "resolution_reason"] = reg.loc[ids, "proposed_reason"]


def auto_resolve(reg, break_ids, on, reasons):
    """System resolution (e.g. late arrival matched by the engine). Audited as ENGINE, not a person."""
    ids = [break_ids] if isinstance(break_ids, str) else list(break_ids)
    if not ids:
        return
    reg.loc[ids, "status"] = "RESOLVED"
    reg.loc[ids, "resolved_on"] = on
    reg.loc[ids, "resolution_reason"] = reasons
    reg.loc[ids, "approved_by"] = "ENGINE"


def refresh_ages(reg: pd.DataFrame, today: dt.date, threshold: int) -> None:
    end = reg["resolved_on"].where(reg["resolved_on"].notna(), today)
    reg["age_days"] = [bd_index(e) - bd_index(o) for o, e in zip(reg["opened_on"], end)]
    reg["escalated"] = (reg["status"] != "RESOLVED") & (reg["age_days"] >= threshold)


# ----------------------------------------------------------------------------- daily loop
def simulate_controllers(reg: pd.DataFrame, today: dt.date, rng) -> None:
    # 1) checkers approve proposals made on an earlier day (always a different user from the maker)
    prop = reg.loc[(reg.status == "PROPOSED") & (reg.proposed_on < today), "proposed_by"]
    if len(prop):
        checkers = [rng.choice([c for c in CONTROLLERS if c != m]) for m in prop]
        approve(reg, prop.index, checkers, today)
    # 2) makers propose resolutions on breaks they are investigating
    inv = reg.loc[reg.status == "INVESTIGATING", ["break_type", "opened_on", "assigned_to"]]
    if len(inv):
        age = np.array([bd_index(today) - bd_index(o) for o in inv["opened_on"]])
        waiting = inv["break_type"].str.startswith("MISSING").to_numpy() & (age < WAIT_FOR_LATE_ARRIVAL_BD)
        go = (rng.random(len(inv)) < inv["break_type"].map(PROPOSE_P).to_numpy()) & ~waiting
        sel = inv[go]
        if len(sel):
            propose(reg, sel.index, sel["assigned_to"].to_numpy(), sel["break_type"].map(RESOLUTION_BY_TYPE).to_numpy(), today)
    # 3) new breaks get picked up
    opened = reg.index[reg.status == "OPEN"]
    if len(opened):
        take = opened[rng.random(len(opened)) < 0.6]
        if len(take):
            assign(reg, take, rng.choice(CONTROLLERS, len(take)), today)


def run_workflow(start: dt.date, days: int, n: int, seed: int, version: str | None = None,
                 out_root: Path | None = None) -> dict:
    rules = load_rules(version)
    threshold = rules["escalation_age_bd"]
    rng = np.random.default_rng(seed + 1000)
    default_out = out_root is None
    out_root = out_root or DATA / "output" / f"workflow_seed={seed}_n={n}_days={days}_{rules['version']}"
    generated = generate_days(start, n, seed, days)
    reg = pd.DataFrame(columns=REGISTER_COLS).set_index("break_id", drop=False)
    carried_int = pd.DataFrame()
    carried_ext = pd.DataFrame()
    snapshots, daily_open, runlogs, summaries, prev_stats = [], [], [], [], None
    late_truth, late_hits = 0, 0

    for day in generated:
        today = day["run_date"]
        new_int, new_ext = normalise_internal(day["internal"]), normalise_external(day["external_raw"])
        I = pd.concat([new_int, carried_int], ignore_index=True) if len(carried_int) else new_int
        E = pd.concat([new_ext, carried_ext], ignore_index=True) if len(carried_ext) else new_ext
        res = reconcile(I, E, today, rules)
        publish(res, out_root / "runs" / f"run_date={today}")
        runlogs.append(res.run_log)
        rs = res.record_status.set_index(["record_id", "side"])
        today_ids = set(res.breaks["break_id"])

        # 1) carried orphans: did they pair today?
        carried_in = len(carried_int) + len(carried_ext)
        open_orphans = reg[(reg.status != "RESOLVED") & (reg.side != "BOTH")]
        # a carried break still present today continues (same id, original type, keeps ageing);
        # one that is gone was paired by today's run -> resolved by the engine
        paired = open_orphans[~open_orphans["break_id"].isin(today_ids)]
        if len(paired):
            keys = list(zip(paired["int_trade_ids"].where(paired.side == "INT", paired["ext_trade_ids"]), paired["side"]))
            st = rs.loc[keys, "status"].to_numpy()
            auto_resolve(reg, paired.index, today,
                         np.where(np.isin(st, RECONCILED), "LATE_ARRIVAL_MATCHED", "REPAIRED_INTO_PAIRED_BREAK"))

        # late-arrival accuracy vs ground truth
        t = day["truth"]
        lt = t[t.scenario.isin(["late_arrival", "late_booking"])]
        late_truth += len(lt)
        if len(lt):
            late_hits += int(np.isin(rs.loc[list(zip(lt.record_id, lt.side)), "status"].to_numpy(), RECONCILED).sum())

        # 2) new breaks
        known = set(reg["break_id"])
        nb = res.breaks[~res.breaks["break_id"].isin(known)].copy()
        nb["opened_on"], nb["status"] = today, "OPEN"
        for c in REGISTER_COLS:
            if c not in nb.columns:
                nb[c] = None
        nb = nb[REGISTER_COLS].set_index("break_id", drop=False)
        reg = pd.concat([reg, nb]) if len(reg) else nb

        # 3) controllers work the queue (simulated)
        simulate_controllers(reg, today, rng)
        refresh_ages(reg, today, threshold)

        # 4) carry forward records of still-open orphan breaks
        open_or = reg[(reg.status != "RESOLVED") & (reg.side != "BOTH")]
        ci = set(open_or.loc[open_or.side == "INT", "int_trade_ids"])
        ce = set(open_or.loc[open_or.side == "EXT", "ext_trade_ids"])
        carried_int = I[I["int_trade_id"].isin(ci)].copy()
        carried_ext = E[E["ext_trade_id"].isin(ce)].copy()

        # 5) snapshot + summary
        o = reg[reg.status != "RESOLVED"]
        snap = {"date": today, "open": len(o), "new": len(nb),
                "resolved": int((reg.resolved_on == today).sum()),
                "resolved_by_engine": int(((reg.resolved_on == today) & (reg.approved_by == "ENGINE")).sum()),
                "escalated": int(o["escalated"].sum()), "open_exposure_inr": float(o["exposure_inr"].sum()),
                "auto_match_rate": res.run_log["auto_match_rate"], "records_in": len(I) + len(E),
                "carried_in": carried_in, "controls_passed": res.run_log["controls_passed"]}
        for _, _, lbl in AGE_BUCKETS:
            snap[f"age_{lbl}"] = int((o["age_days"].map(age_bucket) == lbl).sum())
        snapshots.append(snap)
        daily_open.append(o.assign(snapshot_date=today)[["snapshot_date", "break_id", "break_type", "status",
                                                          "age_days", "escalated", "exposure_inr", "currency",
                                                          "counterparty"]])
        stats = summary_stats(res.breaks, reg, today, prev_stats)
        text, src = daily_summary(stats)
        summaries.append(f"## {today}\n\n{text}\n\n_source: {src}_\n")
        prev_stats = stats
        print(f"{today}: in={len(I) + len(E):,} carried={snap['carried_in']:,} new_breaks={snap['new']:,} "
              f"resolved={snap['resolved']:,} (engine {snap['resolved_by_engine']:,}) open={snap['open']:,} "
              f"escalated={snap['escalated']:,} {res.run_log['duration_s']}s")

    snap_df = pd.DataFrame(snapshots)
    reg = reg.reset_index(drop=True)
    reg.to_parquet(out_root / "break_register.parquet", index=False)
    snap_df.to_parquet(out_root / "daily_snapshot.parquet", index=False)
    pd.concat(daily_open, ignore_index=True).to_parquet(out_root / "open_breaks_daily.parquet", index=False)
    (out_root / "run_logs.json").write_text(json.dumps(runlogs, indent=2, default=str))
    (out_root / "daily_summaries.md").write_text("# Daily controller summaries\n\n" + "\n".join(summaries))

    resolved = reg[reg.status == "RESOLVED"]
    metrics = {
        "days": days, "trades_per_day": n, "records_processed": int(snap_df["records_in"].sum()),
        "breaks_opened": len(reg), "breaks_resolved": len(resolved),
        "resolved_by_engine": int((resolved.approved_by == "ENGINE").sum()),
        "resolved_by_reason": resolved["resolution_reason"].value_counts().to_dict(),
        "late_arrivals_injected": late_truth, "late_arrivals_auto_matched": late_hits,
        "maker_checker_resolutions": int((resolved.approved_by != "ENGINE").sum()),
        "maker_checker_self_approvals": int(((resolved.approved_by == resolved.proposed_by)).sum()),
        "open_at_end": int((reg.status != "RESOLVED").sum()),
        "escalated_at_end": int(reg["escalated"].sum()),
        "median_age_resolved_bd": float(resolved["age_days"].median()) if len(resolved) else None,
        "controls_passed_all_days": bool(snap_df["controls_passed"].all()),
        "mean_run_seconds": round(float(np.mean([r["duration_s"] for r in runlogs])), 2),
    }
    (out_root / "workflow_metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    report = _report(metrics, snap_df, rules["version"], seed)
    (out_root / "workflow_report.md").write_text(report)
    if default_out:  # only canonical runs update reports/
        REPORTS.mkdir(exist_ok=True)
        (REPORTS / f"workflow_{start}_{days}d.md").write_text(report)
    return {"register": reg, "snapshots": snap_df, "metrics": metrics, "out": out_root}


def _report(m, snap, version, seed) -> str:
    lines = [f"# Multi-day run — {m['days']} business days × {m['trades_per_day']:,} trades/day ({version}, seed {seed})", "",
             "_Controller pick-up and resolution behaviour is simulated; engine matching, carry-forward and ageing are real._", "",
             "| Metric | Value |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" if isinstance(v, int) else f"| {k} | {v} |" for k, v in m.items()]
    lines += ["", "## Daily snapshot", "", "| " + " | ".join(snap.columns) + " |", "|" + "---|" * len(snap.columns)]
    for r in snap.itertuples(index=False):
        lines.append("| " + " | ".join(f"{x:,.0f}" if isinstance(x, float) and x > 1 else
                                        f"{x:.2%}" if isinstance(x, float) else str(x) for x in r) + " |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2026, 11, 20))
    ap.add_argument("--days", type=int, default=15)
    ap.add_argument("--n", type=int, default=50_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--version", default=None)
    a = ap.parse_args(argv)
    r = run_workflow(a.start, a.days, a.n, a.seed, a.version)
    print(json.dumps(r["metrics"], indent=2, default=str))


if __name__ == "__main__":
    main()
