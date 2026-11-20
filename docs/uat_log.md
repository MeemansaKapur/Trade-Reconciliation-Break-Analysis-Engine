# UAT log

Executed against the acceptance criteria in [user_stories.md](user_stories.md). Automated cases run with `pytest` (39 tests). Quality criteria are checked against `reports/eval_2026-11-20.md` (5 seeds × 50,000 trades).
An honest log: three criteria **failed** on first execution and were fixed through controlled releases (see [release_notes.md](release_notes.md)).

| UAT | Story / AC | Steps | Expected | Actual | Result |
|---|---|---|---|---|---|
| UAT-01 | US-01 / 01.1 | Run 5K-trade day; count outcomes | N+M outcomes, one per record | 10,063 outcomes = 10,063 inputs; 0 duplicates | PASS |
| UAT-02 | US-01 / 01.2 | Explode match legs | No ID twice | All unique | PASS |
| UAT-03 | US-01 / 01.3 | Run twice | Identical outputs, same run_id | Identical | PASS |
| UAT-04 | US-02 / 02.1 | ₹0.40 diff, ₹1.00 tolerance | TOLERATED, rule P2_ECON audited | TOLERATED, P2_ECON, v1.3 | PASS |
| UAT-05 | US-02 / 02.2 | Exact amounts via reference | MATCHED by P1_REF | MATCHED by P1_REF | PASS |
| UAT-06 | US-02 / 02.3 | Lower tolerance in config only | Result flips to break | AMOUNT_MISMATCH | PASS |
| **UAT-07a** | US-02 / 02.4 | 5-seed evaluation, **v1.0** | 0 genuine breaks auto-tolerated | **1,188 fee breaks hidden (false tolerance 5.42%)** | **FAIL** |
| UAT-07b | US-02 / 02.4 | Re-test after release v1.3 (tolerance cap) | 0 | 0 of 2,098 hidden; parity: 230 newly broken, all `fee_mismatch` | PASS |
| UAT-08 | US-03 / 03.1 | One case per break type (8 cases) | Correct type each | 8/8 | PASS |
| UAT-09 | US-03 / 03.2 | Two identical bookings | Later one DUPLICATE | DUPLICATE, duplicate_of set | PASS |
| **UAT-10a** | US-03 / 03.3 | 5-seed evaluation, **v1.0**: partial fills matched | ≥ 90% | **61.7%** | **FAIL** |
| UAT-10b | US-03 / 03.3 | Re-test after release v1.2 (bounded subset search) | ≥ 90% | 95.8% (94.7–96.7%) | PASS |
| UAT-11 | US-03 / 03.4 | Reference pair + economic decoy | Pass 1 wins | P1_REF | PASS |
| **UAT-12a** | Step 2 AC / US-03 | **v1.0**: 2-day timing breaks | One paired break per trade | Pairs split into 2 orphan breaks (94 records, seed 42) | **FAIL** |
| UAT-12b | | Re-test after release v1.1 (±2 bd window) | Paired | 94 re-paired; break items −47; 0 new wrong links | PASS |
| UAT-13 | US-04 / 04.1 | Open day 1, resolve day 3 | age 2, reason recorded | age 2, BOOKING_CORRECTED | PASS |
| UAT-14 | US-04 / 04.2 | Age = threshold | escalated | escalated | PASS |
| UAT-15 | US-05 / 05.1 | Explain with fake LLM | Engine fields unchanged | Unchanged (frame-equal) | PASS |
| UAT-16 | US-05 / 05.2 | No API key | Template explanations | 20/20 template | PASS |
| UAT-17 | US-05 / 05.3 | Bad JSON / bad enum / contradicting type | Rejected | 3/3 rejected | PASS |
| UAT-18 | US-05 / 05.4 | Inspect prompt payload | No IDs/accounts/counterparties | None present | PASS |
| UAT-19 | US-06 / 06.1 | Audit coverage | 1 row per decision, rule + version + hash | 100% | PASS |
| UAT-20 | US-06 / 06.2 | Delete one output row | Tie-out fails | 2 checks FAIL | PASS |
| UAT-21 | US-06 / 06.3 | Bad ISIN, duplicate key | Run stops, nothing published | ControlFailure raised | PASS |
| UAT-22 | US-07 / 07.1 | Parity v1.2 vs v1.2 | 100% unchanged | 100% | PASS |
| UAT-23 | US-07 / 07.2 | Parity v1.2 vs v1.3 | Newly broken listed | All newly broken = fee_mismatch | PASS |
| UAT-24 | US-07 / 07.3 | Release notes | ≥ 2 releases with parity | 3 releases | PASS |
| UAT-25 | US-08 / 08.1 | 15-day run | Late arrivals auto-clear | 7,940 / 8,005 (99.2%) | PASS |
| UAT-26 | US-08 / 08.2 | Daily summary | 5 lines per day | 15 summaries | PASS |
| UAT-27 | US-08 / 08.3 | Maker approves own proposal | Rejected | MakerCheckerViolation; 0 self-approvals in 23,049 resolutions | PASS |
| **NFR-01a** | Performance | 15-day × 50K run | < 90 s | **2 min 39 s** (row-by-row register updates) | **FAIL** |
| NFR-01b | | Re-test after batching transitions | < 90 s | ~60 s | PASS |
| NFR-02 | Performance | 1,000,000 trades (2.0M records) single run | Linear scaling, controls pass | 30.5 s, ≈ 66K records/s, PASS | PASS |
| DOC-01 | Docs | A person who has never seen the repo runs one day from the README | Runs end-to-end | _to be done with a peer_ | OPEN |
