"""Live data acquisition: Amazon market data (listings, history, reviews) from licensed APIs -- and, only if an
operator enables it, from public pages -- turned into dated snapshots that go through the same pipeline as a
SellerSprite upload. Uploads keep working unchanged; this is a second way in.

    from dip.acquire import status, run_market
    status()                                   # which providers are configured, for which capabilities
    run_market("denture_base", history=True)   # search + details + reviews (+ monthly history backfill)

See base.py (credentials, rate limits, cache, raw landing archive, budget), models.py (what providers return)
and run.py (the market run). Settings: config/platform/acquisition.yaml.
"""

from __future__ import annotations

from dip.acquire.amazon.html import AmazonHTML
from dip.acquire.amazon.keepa import Keepa
from dip.acquire.amazon.paapi import PAAPI
from dip.acquire.amazon.scraper_api import ScraperAPI
from dip.acquire.base import AcquireError, BudgetExceeded, Ledger, NotConfigured, Provider, config

PROVIDERS: dict[str, type[Provider]] = {p.name: p for p in (Keepa, PAAPI, ScraperAPI, AmazonHTML)}

LABELS = {"keepa": "Keepa API (history, sales badges, fees)", "paapi": "Amazon Product Advertising API 5.0",
          "scraper_api": "Amazon data API (search, product, reviews)", "amazon_html": "Direct amazon.com pages (off by default)"}


def provider_for(capability: str, ledger: Ledger | None = None, **kw) -> Provider | None:
    """The first configured provider for a capability, in the order config lists them."""
    for name in config()["capabilities"].get(capability, []):
        cls = PROVIDERS.get(name)
        if cls is not None and capability in cls.capabilities and cls.configured():
            return cls(ledger=ledger, **kw)
    return None


def status() -> dict:
    caps = config()["capabilities"]
    providers = [{"name": n, "label": LABELS.get(n, n), "capabilities": list(c.capabilities), "configured": c.configured(),
                  "missing": c.missing(), "cost_usd_per_request": config()["providers"].get(n, {}).get("cost_usd_per_request")}
                 for n, c in PROVIDERS.items()]
    active = {cap: next((n for n in order if n in PROVIDERS and cap in PROVIDERS[n].capabilities and PROVIDERS[n].configured()), None)
              for cap, order in caps.items()}
    return {"providers": providers, "active": active, "ready": bool(active.get("amazon_search") and active.get("amazon_detail")),
            "direct_html_enabled": bool(config()["direct_html"]["enabled"])}


from dip.acquire.run import run_market  # noqa: E402  (run imports the registry above)

__all__ = ["PROVIDERS", "AcquireError", "BudgetExceeded", "Ledger", "NotConfigured", "provider_for", "run_market", "status"]
