"""Normalisation layer: map the custodian's vendor file onto the internal schema.

Real vendor files use their own column names, codes and formats. Everything vendor-specific lives here
so the matching engine only ever sees one clean schema.
"""
from __future__ import annotations

import pandas as pd

from recon.common import load_yaml

VENDOR_COLUMN_MAP = {
    "ext_trade_id": "ext_trade_id", "security_id": "isin", "buy_sell": "side", "qty": "quantity",
    "px": "price", "net_amount": "amount", "ccy": "currency", "trade_dt": "trade_date",
    "value_date": "settle_date", "bic": "counterparty", "acct": "account",
}
SIDE_MAP = {"B": "BUY", "S": "SELL", "BUY": "BUY", "SELL": "SELL"}
ECON_COLS = ["isin", "side", "quantity", "price", "amount", "currency", "trade_date", "settle_date",
             "counterparty", "account"]


class NormalisationError(ValueError):
    pass


def normalise_external(raw: pd.DataFrame) -> pd.DataFrame:
    missing = set(VENDOR_COLUMN_MAP) - set(raw.columns)
    if missing:
        raise NormalisationError(f"external file missing vendor columns: {sorted(missing)}")
    df = raw.rename(columns=VENDOR_COLUMN_MAP)[list(VENDOR_COLUMN_MAP.values())].copy()
    df["ext_trade_id"] = df["ext_trade_id"].astype(str).str.strip()
    df["isin"] = df["isin"].astype(str).str.strip().str.upper()
    df["side"] = df["side"].astype(str).str.strip().str.upper().map(SIDE_MAP)
    df["currency"] = df["currency"].astype(str).str.strip().str.upper()
    for c in ("trade_date", "settle_date"):
        df[c] = pd.to_datetime(df[c], format="%d/%m/%Y", errors="coerce").dt.date
    bic_to_cp = {v: k for k, v in load_yaml("counterparty_map.yaml").items()}
    df["counterparty"] = df["counterparty"].astype(str).str.strip().map(bic_to_cp)
    df["account"] = df["account"].astype(str).str.strip()
    df["quantity"] = df["quantity"].astype(float)
    df["price"] = df["price"].astype(float)
    df["amount"] = df["amount"].astype(float).round(2)
    return df


def normalise_internal(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    df["int_trade_id"] = df["int_trade_id"].astype(str).str.strip()
    df["ext_ref"] = df["ext_ref"].where(df["ext_ref"].notna(), None)
    df["ext_ref"] = df["ext_ref"].map(lambda x: x.strip() if isinstance(x, str) else None)
    df["isin"] = df["isin"].astype(str).str.strip().str.upper()
    df["side"] = df["side"].astype(str).str.strip().str.upper()
    df["currency"] = df["currency"].astype(str).str.strip().str.upper()
    for c in ("trade_date", "settle_date"):
        df[c] = pd.to_datetime(df[c]).dt.date
    df["quantity"] = df["quantity"].astype(float)
    df["price"] = df["price"].astype(float)
    df["amount"] = df["amount"].astype(float).round(2)
    return df
