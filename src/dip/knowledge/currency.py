"""Currency normalization (spec 68): every money value is compared in one base currency (USD).

The marketplace of an upload decides its currency (``currency.marketplaces``). Prices and revenues of a
non-USD marketplace are converted with the configured rate (``currency.rates_to_usd``, with its
``rates_as_of`` date). The local values are kept in ``price_local`` / ``revenue_local``. A marketplace
without a configured rate stops the run with a clear message: currencies are never mixed silently, and a
rate is never guessed.
"""

from __future__ import annotations

import pandas as pd

from dip.knowledge import config

MONEY_COLUMNS = ("price", "revenue")


def _cfg() -> dict:
    return config()["currency"]


def currency_of(marketplace: str | None) -> str:
    c = _cfg()
    if not marketplace:
        return c["base"]
    m = str(marketplace).strip().upper()
    if m not in c["marketplaces"]:
        raise ValueError(f"unknown marketplace '{marketplace}': add it to currency.marketplaces in config/platform/knowledge.yaml")
    return c["marketplaces"][m]


def normalize(frame: pd.DataFrame, marketplace: str | None) -> tuple[pd.DataFrame, dict]:
    """(frame in the base currency, a record of the conversion)."""
    c = _cfg()
    cur = currency_of(marketplace)
    if cur == c["base"]:
        return frame, {"currency": cur, "base": c["base"], "rate": 1.0, "converted": False}
    rate = (c.get("rates_to_usd") or {}).get(cur)
    if rate is None:
        raise ValueError(f"no exchange rate for {cur} (marketplace {marketplace}): set currency.rates_to_usd.{cur} and "
                         "currency.rates_as_of in config/platform/knowledge.yaml; currencies are never mixed silently")
    out = frame.copy()
    for col in MONEY_COLUMNS:
        if col in out:
            out[f"{col}_local"] = out[col]
            out[col] = pd.to_numeric(out[col], errors="coerce") * float(rate)
    return out, {"currency": cur, "base": c["base"], "rate": float(rate), "rates_as_of": c.get("rates_as_of"), "converted": True}
