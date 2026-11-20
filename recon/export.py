"""Export a star schema for Power BI / Tableau, and a formatted Excel break report for controllers.

Usage:
  python -m recon.export --workflow-dir "data/output/workflow_seed=42_n=50000_days=15_v1.3"
Outputs:
  dashboard/data/*.csv        fact + dimension tables (load these into Power BI / Tableau)
  dashboard/break_report.xlsx  Excel report: summary, open breaks (filterable), ageing by type
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from recon.common import REPORTS, ROOT, load_yaml
from recon.workflow import age_bucket

DASH = ROOT / "dashboard"


def build_star(wf_dir: Path) -> dict[str, pd.DataFrame]:
    reg = pd.read_parquet(wf_dir / "break_register.parquet")
    daily = pd.read_parquet(wf_dir / "daily_snapshot.parquet")
    open_daily = pd.read_parquet(wf_dir / "open_breaks_daily.parquet")
    tax = load_yaml("break_taxonomy.yaml")
    cmap = load_yaml("counterparty_map.yaml")

    reg["age_bucket"] = reg["age_days"].map(age_bucket)
    reg["opened_date_key"] = pd.to_datetime(reg["opened_on"]).dt.strftime("%Y%m%d").astype(int)
    reg["resolved_date_key"] = pd.to_datetime(reg["resolved_on"]).dt.strftime("%Y%m%d").astype("Int64")
    fact_break = reg.drop(columns=["opened_on", "resolved_on", "proposed_on"])
    open_daily["date_key"] = pd.to_datetime(open_daily["snapshot_date"]).dt.strftime("%Y%m%d").astype(int)
    open_daily["age_bucket"] = open_daily["age_days"].map(age_bucket)
    daily["date_key"] = pd.to_datetime(daily["date"]).dt.strftime("%Y%m%d").astype(int)

    dates = pd.to_datetime(sorted(set(daily["date"]) | set(reg["opened_on"])))
    dim_date = pd.DataFrame({"date_key": dates.strftime("%Y%m%d").astype(int), "date": dates.date,
                             "weekday": dates.day_name(), "month": dates.strftime("%Y-%m")})
    dim_cpty = pd.DataFrame([{"counterparty": k, "custodian_code": v} for k, v in cmap.items()])
    dim_type = pd.DataFrame([{"break_type": k, "priority": v["priority"], "severity": v["severity"], "owner": v["owner"],
                              "condition": v["condition"], "action": v["action"]} for k, v in tax.items()])

    quality = []
    ev = sorted(REPORTS.glob("eval_*.json"))
    if ev:
        for version, rows in json.loads(ev[-1].read_text()).items():
            for r in rows:
                quality.append({"rule_version": version, "seed": r["seed"],
                                **{k: r[k] for k in ["pairing_precision", "pairing_recall", "auto_match_rate",
                                                     "status_accuracy", "false_tolerance_rate", "false_tolerance_count",
                                                     "false_break_count", "partial_fill_match_rate", "break_items",
                                                     "duration_s"]}})
    return {"fact_break": fact_break, "fact_open_break_daily": open_daily, "fact_daily": daily,
            "fact_quality": pd.DataFrame(quality), "dim_date": dim_date, "dim_counterparty": dim_cpty,
            "dim_break_type": dim_type}


def write_excel(tables: dict, path: Path) -> None:
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    reg = tables["fact_break"]
    open_ = reg[reg.status != "RESOLVED"].sort_values(["escalated", "age_days", "exposure_inr"], ascending=False)
    last = tables["fact_daily"].iloc[-1]
    summary = pd.DataFrame([
        ("Business date", str(last["date"])), ("Records reconciled today", int(last["records_in"])),
        ("Auto-match rate", float(last["auto_match_rate"])), ("Open breaks", int(last["open"])),
        ("New today", int(last["new"])), ("Resolved today", int(last["resolved"])),
        ("Escalated (age ≥ threshold)", int(last["escalated"])), ("Open exposure (INR)", float(last["open_exposure_inr"])),
        ("Controls", "PASS" if last["controls_passed"] else "FAIL")], columns=["Metric", "Value"])
    ageing = pd.crosstab(open_["break_type"], open_["age_bucket"]).reindex(columns=["0-1", "2-5", "6-10", "10+"], fill_value=0)
    cols = ["break_id", "break_type", "side", "currency", "counterparty", "isin", "amount", "amount_diff",
            "exposure_inr", "age_days", "escalated", "status", "assigned_to", "int_trade_ids", "ext_trade_ids"]
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="Summary", index=False)
        open_[cols].to_excel(xw, sheet_name="Open breaks", index=False)
        ageing.to_excel(xw, sheet_name="Ageing by type")
        tables["fact_daily"].drop(columns=["date_key"]).to_excel(xw, sheet_name="Daily trend", index=False)
        hdr = PatternFill("solid", fgColor="1F3864")
        for ws in xw.book.worksheets:
            for c in ws[1]:
                c.font, c.fill, c.alignment = Font(bold=True, color="FFFFFF"), hdr, Alignment(horizontal="center")
            ws.freeze_panes = "A2"
            for i, col in enumerate(ws.columns, 1):
                width = max(len(str(c.value or "")) for c in list(col)[:200])
                ws.column_dimensions[get_column_letter(i)].width = min(45, max(10, width + 2))
        ws = xw.book["Open breaks"]
        ws.auto_filter.ref = ws.dimensions
        n = ws.max_row
        for col in ("G", "H", "I"):
            for c in ws[f"{col}2:{col}{n}"]:
                c[0].number_format = "#,##0.00"
        ws.conditional_formatting.add(f"J2:J{n}", CellIsRule(operator="greaterThanOrEqual", formula=["5"],
                                                              fill=PatternFill("solid", fgColor="F8CBAD")))
        s = xw.book["Summary"]
        s["B4"].number_format = "0.00%"
        s["B9"].number_format = "#,##0"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workflow-dir", type=Path, required=True)
    a = ap.parse_args(argv)
    tables = build_star(a.workflow_dir)
    (DASH / "data").mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(DASH / "data" / f"{name}.csv", index=False)
        print(f"dashboard/data/{name}.csv  {len(df):,} rows")
    write_excel(tables, DASH / "break_report.xlsx")
    print("dashboard/break_report.xlsx")


if __name__ == "__main__":
    main()
