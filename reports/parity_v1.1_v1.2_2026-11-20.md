# Parity report: v1.1 → v1.2

Run date 2026-11-20 · seed 42 · 50,000 internal trades · 100,354 records compared

**Change:** Pass 3 bounded subset search (<= 6 legs, <= 12 candidates) for partial fills whose key group is polluted by other fills.

## Difference buckets

| Bucket | Records | Meaning |
|---|---|---|
| UNCHANGED | 99,675 | same status, same counterpart |
| NEWLY_MATCHED | 679 | break → matched/tolerated |
| NEWLY_BROKEN | 0 | matched/tolerated → break (**review every one**) |
| RECLASSIFIED | 0 | break type changed |
| TIER_CHANGE | 0 | MATCHED ↔ TOLERATED |
| RE_PAIRED | 0 | same status, different counterpart |

Unchanged: **99.323%** of records.

## Transitions (changed records only)

| Base status | Candidate status | Records |
|---|---|---|
| MISSING_INTERNAL | MATCHED | 286 |
| QTY_MISMATCH | MATCHED | 284 |
| MISSING_INTERNAL | TOLERATED | 55 |
| QTY_MISMATCH | TOLERATED | 54 |

## Quality vs ground truth

| Metric | v1.1 | v1.2 | Δ |
|---|---|---|---|
| pairing_precision | 99.994% | 99.994% | +0.000 pp |
| pairing_recall | 98.736% | 99.751% | +1.015 pp |
| wrong_links | 3 | 3 | +0 |
| auto_match_rate | 95.310% | 95.986% | +0.677 pp |
| status_accuracy | 98.990% | 99.666% | +0.677 pp |
| false_tolerance_count | 230 | 230 | +0 |
| false_break_count | 729 | 50 | -679 |
| partial_fill_match_rate | 61.734% | 97.463% | +35.729 pp |
| break_items | 3,221 | 2,542 | -679 |

Of the 679 changed records, **679** now agree with ground truth (were correct before: 0); **0** are still/now wrong.
