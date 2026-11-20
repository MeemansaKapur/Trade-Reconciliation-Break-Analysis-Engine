# Benchmark — rule version v1.3

Machine: arm64 / Darwin 25.3.0, Python 3.14.2. Single process, DuckDB in-memory. Excludes synthetic data generation.

| Internal trades | Records (int+ext) | Normalise s | Reconcile s | Total s | Records/s | Precision | Recall | Auto-match | Controls |
|---|---|---|---|---|---|---|---|---|---|
| 50,000 | 100,354 | 0.04 | 1.38 | 1.42 | 70,671 | 99.994% | 99.751% | 95.76% | PASS |
| 250,000 | 502,045 | 0.21 | 6.78 | 6.98 | 71,903 | 99.937% | 99.127% | 95.29% | PASS |
| 1,000,000 | 2,008,239 | 1.00 | 29.46 | 30.46 | 65,924 | 99.723% | 98.153% | 94.64% | PASS |
