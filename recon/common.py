"""Shared helpers: config loading, business-day calendar, ISIN check digits, hashing, paths."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
ENGINE_VERSION = "1.0.0"  # code version; rule versions live in config/rules.yaml


# ----------------------------------------------------------------------------- config
def load_yaml(name: str) -> dict:
    with open(CONFIG / name) as f:
        return yaml.safe_load(f)


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_rules(version: str | None = None, rules_doc: dict | None = None) -> dict:
    """Resolve a rule version (following `inherits`) into one flat config dict."""
    doc = rules_doc or load_yaml("rules.yaml")
    version = version or doc["current"]
    versions = doc["versions"]
    if version not in versions:
        raise KeyError(f"Unknown rule version {version!r}; known: {sorted(versions)}")
    chain, v = [], version
    while v:
        chain.append(v)
        v = versions[v].get("inherits")
    resolved: dict = {}
    for v in reversed(chain):
        layer = {k: val for k, val in versions[v].items() if k != "inherits"}
        resolved = _deep_merge(resolved, layer)
    resolved["version"] = version
    return resolved


def gen_config() -> dict:
    return load_yaml("generator.yaml")


# ----------------------------------------------------------------------------- calendar
@lru_cache(maxsize=1)
def holidays() -> frozenset[dt.date]:
    return frozenset(dt.date.fromisoformat(d) for d in gen_config().get("holidays", []))


def is_business_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in holidays()


def add_bd(d: dt.date, n: int) -> dt.date:
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    while remaining:
        d = d + dt.timedelta(days=step)
        if is_business_day(d):
            remaining -= 1
    return d


def business_days(start: dt.date, count: int) -> list[dt.date]:
    d = start if is_business_day(start) else add_bd(start, 1)
    out = [d]
    while len(out) < count:
        d = add_bd(d, 1)
        out.append(d)
    return out


def bd_between(a: dt.date, b: dt.date) -> int:
    """Signed number of business days from a to b."""
    if a == b:
        return 0
    sign = 1 if b > a else -1
    lo, hi = (a, b) if b > a else (b, a)
    n, d = 0, lo
    while d < hi:
        d += dt.timedelta(days=1)
        if is_business_day(d):
            n += 1
    return sign * n


@lru_cache(maxsize=None)
def bd_index(d: dt.date) -> int:
    """Business-day ordinal relative to 2020-01-01 (fast ageing arithmetic)."""
    return bd_between(dt.date(2020, 1, 1), d)


def calendar_frame(start: dt.date, end: dt.date) -> pd.DataFrame:
    """One row per calendar date with a running business-day index (used for SQL bd diffs)."""
    rows, idx, d = [], 0, start
    while d <= end:
        if is_business_day(d):
            idx += 1
        rows.append((d, idx))
        d += dt.timedelta(days=1)
    return pd.DataFrame(rows, columns=["d", "bd_idx"])


# ----------------------------------------------------------------------------- ISIN
def isin_check_digit(body11: str) -> str:
    digits = "".join(str(int(c, 36)) for c in body11.upper())
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def isin_is_valid(isin: str) -> bool:
    if not isinstance(isin, str) or len(isin) != 12 or not isin[:2].isalpha() or not isin.isalnum():
        return False
    return isin_check_digit(isin[:11]) == isin[11]


# ----------------------------------------------------------------------------- hashing
def stable_hash(obj, n: int = 16) -> str:
    payload = json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:n]


def raw_dir(run_date: dt.date, seed: int, n: int) -> Path:
    return DATA / "raw" / f"run_date={run_date.isoformat()}" / f"seed={seed}_n={n}"
