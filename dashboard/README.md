# Dashboard build guide (Power BI / Tableau)

Data: run `python -m recon.export --workflow-dir ...` → `dashboard/data/*.csv` (star schema) and `dashboard/break_report.xlsx` (Excel report for controllers).

> **On a Mac:** Power BI Desktop is Windows-only. Options: (1) **Power BI service in the browser** (app.powerbi.com, sign in with a work/school account, upload the CSVs), (2) a Windows VM / lab machine, or (3) **Tableau Public** (free, runs on macOS). The model and measures below work for both. In Tableau they become calculated fields.

## Star schema

```
                dim_date (date_key)
                   │
dim_counterparty ──┼── fact_break (one row per break: type, status, age, exposure, maker/checker)
(counterparty)     │
                   ├── fact_open_break_daily (one row per open break per day → ageing trend)
dim_break_type ────┘
(break_type: severity, owner, action)

fact_daily   (one row per business day: open / new / resolved / escalated / auto-match / controls)
fact_quality (one row per rule version × seed: precision, recall, false tolerance …)
```
Relationships (single direction, many-to-one): `fact_break[opened_date_key] → dim_date[date_key]`, `fact_break[counterparty] → dim_counterparty[counterparty]`, `fact_break[break_type] → dim_break_type[break_type]`, `fact_open_break_daily[date_key] → dim_date[date_key]`, `fact_daily[date_key] → dim_date[date_key]`.

## Pages (who each is for)

| Page | Audience | Visuals |
|---|---|---|
| 1 · Daily status | Team lead | Cards: records reconciled, auto-match %, open breaks, escalated, controls PASS/FAIL. Bars: open breaks by type; by age bucket (0-1 / 2-5 / 6-10 / 10+) |
| 2 · Investigation | Controller | Table of open breaks (slicers: type, counterparty, currency, age bucket); top counterparties by count and exposure; drill-through to a break with its explanation |
| 3 · Trends | Team lead | Line: open breaks per day; columns: new vs resolved per day; stacked area: age buckets over time |
| 4 · Quality | Change manager | Precision / recall / false-tolerance rate by rule version (from `fact_quality`) |

Every number ties back to `run_log.json` / `reports/eval_*.md`. Put screenshots in `dashboard/screenshots/` and link them from the main README.

## DAX measures
See [measures.dax](measures.dax).
