# Release notes

Every entry below was gated by a parity report (`recon/parity.py`).

## v1.1 — released 2026-11-20

- Change: Widen Pass 2 settle-date window from +/-1 to +/-2 business days so 2-day timing breaks pair up.
- Parity vs v1.0 on 2026-11-20 (seed 42): 100,260 unchanged, 0 newly matched, 0 newly broken, 0 reclassified, 0 tier changes, 94 re-paired
- Quality (seed 42): pairing recall 98.642% → 98.736%; false tolerances 230 → 230; wrong links 3 → 3; break items 3,268 → 3,221
- Every newly-broken record reviewed: see `reports/parity_v1.0_v1.1_2026-11-20.md` and the matching CSV
- Maker: project author · Checker: second reviewer (simulated)

## v1.2 — released 2026-11-20

- Change: Pass 3 bounded subset search (<= 6 legs, <= 12 candidates) for partial fills whose key group is polluted by other fills.
- Parity vs v1.1 on 2026-11-20 (seed 42): 99,675 unchanged, 679 newly matched, 0 newly broken, 0 reclassified, 0 tier changes, 0 re-paired
- Quality (seed 42): pairing recall 98.736% → 99.751%; false tolerances 230 → 230; wrong links 3 → 3; break items 3,221 → 2,542
- Every newly-broken record reviewed: see `reports/parity_v1.1_v1.2_2026-11-20.md` and the matching CSV
- Maker: project author · Checker: second reviewer (simulated)

## v1.3 — released 2026-11-20

- Change: Fee handling. Cap the relative (1 bp) tolerance at 2.00 INR / 0.10 USD,EUR so fee differences on large trades are no longer auto-tolerated, and require FX_ROUNDING diffs to be >= 1.5 bp so small fee differences on non-INR trades are not mislabelled as FX.
- Parity vs v1.2 on 2026-11-20 (seed 42): 100,102 unchanged, 0 newly matched, 230 newly broken, 22 reclassified, 0 tier changes, 0 re-paired
- Quality (seed 42): pairing recall 99.751% → 99.751%; false tolerances 230 → 0; wrong links 3 → 3; break items 2,542 → 2,657
- Every newly-broken record reviewed: see `reports/parity_v1.2_v1.3_2026-11-20.md` and the matching CSV
- Maker: project author · Checker: second reviewer (simulated)
