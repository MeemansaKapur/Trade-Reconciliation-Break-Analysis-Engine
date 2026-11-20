"""Runtime benchmark at increasing volumes (engine only; data generation excluded).

Usage: python -m recon.benchmark --sizes 50000 250000 1000000
"""
from __future__ import annotations

import argparse
import datetime as dt
import platform
import time

from recon.common import REPORTS, load_rules
from recon.engine import reconcile
from recon.evaluate import score
from recon.generate import generate_days
from recon.normalise import normalise_external, normalise_internal


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[50_000, 250_000, 1_000_000])
    ap.add_argument("--version", default="v1.3")
    a = ap.parse_args(argv)
    run_date = dt.date(2026, 11, 20)
    rows = []
    for n in a.sizes:
        day = generate_days(run_date, n, 42, 1)[0]
        t = time.perf_counter()
        I, E = normalise_internal(day["internal"]), normalise_external(day["external_raw"])
        norm = time.perf_counter() - t
        res = reconcile(I, E, run_date, load_rules(a.version))
        s = score(res, day["truth"])
        total = norm + res.run_log["duration_s"]
        rows.append((n, len(I) + len(E), norm, res.run_log["duration_s"], total, (len(I) + len(E)) / total,
                     s["pairing_precision"], s["pairing_recall"], s["auto_match_rate"], res.run_log["controls_passed"]))
        print(rows[-1])
    lines = [f"# Benchmark — rule version {a.version}", "",
             f"Machine: {platform.machine()} / {platform.system()} {platform.release()}, Python {platform.python_version()}. "
             "Single process, DuckDB in-memory. Excludes synthetic data generation.", "",
             "| Internal trades | Records (int+ext) | Normalise s | Reconcile s | Total s | Records/s | Precision | Recall | Auto-match | Controls |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r[0]:,} | {r[1]:,} | {r[2]:.2f} | {r[3]:.2f} | {r[4]:.2f} | {r[5]:,.0f} | {r[6]:.3%} | "
                     f"{r[7]:.3%} | {r[8]:.2%} | {'PASS' if r[9] else 'FAIL'} |")
    (REPORTS / "benchmark.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
