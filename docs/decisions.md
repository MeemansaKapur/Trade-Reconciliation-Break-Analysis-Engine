# Design decisions

Short log: decision · alternatives considered · why.

**D-01 · The LLM explains decisions and never makes them.**
Alternatives: LLM-assisted fuzzy matching. Why: matching must be deterministic, reproducible and auditable by rule ID and version. An LLM in the decision path can't be audited or regression-tested. The explainer only adds text columns, and a test asserts the engine fields are unchanged.

**D-02 · Matching passes go strictest first, and each is one-to-one.**
Alternatives: a single scored fuzzy join. Why: a loose rule must never take a record that had a certain (reference) match. Every pair records which pass and rule produced it.

**D-03 · Pass 2 uses mutual-best ranking with deterministic tie-breaks and runs iteratively.**
`ROW_NUMBER` on both sides keeps only pairs that are each other's best candidate. Final tie-break is the record ID, so identical input always gives identical output. Records whose best candidate was taken get another chance in the next round. On 50K trades this converges in 3 rounds (capped at 10).

**D-04 · Pass 3 subset search is bounded (≤ 6 legs, ≤ 12 candidate rows per key).**
Alternatives: exhaustive subset-sum (exponential) or no subset search. Why: the whole-group rule alone matched only 61.7% of partial fills, because popular securities often have several fills under the same key. The bounded search raised this to 95.8% (5-seed mean) and adds ~0.1 s per 50K-trade run. About 20–40 groups per run exceed the cap and are skipped. They are counted in `run_log.links_by_rule.P3_subset_skipped` rather than silently missed.

**D-05 · A wrong match is worse than a missed match.**
A missed match shows up as a break and gets reviewed. A wrong match hides two problems. So pairing **precision** and **false tolerances** are the headline quality metrics. Every rule change is checked for new wrong links in its parity report.

**D-06 · Tolerance = max(absolute, min(1 bp × amount, cap)) — the cap was added in v1.3.**
v1.0 used max(₹1, 1 bp). Evaluation showed this hid 56.6% of genuine fee differences (1,188 of 2,098 across 5 seeds), because 1 bp of a ₹1 crore trade is ₹1,000. Capping the relative part at ₹2 / $0.10 cut false tolerances to 0. Auto-match fell by 0.23 pp, which is the right trade: those were real breaks.

**D-07 · A failed control stops the run and publishes nothing.**
Alternatives: publish partial results with a warning. Why: a reconciliation you can't prove complete is worse than none, because people act on it. Outputs are written to a temp directory and swapped in atomically.

**D-08 · Break identity is a hash of the record IDs.**
This keeps the same break ID across days, so carried breaks keep their `opened_on` and age correctly. A carried break keeps its original classification until it pairs. For example, a duplicate stays `DUPLICATE` on day 2 even though its original isn't in day 2's file.

**D-09 · Synthetic data with ground truth instead of "realistic-looking" data.**
Labelled injections let quality be reported as precision/recall and per-class recall, not guessed. All data is labelled synthetic in the README.

**D-10 · The custodian file has its own column names, codes and formats.**
Real vendor files differ from internal schemas: BIC-style counterparty codes, `B`/`S` sides, `DD/MM/YYYY` dates, lowercase or padded ISINs. All of this is handled in `normalise.py`, so the engine sees one schema.

**D-11 · DuckDB SQL instead of pandas merges for matching.**
Window functions (`ROW_NUMBER`, `QUALIFY`), anti-joins and `DECIMAL(18,2)` amounts make the rules readable as SQL that a Finance-Tech team would recognise, and the engine stays linear (≈ 66K records/s up to 2M records).

**D-12 · Workflow transitions are batched.**
The first multi-day version updated the break register one row at a time and took 2 min 39 s for 15 days. Batching each state transition while keeping per-row validation and the maker-checker check cut this to ~60 s. The remaining time is data generation and the engine itself.
