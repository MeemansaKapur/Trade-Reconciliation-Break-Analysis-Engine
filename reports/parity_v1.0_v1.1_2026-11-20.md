# Parity report: v1.0 → v1.1

Run date 2026-11-20 · seed 42 · 50,000 internal trades · 100,354 records compared

**Change:** Widen Pass 2 settle-date window from +/-1 to +/-2 business days so 2-day timing breaks pair up.

## Difference buckets

| Bucket | Records | Meaning |
|---|---|---|
| UNCHANGED | 100,260 | same status, same counterpart |
| NEWLY_MATCHED | 0 | break → matched/tolerated |
| NEWLY_BROKEN | 0 | matched/tolerated → break (**review every one**) |
| RECLASSIFIED | 0 | break type changed |
| TIER_CHANGE | 0 | MATCHED ↔ TOLERATED |
| RE_PAIRED | 94 | same status, different counterpart |

Unchanged: **99.906%** of records.

## Transitions (changed records only)

| Base status | Candidate status | Records |
|---|---|---|
| TIMING | TIMING | 94 |

## Quality vs ground truth

| Metric | v1.0 | v1.1 | Δ |
|---|---|---|---|
| pairing_precision | 99.994% | 99.994% | +0.000 pp |
| pairing_recall | 98.642% | 98.736% | +0.094 pp |
| wrong_links | 3 | 3 | +0 |
| auto_match_rate | 95.310% | 95.310% | +0.000 pp |
| status_accuracy | 98.990% | 98.990% | +0.000 pp |
| false_tolerance_count | 230 | 230 | +0 |
| false_break_count | 729 | 729 | +0 |
| partial_fill_match_rate | 61.734% | 61.734% | +0.000 pp |
| break_items | 3,268 | 3,221 | -47 |

Of the 94 changed records, **94** now agree with ground truth (were correct before: 94); **0** are still/now wrong.
