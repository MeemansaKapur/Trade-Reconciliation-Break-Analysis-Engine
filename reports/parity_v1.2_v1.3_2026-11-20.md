# Parity report: v1.2 → v1.3

Run date 2026-11-20 · seed 42 · 50,000 internal trades · 100,354 records compared

**Change:** Fee handling. Cap the relative (1 bp) tolerance at 2.00 INR / 0.10 USD,EUR so fee differences on large trades are no longer auto-tolerated, and require FX_ROUNDING diffs to be >= 1.5 bp so small fee differences on non-INR trades are not mislabelled as FX.

## Difference buckets

| Bucket | Records | Meaning |
|---|---|---|
| UNCHANGED | 100,102 | same status, same counterpart |
| NEWLY_MATCHED | 0 | break → matched/tolerated |
| NEWLY_BROKEN | 230 | matched/tolerated → break (**review every one**) |
| RECLASSIFIED | 22 | break type changed |
| TIER_CHANGE | 0 | MATCHED ↔ TOLERATED |
| RE_PAIRED | 0 | same status, different counterpart |

Unchanged: **99.749%** of records.

## Transitions (changed records only)

| Base status | Candidate status | Records |
|---|---|---|
| TOLERATED | AMOUNT_MISMATCH | 230 |
| FX_ROUNDING | AMOUNT_MISMATCH | 22 |

## Quality vs ground truth

| Metric | v1.2 | v1.3 | Δ |
|---|---|---|---|
| pairing_precision | 99.994% | 99.994% | +0.000 pp |
| pairing_recall | 99.751% | 99.751% | +0.000 pp |
| wrong_links | 3 | 3 | +0 |
| auto_match_rate | 95.986% | 95.757% | -0.229 pp |
| status_accuracy | 99.666% | 99.917% | +0.251 pp |
| false_tolerance_count | 230 | 0 | -230 |
| false_break_count | 50 | 50 | +0 |
| partial_fill_match_rate | 97.463% | 97.463% | +0.000 pp |
| break_items | 2,542 | 2,657 | +115 |

Of the 252 changed records, **252** now agree with ground truth (were correct before: 0); **0** are still/now wrong.

Newly broken by scenario (all 230 listed in the CSV for review):

- fee_mismatch: 230
