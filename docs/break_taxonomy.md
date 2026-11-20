# Break taxonomy

Machine-readable source: [`config/break_taxonomy.yaml`](../config/break_taxonomy.yaml), which the explainer also uses.

## Classification order and why

Pairs created by a matching pass are tiered in this order:

1. **Quantity differs** → `QTY_MISMATCH`
2. **Settle date differs** → `TIMING`
3. amount diff ≤ 0.01 → `MATCHED`
4. amount diff ≤ tolerance → `TOLERATED`
5. non-INR and diff between 1.5 bp and 5 bp → `FX_ROUNDING`
6. otherwise → `AMOUNT_MISMATCH`

Records that no pass could pair are classified in this order:

| Priority | Test | break_type | Why this position |
|---|---|---|---|
| 1 | Identical to an earlier internal booking except ID | `DUPLICATE` | If tested after "missing", every duplicate would be mislabelled `MISSING_EXTERNAL` and the fix (cancel the booking) would be missed |
| 2 | Near-match exists (same ISIN/side/ccy/counterparty, same qty **or** same price, settle within ±5 bd) | `QTY_MISMATCH` / `TIMING` / `AMOUNT_MISMATCH` | A near-match is a strong hint that it's the same trade with an error, so the controller is given the suggested counterpart |
| 3 | Nothing similar on the other side | `MISSING_EXTERNAL` / `MISSING_INTERNAL` | Last resort |

The near-match ("diagnostic") join is **not** a match. It only fills `suggested_counterpart`.

## Types

| Break type | Severity | Typical cause | Owner | Suggested action |
|---|---|---|---|---|
| `AMOUNT_MISMATCH` | High | Fee/commission treatment (small absolute diff) or wrong price (≥ ~0.1%) | Trade support | Compare price and fees with the broker confirm; amend or claim |
| `QTY_MISMATCH` | High | Partial execution booked as full, keying error | Trade support | Check execution report; amend quantity or chase the fill |
| `TIMING` | Medium | Settlement-cycle assumption (T+1 vs T+2), holiday calendar | Settlements | Confirm cycle with custodian; amend value date |
| `FX_ROUNDING` | Low | Different FX rate or rounding convention | Product control | Confirm rate source; write off within policy (needs checker approval) |
| `MISSING_EXTERNAL` | High | Custodian hasn't confirmed, or the trade failed | Settlements | Chase custodian; most clear within 1–3 business days |
| `MISSING_INTERNAL` | High | Trade executed but not booked, or booked to another account | Trade support | Search other books; book if genuine |
| `DUPLICATE` | High | Same trade booked twice | Trade support | Cancel the later booking after confirming one execution |

## Known limitations (measured)

- **Small fee differences on USD/EUR trades can look like FX rounding.** By amount alone, a $1.70 fee on a $3,600 trade (4.7 bp) is indistinguishable from an FX conversion difference. v1.3 added a 1.5 bp floor to the FX band, which fixed most cases, but ~3–4% of `AMOUNT_MISMATCH` records are still labelled `FX_ROUNDING` (see the confusion matrix in `reports/eval_*.md`). Fix: carry a separate fee/commission field from the custodian file.
- **Coincidental economic matches.** About 5 wrong links per 50K trades: a partial-fill leg happened to share ISIN, side, counterparty and quantity with a different trade, with a price within the 5% candidate band. Wrong links are the most dangerous error (they hide two problems), so they are tracked separately.
