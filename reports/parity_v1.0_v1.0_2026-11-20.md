# Parity report: v1.0 → v1.0

Run date 2026-11-20 · seed 42 · 50,000 internal trades · 100,354 records compared

**Change:** Baseline. Pass 1 reference match, Pass 2 economic match (+/-1 bd), Pass 3 whole-group partial fills.

## Difference buckets

| Bucket | Records | Meaning |
|---|---|---|
| UNCHANGED | 100,354 | same status, same counterpart |
| NEWLY_MATCHED | 0 | break → matched/tolerated |
| NEWLY_BROKEN | 0 | matched/tolerated → break (**review every one**) |
| RECLASSIFIED | 0 | break type changed |
| TIER_CHANGE | 0 | MATCHED ↔ TOLERATED |
| RE_PAIRED | 0 | same status, different counterpart |

Unchanged: **100.000%** of records.

## Transitions (changed records only)

| Base status | Candidate status | Records |
|---|---|---|

## Quality vs ground truth

| Metric | v1.0 | v1.0 | Δ |
|---|---|---|---|
| pairing_precision | 99.994% | 99.994% | +0.000 pp |
| pairing_recall | 98.642% | 98.642% | +0.000 pp |
| wrong_links | 3 | 3 | +0 |
| auto_match_rate | 95.310% | 95.310% | +0.000 pp |
| status_accuracy | 98.990% | 98.990% | +0.000 pp |
| false_tolerance_count | 230 | 230 | +0 |
| false_break_count | 729 | 729 | +0 |
| partial_fill_match_rate | 61.734% | 61.734% | +0.000 pp |
| break_items | 3,268 | 3,268 | +0 |

Of the 0 changed records, **0** now agree with ground truth (were correct before: 0); **0** are still/now wrong.
