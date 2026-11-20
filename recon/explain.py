"""GenAI break explanations and the daily controller summary.

Design rule: the LLM EXPLAINS decisions, it never MAKES them. Matching, tiering and classification are
deterministic and audited. This module only adds three text columns to the break table.

Guardrails
  * data minimisation: only the fields needed to explain a break are sent (no trade IDs, accounts, counterparties)
  * temperature 0 and a strict JSON contract; invalid JSON or an out-of-enum cause -> template fallback
  * if the text names a different break type than the engine assigned, it is flagged and replaced
  * results cached by input hash, so re-runs do not re-call the API
  * works with no API key at all (template mode)

Usage:
  python -m recon.explain --run-dir data/output/seed=42_n=50000/version=v1.3/run_date=2026-11-20
  python -m recon.explain eval --n 100 --seed 42        # LLM accuracy vs ground truth (needs an API key)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

import pandas as pd

from recon.common import DATA, REPORTS, load_rules, load_yaml, stable_hash

EXPLAIN_COLS = ["explanation", "suggested_action", "cause_category", "explainer", "explainer_flag"]
CACHE = DATA / "cache" / "explanations.jsonl"
SYSTEM_PROMPT = """You are assisting a financial controller who reviews trade reconciliation breaks.
A deterministic engine has ALREADY classified the break. You must not change or question the break_type.
Using ONLY the JSON data provided, explain the most likely cause in at most 3 sentences and give one concrete next step.
Choose cause_category from the allowed list given in the data. Quote the actual numbers.
Respond with JSON only: {"explanation": str, "suggested_action": str, "cause_category": str, "confidence": "low"|"medium"|"high"}"""


def taxonomy() -> dict:
    return load_yaml("break_taxonomy.yaml")


def payload(b: pd.Series, tax: dict) -> dict:
    """Minimal, non-identifying view of a break."""
    t = tax[b["break_type"]]
    amt = float(b["amount"]) if pd.notna(b["amount"]) else None
    diff = float(b["amount_diff"]) if pd.notna(b["amount_diff"]) else None
    return {
        "break_type": b["break_type"],
        "side": {"BOTH": "paired", "INT": "internal only", "EXT": "external only"}[b["side"]],
        "currency": b["currency"],
        "amount": round(amt, 2) if amt is not None else None,
        "amount_diff": round(diff, 2) if diff is not None else None,
        "amount_diff_bp": round(abs(diff) / abs(amt) * 1e4, 2) if diff is not None and amt else None,
        "qty_diff": float(b["qty_diff"]) if pd.notna(b["qty_diff"]) else None,
        "settle_day_diff": int(b["day_diff"]) if pd.notna(b["day_diff"]) else None,
        "near_match_found": bool(pd.notna(b.get("suggested_counterpart"))) or b["side"] == "BOTH",
        "age_days": int(b.get("age_days", 0) or 0),
        "taxonomy": {"condition": t["condition"], "action": t["action"]},
        "allowed_cause_category": list(t["likely_causes"]),
        "cause_hints": t["likely_causes"],
    }


def template_explain(p: dict) -> dict:
    """Deterministic fallback, built only from the taxonomy and the numbers."""
    causes = p["allowed_cause_category"]
    cause = causes[0]
    if p["break_type"] == "AMOUNT_MISMATCH" and p["amount_diff_bp"] is not None:
        cause = "FEE_OR_COMMISSION" if p["amount_diff_bp"] < 5 else "PRICE_OR_BOOKING_ERROR"
    nums = []
    if p["amount_diff"] is not None:
        nums.append(f"amount differs by {p['amount_diff']:,.2f} {p['currency']} ({p['amount_diff_bp']} bp)")
    if p["qty_diff"]:
        nums.append(f"quantity differs by {p['qty_diff']:,.0f}")
    if p["settle_day_diff"]:
        nums.append(f"settle date differs by {p['settle_day_diff']} business day(s)")
    detail = "; ".join(nums) if nums else f"{p['side']} record of {p['amount']:,.2f} {p['currency']} with no counterpart"
    return {"explanation": f"{p['break_type'].replace('_', ' ').title()}: {detail}. Likely cause: "
                           f"{p['cause_hints'][cause]}.",
            "suggested_action": p["taxonomy"]["action"], "cause_category": cause, "confidence": "medium"}


class LLM:
    """Thin provider adapter. Picks Anthropic or OpenAI from the environment; None if neither is configured."""

    def __init__(self):
        self.provider = self.model = self.client = None
        if os.getenv("ANTHROPIC_API_KEY"):
            try:
                import anthropic
                self.client, self.provider = anthropic.Anthropic(), "anthropic"
                self.model = os.getenv("RECON_LLM_MODEL", "claude-haiku-5-5")
            except ImportError:
                pass
        if not self.client and os.getenv("OPENAI_API_KEY"):
            try:
                import openai
                self.client, self.provider = openai.OpenAI(), "openai"
                self.model = os.getenv("RECON_LLM_MODEL", "gpt-4o-mini")
            except ImportError:
                pass

    @property
    def available(self) -> bool:
        return self.client is not None

    def complete(self, system: str, user: str) -> str:
        if self.provider == "anthropic":
            r = self.client.messages.create(model=self.model, max_tokens=400, temperature=0, system=system,
                                            messages=[{"role": "user", "content": user}])
            return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        r = self.client.chat.completions.create(model=self.model, temperature=0,
                                                response_format={"type": "json_object"},
                                                messages=[{"role": "system", "content": system},
                                                          {"role": "user", "content": user}])
        return r.choices[0].message.content


def validate(raw: str, p: dict) -> tuple[dict | None, str]:
    try:
        txt = raw.strip()
        if txt.startswith("```"):
            txt = txt.strip("`").split("\n", 1)[1].rsplit("```", 1)[0] if "\n" in txt else txt
        out = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
    except Exception:
        return None, "invalid_json"
    if not all(isinstance(out.get(k), str) and out[k].strip() for k in ("explanation", "suggested_action", "cause_category")):
        return None, "missing_fields"
    if out["cause_category"] not in p["allowed_cause_category"]:
        return None, "cause_out_of_enum"
    others = [bt for bt in taxonomy() if bt != p["break_type"]]
    text = (out["explanation"] + " " + out["suggested_action"]).upper()
    if any(bt in text or bt.replace("_", " ") in text for bt in others):
        return None, "contradicts_break_type"
    return out, ""


def _load_cache() -> dict:
    if not CACHE.exists():
        return {}
    return {r["key"]: r["value"] for r in map(json.loads, CACHE.read_text().splitlines()) if r}


def explain_breaks(breaks: pd.DataFrame, llm: LLM | None = None, limit: int | None = None) -> pd.DataFrame:
    """Return a copy of `breaks` with explanation columns added. Never modifies existing columns."""
    llm = llm or LLM()
    tax = taxonomy()
    cache = _load_cache()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    out = breaks.copy()
    for c in EXPLAIN_COLS:
        out[c] = None
    rows = out.index if limit is None else out.index[:limit]
    with open(CACHE, "a") as cf:
        for ix in rows:
            p = payload(out.loc[ix], tax)
            key = stable_hash([p, llm.provider, llm.model, SYSTEM_PROMPT])
            flag, src = "", "template"
            res = cache.get(key)
            if res is None and llm.available:
                try:
                    res, flag = validate(llm.complete(SYSTEM_PROMPT, json.dumps(p)), p)
                except Exception as e:  # network / quota: degrade, never fail the run
                    res, flag = None, f"api_error:{type(e).__name__}"
                if res is not None:
                    cf.write(json.dumps({"key": key, "value": res}) + "\n")
                    cache[key] = res
            if res is not None:
                src = f"llm:{llm.model}"
            else:
                res = template_explain(p)
            out.loc[ix, ["explanation", "suggested_action", "cause_category", "explainer", "explainer_flag"]] = [
                res["explanation"], res["suggested_action"], res["cause_category"], src, flag]
    return out


# ----------------------------------------------------------------------------- daily summary
def summary_stats(today: pd.DataFrame, register: pd.DataFrame | None, run_date: dt.date, prev: dict | None = None) -> dict:
    reg = register if register is not None else today.assign(status="OPEN")
    open_ = reg[reg.status != "RESOLVED"]
    stats = {
        "run_date": run_date.isoformat(),
        "open_breaks": int(len(open_)),
        "new_today": int((reg["opened_on"].astype(str) == run_date.isoformat()).sum()),
        "resolved_today": int((reg.get("resolved_on", pd.Series(dtype=str)).astype(str) == run_date.isoformat()).sum()),
        "open_by_type": open_["break_type"].value_counts().to_dict(),
        "open_exposure_inr": round(float(open_["exposure_inr"].sum()), 0),
        "escalated": int(open_.get("escalated", pd.Series(False, index=open_.index)).sum()),
        "oldest": open_.sort_values(["age_days", "exposure_inr"], ascending=False).head(3)[
            ["break_type", "currency", "age_days", "exposure_inr"]].to_dict("records"),
        "largest": open_.sort_values("exposure_inr", ascending=False).head(3)[
            ["break_type", "currency", "age_days", "exposure_inr"]].to_dict("records"),
    }
    if prev:
        stats["open_change_vs_prev"] = stats["open_breaks"] - prev["open_breaks"]
    return stats


def template_summary(s: dict) -> str:
    top = ", ".join(f"{k} {v}" for k, v in list(s["open_by_type"].items())[:3])
    chg = f" ({s['open_change_vs_prev']:+,} vs previous day)" if "open_change_vs_prev" in s else ""
    big = s["largest"][0] if s["largest"] else None
    old = s["oldest"][0] if s["oldest"] else None
    return "\n".join([
        f"- {s['run_date']}: {s['open_breaks']:,} open breaks{chg}; {s['new_today']:,} new, {s['resolved_today']:,} resolved.",
        f"- Largest categories: {top}.",
        f"- Open exposure ≈ ₹{s['open_exposure_inr']:,.0f}; {s['escalated']:,} breaks past the escalation threshold.",
        f"- Largest single item: {big['break_type']} in {big['currency']}, ≈ ₹{big['exposure_inr']:,.0f}." if big else "- No open items.",
        f"- Oldest item: {old['break_type']}, {old['age_days']} business days old." if old else "- No ageing items.",
    ])


def daily_summary(stats: dict, llm: LLM | None = None) -> tuple[str, str]:
    llm = llm or LLM()
    if llm.available:
        try:
            txt = llm.complete("Write a 5-line status summary for a financial control team lead. Use only the numbers "
                               "given, bullet points, no speculation.", json.dumps(stats, default=str))
            if txt and len(txt.splitlines()) <= 8:
                return txt.strip(), f"llm:{llm.model}"
        except Exception:
            pass
    return template_summary(stats), "template"


# ----------------------------------------------------------------------------- evaluation
CAUSE_TRUTH = {"fee_mismatch": "FEE_OR_COMMISSION", "amount_mismatch": "PRICE_OR_BOOKING_ERROR",
               "fx_rounding": "FX_CONVERSION", "qty_mismatch": "PARTIAL_EXECUTION_OR_BOOKING_ERROR",
               "timing": "SETTLEMENT_DATE_DIFFERENCE", "missing_external": "UNCONFIRMED_TRADE",
               "missing_internal": "UNBOOKED_TRADE", "duplicate": "DUPLICATE_BOOKING"}


def evaluate_explanations(n: int, seed: int, version: str | None = None) -> str:
    from recon.engine import reconcile
    from recon.generate import generate_days
    from recon.normalise import normalise_external, normalise_internal
    run_date = dt.date(2026, 11, 20)
    day = generate_days(run_date, 50_000, seed, 1)[0]
    res = reconcile(normalise_internal(day["internal"]), normalise_external(day["external_raw"]), run_date,
                    load_rules(version))
    b = res.breaks.copy()
    b["record_id"] = b["int_trade_ids"].fillna(b["ext_trade_ids"]).str.split(",").str[0]
    b["rside"] = b["side"].replace({"BOTH": "INT"})
    t = day["truth"].rename(columns={"side": "rside"})
    b = b.merge(t[["record_id", "rside", "scenario"]], on=["record_id", "rside"], how="left")
    b = b[b["scenario"].isin(CAUSE_TRUTH)]
    # stratified sample so every break type is represented
    per_type = max(1, n // b["break_type"].nunique())
    sample = pd.concat([g.sample(min(len(g), per_type), random_state=seed)
                        for _, g in b.groupby("break_type")]).head(n).reset_index(drop=True)
    llm = LLM()
    explained = explain_breaks(sample, llm)
    explained["truth_cause"] = explained["scenario"].map(CAUSE_TRUTH)
    explained["correct"] = explained["cause_category"] == explained["truth_cause"]
    status_unchanged = explained[res.breaks.columns.drop(EXPLAIN_COLS, errors="ignore")].equals(
        sample[res.breaks.columns.drop(EXPLAIN_COLS, errors="ignore")])
    mode = f"LLM ({llm.provider}:{llm.model})" if llm.available else "TEMPLATE ONLY (no API key set)"
    acc = explained["correct"].mean()
    by = explained.groupby("break_type")["correct"].agg(["mean", "size"])
    lines = [f"# Explanation evaluation — {mode}", "",
             f"Sample: {len(explained)} breaks (stratified by type), seed {seed}, rule version {res.rule_version}.", "",
             f"- Cause accuracy vs ground truth: **{acc:.1%}** (n = {len(explained)})",
             f"- Explanations served by LLM: {(explained['explainer'].str.startswith('llm')).sum()} · "
             f"template fallbacks: {(explained['explainer'] == 'template').sum()}",
             f"- Guardrail flags: {explained['explainer_flag'].replace('', pd.NA).value_counts().to_dict()}",
             f"- Engine fields unchanged by the explainer: **{status_unchanged}**", "",
             "| Break type | Accuracy | n |", "|---|---|---|"]
    lines += [f"| {k} | {r['mean']:.0%} | {int(r['size'])} |" for k, r in by.iterrows()]
    wrong = explained[~explained["correct"]]
    if len(wrong):
        lines += ["", "## Misses", ""] + [
            f"- {r.break_type} truth={r.truth_cause} said={r.cause_category}: {r.explanation[:160]}"
            for r in wrong.head(15).itertuples()]
    REPORTS.mkdir(exist_ok=True)
    tag = "llm" if llm.available else "template"
    (REPORTS / f"explain_eval_{tag}_seed{seed}.md").write_text("\n".join(lines) + "\n")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    e = sub.add_parser("eval")
    e.add_argument("--n", type=int, default=100)
    e.add_argument("--seed", type=int, default=42)
    e.add_argument("--version", default=None)
    ap.add_argument("--run-dir", type=Path)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args(argv)
    if a.cmd == "eval":
        print(evaluate_explanations(a.n, a.seed, a.version))
        return
    if not a.run_dir:
        ap.error("--run-dir is required")
    b = pd.read_parquet(a.run_dir / "breaks.parquet")
    out = explain_breaks(b, limit=a.limit)
    out.to_parquet(a.run_dir / "breaks_explained.parquet", index=False)
    run_date = dt.date.fromisoformat(a.run_dir.name.split("=")[1])
    text, src = daily_summary(summary_stats(out, None, run_date))
    (a.run_dir / "daily_summary.md").write_text(text + f"\n\n_source: {src}_\n")
    print(out[["break_type", "explanation"]].head(5).to_string()), print(), print(text)


if __name__ == "__main__":
    main()
