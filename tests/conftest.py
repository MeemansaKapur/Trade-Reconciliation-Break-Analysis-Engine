import datetime as dt

import pandas as pd
import pytest

from recon.common import load_rules
from recon.generate import generate_days
from recon.normalise import normalise_external, normalise_internal

RUN_DATE = dt.date(2026, 11, 20)   # Friday
T1 = dt.date(2026, 11, 23)         # Monday (T+1 business day)


def trade(i, ext_ref=None, isin="INE002A01018", side="BUY", qty=100.0, price=2500.0, amount=None, ccy="INR",
          settle=T1, cpty="CP01", account="EQ-BOOK-01"):
    return {"int_trade_id": f"INT-T-{i:04d}", "ext_ref": ext_ref, "isin": isin, "side": side, "quantity": qty,
            "price": price, "amount": round(qty * price, 2) if amount is None else amount, "currency": ccy,
            "trade_date": RUN_DATE, "settle_date": settle, "counterparty": cpty, "account": account}


def ext(e, isin="INE002A01018", side="BUY", qty=100.0, price=2500.0, amount=None, ccy="INR", settle=T1,
        cpty="CP01", account="EQ-BOOK-01"):
    return {"ext_trade_id": f"CST-T-{e:04d}", "isin": isin, "side": side, "quantity": qty, "price": price,
            "amount": round(qty * price, 2) if amount is None else amount, "currency": ccy, "trade_date": RUN_DATE,
            "settle_date": settle, "counterparty": cpty, "account": account}


def frames(ints, exts):
    return pd.DataFrame(ints), pd.DataFrame(exts)


@pytest.fixture(scope="session")
def small_day():
    day = generate_days(RUN_DATE, 5_000, 123, 1)[0]
    return day, normalise_internal(day["internal"]), normalise_external(day["external_raw"])


@pytest.fixture
def rules():
    return load_rules("v1.3")
