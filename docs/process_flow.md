# Daily reconciliation process

```mermaid
flowchart TD
    A[Receive files<br/>internal bookings + custodian statement] --> B[Normalise<br/>vendor columns, codes, date formats]
    B --> C{Pre-checks<br/>schema · null keys · unique IDs<br/>ISIN check digit · currency · dates}
    C -- FAIL --> X[Stop run<br/>publish nothing<br/>alert per runbook]
    C -- PASS --> D[Add yesterday's open<br/>unpaired records - carry-forward]
    D --> E[Pass 1: reference match<br/>P1_REF]
    E --> F[Pass 2: economic match<br/>P2_ECON, mutual-best, iterative]
    F --> G[Pass 3: one-to-many<br/>P3_GROUP, then bounded P3_SUBSET]
    G --> H[Tier pairs<br/>MATCHED / TOLERATED / break]
    H --> I[Classify unpaired records<br/>DUPLICATE → near-match → MISSING]
    I --> J{Tie-out<br/>records in = records out<br/>per side, per currency, count and amount}
    J -- FAIL --> X
    J -- PASS --> K[Publish atomically<br/>match_results · breaks · audit_log · run_log]
    K --> L[Explain breaks + daily summary<br/>LLM or template, read-only]
    L --> M[Dashboard / Excel report]
    M --> N[Controller triage<br/>OPEN → INVESTIGATING]
    N --> O[Maker proposes resolution<br/>PROPOSED]
    O --> P[Checker approves<br/>different person → RESOLVED]
    K -. late arrival next day .-> D
```

| Stage | Owner | Output | Code |
|---|---|---|---|
| Normalise | Engine | clean external frame | `recon/normalise.py` |
| Pre-checks / tie-out | Engine (control) | `controls.csv` | `recon/controls.py` |
| Matching passes | Engine | `match_results.parquet` | `recon/sql/pass*.sql` |
| Classification | Engine | `breaks.parquet` | `recon/sql/classify_orphans.sql` |
| Audit | Engine | `audit_log.parquet`, `run_log.json` | `recon/engine.py` |
| Explanation | Explainer (read-only) | `breaks_explained.parquet`, `daily_summary.md` | `recon/explain.py` |
| Workflow | Controllers (maker + checker) | `break_register.parquet` | `recon/workflow.py` |
| Rule change | Change manager | parity report + release note | `recon/parity.py` |
