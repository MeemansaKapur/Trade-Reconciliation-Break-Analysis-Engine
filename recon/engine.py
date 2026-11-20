"""Reconciliation engine: pre-checks -> Pass 1 -> Pass 2 -> Pass 3 -> tiering -> classification -> tie-out.

Usage:
  python -m recon.engine --date 2026-11-20 --n 50000 --seed 42 --version v1.3
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd

from recon import controls
from recon.common import (DATA, ENGINE_VERSION, ROOT, calendar_frame, gen_config, load_rules, stable_hash)
from recon.normalise import normalise_external, normalise_internal

SQL = ROOT / "recon" / "sql"
RECONCILED = ("MATCHED", "TOLERATED")


@dataclass
class RunResult:
    run_id: str
    run_date: dt.date
    rule_version: str
    match_results: pd.DataFrame
    breaks: pd.DataFrame
    record_status: pd.DataFrame
    audit_log: pd.DataFrame
    run_log: dict
    controls: list = field(default_factory=list)


def _sql(name: str) -> str:
    return (SQL / name).read_text()


def _num(x) -> str:
    """Render a config number as a SQL literal (config values are validated as numbers)."""
    return repr(float(x))


def _install_tolerance_macros(con, tol: dict) -> None:
    abs_cases = " ".join(f"WHEN '{c}' THEN {_num(v)}" for c, v in tol["tolerated_abs"].items() if c.isalpha())
    con.execute(f"CREATE OR REPLACE MACRO tol_abs(ccy) AS CASE ccy {abs_cases} ELSE 0.0 END")
    cap = tol.get("tolerated_pct_cap")
    if cap:
        cap_cases = " ".join(f"WHEN '{c}' THEN {_num(v)}" for c, v in cap.items() if c.isalpha())
        con.execute(f"CREATE OR REPLACE MACRO tol_cap(ccy) AS CASE ccy {cap_cases} ELSE 1e18 END")
    else:
        con.execute("CREATE OR REPLACE MACRO tol_cap(ccy) AS 1e18")
    pct = tol.get("tolerated_pct") or 0.0
    con.execute(f"CREATE OR REPLACE MACRO tol_limit(ccy, amt) AS "
                f"GREATEST(tol_abs(ccy), LEAST({_num(pct)} * ABS(amt), tol_cap(ccy)))")


def _load_inputs(con, internal: pd.DataFrame, external: pd.DataFrame) -> None:
    dates = pd.concat([internal["trade_date"], internal["settle_date"], external["trade_date"], external["settle_date"]])
    start = min(dates) - dt.timedelta(days=15)
    end = max(dates) + dt.timedelta(days=40)
    cal = calendar_frame(start, end)
    con.register("cal_df", cal)
    con.register("int_df", internal)
    con.register("ext_df", external)
    con.execute("CREATE OR REPLACE TABLE cal AS SELECT CAST(d AS DATE) AS d, bd_idx FROM cal_df")
    econ = """CAST(x.quantity AS DOUBLE) AS quantity, CAST(x.price AS DOUBLE) AS price,
              CAST(x.amount AS DECIMAL(18,2)) AS amount, x.currency,
              CAST(x.trade_date AS DATE) AS trade_date, CAST(x.settle_date AS DATE) AS settle_date,
              x.counterparty, x.account, c.bd_idx AS settle_bd"""
    con.execute(f"""CREATE OR REPLACE TABLE int_n AS
        SELECT x.int_trade_id, x.ext_ref, x.isin, x.side, {econ}
        FROM int_df x JOIN cal c ON c.d = CAST(x.settle_date AS DATE)""")
    con.execute(f"""CREATE OR REPLACE TABLE ext_n AS
        SELECT x.ext_trade_id, x.isin, x.side, {econ}
        FROM ext_df x JOIN cal c ON c.d = CAST(x.settle_date AS DATE)""")
    con.execute("CREATE OR REPLACE TABLE links (int_trade_id VARCHAR, ext_trade_id VARCHAR, pass VARCHAR, rule_id VARCHAR)")
    con.execute("""CREATE OR REPLACE VIEW int_unmatched AS SELECT * FROM int_n i
                   WHERE NOT EXISTS (SELECT 1 FROM links l WHERE l.int_trade_id = i.int_trade_id)""")
    con.execute("""CREATE OR REPLACE VIEW ext_unmatched AS SELECT * FROM ext_n e
                   WHERE NOT EXISTS (SELECT 1 FROM links l WHERE l.ext_trade_id = e.ext_trade_id)""")


def _add_links(con, df: pd.DataFrame, pass_name: str, rule_id: str) -> int:
    if df.empty:
        return 0
    df = df.assign(**{"pass": pass_name, "rule_id": rule_id})[["int_trade_id", "ext_trade_id", "pass", "rule_id"]]
    con.register("new_links", df)
    con.execute("INSERT INTO links SELECT * FROM new_links")
    con.unregister("new_links")
    return df["int_trade_id"].nunique()


def _subset_search(con, p3: dict) -> tuple[pd.DataFrame, int]:
    """Bounded subset search for partial fills whose key group also contains unrelated fills.
    Exhaustive search is exponential, so: <= max_legs legs per subset, <= max_candidates rows per key.
    Groups above the cap are skipped and counted (reported in run_log)."""
    ints = con.execute("""SELECT int_trade_id, isin, side, currency, counterparty, settle_bd, quantity,
                                 CAST(amount AS DOUBLE) AS amount, tol_limit(currency, amount) AS tol
                          FROM int_unmatched ORDER BY int_trade_id""").df()
    exts = con.execute("""SELECT ext_trade_id, isin, side, currency, counterparty, settle_bd, quantity,
                                 CAST(amount AS DOUBLE) AS amount
                          FROM ext_unmatched ORDER BY ext_trade_id""").df()
    key = ["isin", "side", "currency", "counterparty", "settle_bd"]
    pools = {k: g.to_dict("records") for k, g in exts.groupby(key, sort=False)}
    used: set[str] = set()
    out, skipped = [], 0
    for r in ints.itertuples(index=False):
        pool = [e for e in pools.get(tuple(getattr(r, c) for c in key), [])
                if e["ext_trade_id"] not in used and e["quantity"] < r.quantity]
        if len(pool) < 2:
            continue
        if len(pool) > p3["max_candidates"]:
            skipped += 1
            continue
        best = None
        for size in range(2, min(p3["max_legs"], len(pool)) + 1):
            for combo in itertools.combinations(pool, size):
                if sum(e["quantity"] for e in combo) != r.quantity:
                    continue
                diff = abs(sum(e["amount"] for e in combo) - r.amount)
                if diff <= r.tol + 1e-9:
                    cand = (round(diff, 6), size, tuple(e["ext_trade_id"] for e in combo))
                    best = cand if best is None or cand < best else best
        if best:
            for eid in best[2]:
                used.add(eid)
                out.append({"int_trade_id": r.int_trade_id, "ext_trade_id": eid})
    return pd.DataFrame(out, columns=["int_trade_id", "ext_trade_id"]), skipped


def reconcile(internal: pd.DataFrame, external: pd.DataFrame, run_date: dt.date, rules: dict,
              raise_on_control_failure: bool = True) -> RunResult:
    """Reconcile already-normalised internal and external frames."""
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    tol = rules["tolerances"]
    passes = rules["passes"]
    currencies = {c for c in tol["tolerated_abs"]}
    run_id = f"RUN-{run_date:%Y%m%d}-{rules['version']}-" + stable_hash(
        [len(internal), len(external), float(internal["amount"].sum()), float(external["amount"].sum()),
         rules["version"]], 8)

    pre = controls.pre_checks(internal, external, currencies)
    if raise_on_control_failure:
        controls.assert_all_pass(pre)
    timings["pre_checks"] = time.perf_counter() - t0

    con = duckdb.connect()
    _install_tolerance_macros(con, tol)
    _load_inputs(con, internal, external)
    counts: dict[str, int] = {}

    t = time.perf_counter()
    if passes["P1_REF"]["enabled"]:
        counts["P1_REF"] = _add_links(con, con.execute(_sql("pass1_reference.sql")).df(), "P1", "P1_REF")
    timings["pass1"] = time.perf_counter() - t

    t = time.perf_counter()
    p2 = passes["P2_ECON"]
    counts["P2_ECON"], rounds = 0, 0
    if p2["enabled"]:
        for rounds in range(1, p2["max_rounds"] + 1):
            new = con.execute(_sql("pass2_economic.sql"), {
                "settle_window_bd": int(p2["settle_window_bd"]),
                "max_candidate_diff_pct": float(p2["max_candidate_diff_pct"])}).df()
            added = _add_links(con, new, "P2", "P2_ECON")
            counts["P2_ECON"] += added
            if added == 0:
                break
    counts["P2_rounds"] = rounds
    timings["pass2"] = time.perf_counter() - t

    t = time.perf_counter()
    p3 = passes["P3_GROUP"]
    counts["P3_GROUP"] = counts["P3_SUBSET"] = counts["P3_subset_skipped"] = 0
    if p3["enabled"]:
        counts["P3_GROUP"] = _add_links(con, con.execute(_sql("pass3_group.sql"), {"max_legs": int(p3["max_legs"])}).df(),
                                        "P3", "P3_GROUP")
        if p3.get("subset_search"):
            sub, skipped = _subset_search(con, p3)
            counts["P3_SUBSET"] = _add_links(con, sub, "P3", "P3_SUBSET")
            counts["P3_subset_skipped"] = skipped
    timings["pass3"] = time.perf_counter() - t

    t = time.perf_counter()
    con.execute(_sql("evaluate_pairs.sql"), {
        "matched_abs": float(tol["matched_abs"]), "base_currency": tol["base_currency"],
        "fx_band_pct": float(tol["fx_band_pct"]), "fx_band_min_pct": float(tol.get("fx_band_min_pct") or 0.0)})
    for stmt in _sql("classify_orphans.sql").split(";"):
        if stmt.strip() and not all(l.strip().startswith("--") or not l.strip() for l in stmt.splitlines()):
            if "$diag_settle_window_bd" in stmt:
                con.execute(stmt, {"diag_settle_window_bd": int(rules["classification"]["diag_settle_window_bd"])})
            else:
                con.execute(stmt)
    timings["classify"] = time.perf_counter() - t

    t = time.perf_counter()
    result = _build_outputs(con, run_id, run_date, rules)
    con.close()
    timings["outputs"] = time.perf_counter() - t

    post = controls.post_checks(internal, external, result.record_status, result.match_results,
                                result.breaks, result.audit_log)
    if raise_on_control_failure:
        controls.assert_all_pass(post)
    result.controls = pre + post

    rs = result.record_status
    fx = gen_config()["reporting_fx_to_inr"]
    result.run_log = {
        "run_id": run_id, "run_date": run_date.isoformat(), "rule_version": rules["version"],
        "engine_version": ENGINE_VERSION,
        "internal_in": len(internal), "external_in": len(external),
        "links_by_rule": counts,
        "internal_by_status": rs[rs.side == "INT"]["status"].value_counts().to_dict(),
        "external_by_status": rs[rs.side == "EXT"]["status"].value_counts().to_dict(),
        "matches": int((result.match_results["tier"] == "MATCHED").sum()),
        "tolerated": int((result.match_results["tier"] == "TOLERATED").sum()),
        "breaks": len(result.breaks),
        "breaks_by_type": result.breaks["break_type"].value_counts().to_dict(),
        "break_exposure_inr": round(float(result.breaks["exposure_inr"].sum()), 2),
        "auto_match_rate": round(float(rs["status"].isin(RECONCILED).mean()), 6),
        "control_totals": {"INT": controls.control_totals(internal), "EXT": controls.control_totals(external)},
        "controls_passed": all(r["status"] == "PASS" for r in pre + post),
        "controls_failed": [r["check"] for r in pre + post if r["status"] == "FAIL"],
        "duration_s": round(time.perf_counter() - t0, 3),
        "timings_s": {k: round(v, 3) for k, v in timings.items()},
        "reporting_fx_to_inr": fx,
    }
    return result


def _build_outputs(con, run_id: str, run_date: dt.date, rules: dict) -> RunResult:
    version = rules["version"]
    fx = gen_config()["reporting_fx_to_inr"]
    fx_case = " ".join(f"WHEN '{c}' THEN {_num(v)}" for c, v in fx.items())
    con.execute(f"CREATE OR REPLACE MACRO to_inr(ccy) AS CASE ccy {fx_case} ELSE 1.0 END")
    match_results = con.execute(f"""
        SELECT match_id, int_trade_id, ext_trade_ids, n_legs, pass, rule_id, '{version}' AS rule_version,
               outcome AS tier, CAST(amount_diff AS DOUBLE) AS amount_diff, qty_diff, day_diff,
               currency, CAST(int_amount AS DOUBLE) AS int_amount
        FROM pairs WHERE outcome IN ('MATCHED','TOLERATED') ORDER BY match_id""").df()

    breaks = con.execute(f"""
        WITH paired AS (
          SELECT 'BRK-' || substr(md5(int_trade_id || '|' || ext_trade_ids), 1, 12) AS break_id,
                 'BOTH' AS side, int_trade_id AS int_trade_ids, ext_trade_ids, outcome AS break_type,
                 isin, currency, counterparty, CAST(int_amount AS DOUBLE) AS amount,
                 CAST(amount_diff AS DOUBLE) AS amount_diff, qty_diff, day_diff, pass, rule_id,
                 NULL AS suggested_counterpart, NULL AS duplicate_of
          FROM pairs WHERE outcome NOT IN ('MATCHED','TOLERATED')),
        int_side AS (
          SELECT 'BRK-' || substr(md5('INT|' || o.int_trade_id), 1, 12), 'INT', o.int_trade_id, NULL,
                 CASE WHEN d.int_trade_id IS NOT NULL THEN 'DUPLICATE'
                      WHEN g.break_type IS NOT NULL THEN g.break_type ELSE 'MISSING_EXTERNAL' END,
                 o.isin, o.currency, o.counterparty, CAST(o.amount AS DOUBLE),
                 CAST(g.amount_diff AS DOUBLE), g.qty_diff, g.day_diff, 'CLASSIFY',
                 CASE WHEN d.int_trade_id IS NOT NULL THEN 'CLS_DUPLICATE'
                      WHEN g.rule_id IS NOT NULL THEN g.rule_id ELSE 'CLS_NO_CANDIDATE' END,
                 g.ext_trade_id, d.dup_of
          FROM int_orph o LEFT JOIN dup d USING (int_trade_id) LEFT JOIN diag g USING (int_trade_id)),
        ext_side AS (
          SELECT 'BRK-' || substr(md5('EXT|' || o.ext_trade_id), 1, 12), 'EXT', NULL, o.ext_trade_id,
                 COALESCE(g.break_type, 'MISSING_INTERNAL'),
                 o.isin, o.currency, o.counterparty, CAST(o.amount AS DOUBLE),
                 CAST(-g.amount_diff AS DOUBLE), -g.qty_diff, -g.day_diff, 'CLASSIFY',
                 COALESCE(g.rule_id, 'CLS_NO_CANDIDATE'), g.int_trade_id, NULL
          FROM ext_orph o LEFT JOIN diag g USING (ext_trade_id))
        SELECT b.*, '{version}' AS rule_version, DATE '{run_date.isoformat()}' AS opened_on, 0 AS age_days,
               'OPEN' AS status,
               CASE WHEN b.break_type IN ('AMOUNT_MISMATCH','FX_ROUNDING','QTY_MISMATCH')
                         AND b.amount_diff IS NOT NULL THEN ABS(b.amount_diff)
                    ELSE ABS(b.amount) END AS exposure,
               (CASE WHEN b.break_type IN ('AMOUNT_MISMATCH','FX_ROUNDING','QTY_MISMATCH')
                          AND b.amount_diff IS NOT NULL THEN ABS(b.amount_diff)
                     ELSE ABS(b.amount) END) * to_inr(b.currency) AS exposure_inr
        FROM (SELECT * FROM paired UNION ALL SELECT * FROM int_side UNION ALL SELECT * FROM ext_side) b
        ORDER BY break_id""").df()
    breaks.columns = ["break_id", "side", "int_trade_ids", "ext_trade_ids", "break_type", "isin", "currency",
                      "counterparty", "amount", "amount_diff", "qty_diff", "day_diff", "pass", "rule_id",
                      "suggested_counterpart", "duplicate_of", "rule_version", "opened_on", "age_days", "status",
                      "exposure", "exposure_inr"]
    con.register("breaks_df", breaks)

    record_status = con.execute("""
        WITH int_pairs AS (
          SELECT p.int_trade_id AS record_id, 'INT' AS side,
                 CASE WHEN p.outcome IN ('MATCHED','TOLERATED') THEN p.outcome ELSE p.outcome END AS status,
                 CASE WHEN p.outcome IN ('MATCHED','TOLERATED') THEN p.match_id
                      ELSE 'BRK-' || substr(md5(p.int_trade_id || '|' || p.ext_trade_ids), 1, 12) END AS ref_id,
                 p.pass, p.rule_id
          FROM pairs p),
        ext_pairs AS (
          SELECT l.ext_trade_id, 'EXT', p.outcome,
                 CASE WHEN p.outcome IN ('MATCHED','TOLERATED') THEN p.match_id
                      ELSE 'BRK-' || substr(md5(p.int_trade_id || '|' || p.ext_trade_ids), 1, 12) END,
                 p.pass, p.rule_id
          FROM links l JOIN pairs p USING (int_trade_id)),
        orph AS (
          SELECT int_trade_ids, 'INT', break_type, break_id, pass, rule_id FROM breaks_df WHERE side = 'INT'
          UNION ALL
          SELECT ext_trade_ids, 'EXT', break_type, break_id, pass, rule_id FROM breaks_df WHERE side = 'EXT'),
        allrs AS (SELECT * FROM int_pairs UNION ALL SELECT * FROM ext_pairs UNION ALL SELECT * FROM orph)
        SELECT a.record_id, a.side, a.status, a.ref_id, a.pass, a.rule_id,
               COALESCE(i.currency, e.currency) AS currency,
               CAST(COALESCE(i.amount, e.amount) AS DOUBLE) AS record_amount,
               COALESCE(i.quantity, e.quantity) AS record_quantity,
               md5(concat_ws('|', COALESCE(i.isin, e.isin), COALESCE(i.side, e.side),
                   COALESCE(i.quantity, e.quantity), COALESCE(i.price, e.price), COALESCE(i.amount, e.amount),
                   COALESCE(i.currency, e.currency), COALESCE(i.trade_date, e.trade_date),
                   COALESCE(i.settle_date, e.settle_date), COALESCE(i.counterparty, e.counterparty),
                   COALESCE(i.account, e.account))) AS inputs_hash
        FROM allrs a
        LEFT JOIN int_n i ON a.side = 'INT' AND i.int_trade_id = a.record_id
        LEFT JOIN ext_n e ON a.side = 'EXT' AND e.ext_trade_id = a.record_id
        ORDER BY a.side, a.record_id""").df()

    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    audit_log = record_status[["record_id", "side", "status", "ref_id", "pass", "rule_id", "inputs_hash"]].copy()
    audit_log.insert(0, "run_id", run_id)
    audit_log = audit_log.rename(columns={"status": "decision"})
    audit_log["rule_version"] = version
    audit_log["ts"] = ts
    return RunResult(run_id, run_date, version, match_results, breaks,
                     record_status.drop(columns=["inputs_hash"]), audit_log, {})


def reconcile_files(internal_path: Path, external_path: Path, run_date: dt.date, version: str | None = None) -> RunResult:
    internal = normalise_internal(pd.read_parquet(internal_path))
    external = normalise_external(pd.read_parquet(external_path))
    return reconcile(internal, external, run_date, load_rules(version))


def publish(result: RunResult, out_dir: Path) -> Path:
    """Atomic publish: write everything to a temp dir, then swap it in. Idempotent per (date, version)."""
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=out_dir.parent))
    result.match_results.to_parquet(tmp / "match_results.parquet", index=False)
    result.breaks.to_parquet(tmp / "breaks.parquet", index=False)
    result.record_status.to_parquet(tmp / "record_status.parquet", index=False)
    result.audit_log.to_parquet(tmp / "audit_log.parquet", index=False)
    (tmp / "run_log.json").write_text(json.dumps(result.run_log, indent=2, default=str))
    pd.DataFrame(result.controls).to_csv(tmp / "controls.csv", index=False)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    os.replace(tmp, out_dir)
    return out_dir


def ensure_raw(run_date: dt.date, n: int, seed: int) -> Path:
    from recon.generate import generate_days, write_day
    d = DATA / "raw" / f"seed={seed}_n={n}_days=1" / f"run_date={run_date}"
    if not (d / "internal_trades.parquet").exists():
        write_day(generate_days(run_date, n, seed, 1)[0], d)
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", type=dt.date.fromisoformat, default=dt.date(2026, 11, 20))
    ap.add_argument("--n", type=int, default=50_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--version", default=None, help="rule version (default: current in rules.yaml)")
    a = ap.parse_args(argv)
    raw = ensure_raw(a.date, a.n, a.seed)
    try:
        res = reconcile_files(raw / "internal_trades.parquet", raw / "external_statement.parquet", a.date, a.version)
    except controls.ControlFailure as e:
        print(f"RUN FAILED - nothing published. {e}")
        raise SystemExit(2)
    out = publish(res, DATA / "output" / f"seed={a.seed}_n={a.n}" / f"version={res.rule_version}" / f"run_date={a.date}")
    rl = res.run_log
    print(f"{rl['run_id']}  in: {rl['internal_in']:,} INT / {rl['external_in']:,} EXT  "
          f"auto-match {rl['auto_match_rate']:.2%}  breaks {rl['breaks']:,}  controls {'PASS' if rl['controls_passed'] else 'FAIL'}  "
          f"{rl['duration_s']}s\n  links: {rl['links_by_rule']}\n  breaks: {rl['breaks_by_type']}\n  -> {out}")


if __name__ == "__main__":
    main()
