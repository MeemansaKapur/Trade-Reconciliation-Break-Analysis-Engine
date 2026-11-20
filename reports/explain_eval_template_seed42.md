# Explanation evaluation — TEMPLATE ONLY (no API key set)

Sample: 98 breaks (stratified by type), seed 42, rule version v1.3.

- Cause accuracy vs ground truth: **98.0%** (n = 98)
- Explanations served by LLM: 0 · template fallbacks: 98
- Guardrail flags: {}
- Engine fields unchanged by the explainer: **True**

| Break type | Accuracy | n |
|---|---|---|
| AMOUNT_MISMATCH | 100% | 14 |
| DUPLICATE | 100% | 14 |
| FX_ROUNDING | 86% | 14 |
| MISSING_EXTERNAL | 100% | 14 |
| MISSING_INTERNAL | 100% | 14 |
| QTY_MISMATCH | 100% | 14 |
| TIMING | 100% | 14 |

## Misses

- FX_ROUNDING truth=FEE_OR_COMMISSION said=FX_CONVERSION: Fx Rounding: amount differs by -1.71 USD (4.7 bp). Likely cause: custodian converted at a slightly different rate or rounding convention.
- FX_ROUNDING truth=FEE_OR_COMMISSION said=FX_CONVERSION: Fx Rounding: amount differs by -0.84 EUR (1.95 bp). Likely cause: custodian converted at a slightly different rate or rounding convention.
