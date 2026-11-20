# Trade Reconciliation & Break Analysis Engine

A reconciliation engine that compares an **internal trade book** against a **custodian statement**. It matches what agrees in three deterministic passes, classifies every break by root cause, proves completeness with control tie-outs, ages and routes breaks through a maker-checker workflow, and uses an LLM to *explain* breaks in plain English. The LLM never decides what matches.

> All data is **synthetic**, generated with ground-truth labels so that match quality is *measured* (precision / recall), not claimed. No real trades, clients or counterparties.

## Results (measured)

Source: [`reports/eval_2026-11-20.md`](reports/eval_2026-11-20.md) — 5 seeds × 50,000 trades/day, mean (min–max), current rules **v1.3**.

| Metric | Value |
|---|---|
| Pairing precision (correct links / links made) | **99.990%** (99.982 – 99.996%) |
| Pairing recall (correct links / true links) | **99.72%** (99.67 – 99.75%) |
| Auto-match rate (matched + tolerated) | **95.55%**, vs 95.63% theoretical ceiling |
| Record status accuracy | **99.86%** |
| Break classification accuracy (7 types) | **98.70%** |
| False tolerances (real breaks hidden as "tolerated") | **0** (v1.0: 238 per run, 5.4%) |
| Partial fills matched (one-to-many) | **95.8%** (v1.0: 61.7%) |
| False breaks (reconcilable records left as breaks) | **85 per run** (v1.0: 752, −88.6%) |
| Runtime, 50K trades (100K records) | **1.4 s** |
| Runtime, 1M trades (2.0M records) | **30.5 s**, ≈ 66K records/s, linear ([benchmark](reports/benchmark.md)) |

**15-business-day operation** ([`reports/workflow_2026-11-20_15d.md`](reports/workflow_2026-11-20_15d.md)): 1.56M records reconciled, 41,462 breaks opened, **7,940 of 8,005 (99.2%) late-arriving records auto-matched** against carried breaks with no manual action, 23,049 maker-checker resolutions with **0 self-approvals**, and controls passed on **15/15 days**.

**Controlled change:** 3 rule releases, each gated by a parity report against the previous version ([release notes](docs/release_notes.md)):

| Release | Change | Parity result |
|---|---|---|
| v1.1 | Settle-date window ±1 → ±2 business days | 94 records re-paired, 0 newly broken, 0 new wrong links, break items −47 |
| v1.2 | Bounded subset search for partial fills | 679 newly matched, all confirmed correct by ground truth; partial-fill match 61.7% → 97.5% |
| v1.3 | Cap the relative tolerance (fee handling) | 230 newly broken, **all genuine fee breaks** that v1.2 had hidden; false tolerances → 0 |

## Design principles
1. **Deterministic matching, explainable output.** Every match comes from a named, versioned rule (`rule_id`, `rule_version` on every row). The LLM only explains.
2. **Every record is accounted for.** Records in = matched + tolerated + breaks, per side and per currency, by count and amount. Otherwise the run fails and publishes nothing.
3. **Measured, not claimed.** Ground-truth labels → precision, recall, confusion matrix, false-tolerance rate.
4. **Controlled change.** No rule change ships without a parity diff and a reviewed release note.
5. **Documentation is part of the product.** User stories → acceptance criteria → automated UAT → runbook.

## How it works

```
generate.py ─► internal_trades + external_statement (vendor format) + ground_truth
                       │
normalise.py ─► one clean schema (column map, BIC→counterparty, DD/MM/YYYY, ISIN case/padding)
                       │
controls.py  ─► pre-checks: schema · null keys · unique IDs · ISIN check digit · currency · dates
                       │
engine.py + sql/
   Pass 1  P1_REF     reference match (ext_ref = custodian ID)                      ~69% of trades
   Pass 2  P2_ECON    economic match, ±N business days, mutual-best ROW_NUMBER, iterative   ~29%
   Pass 3  P3_GROUP   one internal = many fills (group sums), then bounded P3_SUBSET search
   Tiering            MATCHED (≤0.01) · TOLERATED (≤ max(abs, min(1bp, cap))) · break
   Classify orphans   DUPLICATE → near-match (QTY / TIMING / AMOUNT) → MISSING_*
                       │
controls.py  ─► tie-out → atomic publish: match_results · breaks · record_status · audit_log · run_log
                       │
   ├─ evaluate.py  precision / recall vs ground truth, 5 seeds
   ├─ parity.py    old vs new rule version, record-by-record diff → release notes
   ├─ explain.py   LLM (Anthropic / OpenAI) or template; JSON-validated; never edits engine fields
   ├─ workflow.py  multi-day carry-forward, ageing, OPEN→INVESTIGATING→PROPOSED→RESOLVED, maker-checker
   └─ export.py    star schema CSVs for Power BI / Tableau + formatted Excel break report
```

## Run it

```bash
make setup          # venv + dependencies (Python 3.11+)
make test           # 39 automated UAT tests
make run            # reconcile one day of 50K trades
make evaluate       # precision/recall, 5 seeds × 4 rule versions  → reports/eval_*.md
make parity         # release gates                                → reports/parity_*.md
make export         # 15-day workflow + dashboard data + Excel      → dashboard/
make benchmark      # 50K / 250K / 1M trades                        → reports/benchmark.md
```
Optional LLM explanations: set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (model via `RECON_LLM_MODEL`), then
`python -m recon.explain eval --n 100` to measure explanation accuracy against ground truth. Without a key, the deterministic template explainer is used. Its baseline is 98% cause accuracy on a stratified sample of 98 breaks ([report](reports/explain_eval_template_seed42.md)).

## Repository

| Path | What |
|---|---|
| `config/rules.yaml` | Versioned matching rules and tolerances (v1.0 → v1.3) |
| `config/generator.yaml` | Synthetic data volumes and break injection rates |
| `config/break_taxonomy.yaml` | Break types, owners, actions (also the explainer's knowledge base) |
| `recon/sql/` | Pass 1–3, tiering and classification SQL (DuckDB) |
| `docs/user_stories.md` | 8 user stories, 26 acceptance criteria (Given/When/Then), each mapped to a test |
| `docs/uat_log.md` | UAT results, including the failures found and fixed |
| `docs/decisions.md` | 12 design decisions with alternatives and trade-offs |
| `docs/break_taxonomy.md` · `process_flow.md` · `runbook.md` · `release_notes.md` | Controller and operations documentation |
| `dashboard/` | Star-schema CSVs, DAX measures, Excel break report, build guide |
| `reports/` | Evaluation, parity, workflow and benchmark reports (all generated) |

## Known limitations
- Small fee differences on USD/EUR trades can look like FX rounding (≈3.5% of amount breaks). Needs a separate fee field from the custodian.
- About 5 wrong links per 50K trades from coincidental economic matches. Quality falls at 1M trades in a 200-security universe (precision 99.72%), because keys become crowded. The next step would be to add execution time or order ID to the Pass 2 key.
- Controller pick-up and resolution behaviour in the multi-day run is simulated. Engine matching, carry-forward, ageing and maker-checker enforcement are real.
