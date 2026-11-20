# Runbook

## Daily run
```bash
.venv/bin/python -m recon.engine --date 2026-11-20 --n 50000 --seed 42          # one day, current rules
.venv/bin/python -m recon.workflow --start 2026-11-20 --days 15 --n 50000      # multi-day with carry-forward
.venv/bin/python -m recon.explain --run-dir "data/output/seed=42_n=50000/version=v1.3/run_date=2026-11-20"
.venv/bin/python -m recon.export --workflow-dir "data/output/workflow_seed=42_n=50000_days=15_v1.3"
```
Check `run_log.json` → `controls_passed: true` before anyone uses the outputs.

## When a control fails
The run prints `RUN FAILED - nothing published` and exits with code 2. Nothing is written for that date, and the previous outputs are untouched.

| Failed check | Likely cause | Action |
|---|---|---|
| `schema` | Vendor changed the file layout | Update `VENDOR_COLUMN_MAP` in `normalise.py`; re-run |
| `no_null_keys` | Truncated / partial file | Request a re-send; do not run on a partial file |
| `unique_primary_key` | File delivered twice and concatenated | De-duplicate at source; re-run |
| `isin_check_digit` | Corrupted identifier | Correct with static data team |
| `known_currency` | New currency traded | Add to `tolerated_abs` in a **new rule version** (parity required) |
| `records_in_equals_records_out` / `control_totals_tie_out_per_currency` | Engine defect | Treat as an incident: do not publish; raise a bug with the run_id |

## Releasing a rule change
1. Add a new version to `config/rules.yaml` with `inherits:` and only the changed keys. Never edit a released version.
2. `python -m recon.parity --base <current> --candidate <new>`
3. Review **every** `NEWLY_BROKEN` and `RE_PAIRED` record in `reports/parity_*_changes.csv`. Check that wrong links didn't increase.
4. If intended, re-run with `--release --approver "<checker name>"` to append to `docs/release_notes.md`.
5. Set `current:` in `rules.yaml` to the new version.

## Escalation
Open breaks at age ≥ `escalation_age_bd` (5 business days) are flagged `escalated` in the register, Excel report and dashboard. Raise these with the team lead daily, and before month-end close.
