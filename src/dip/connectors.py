"""Connector registry for live sources.

Each connector is configured by environment variables and, when polled,
turns what it fetches into events on the bus -- the same path a manual upload
takes:

    sellersprite_api   DIP_SELLERSPRITE_EXPORT_URL (+ DIP_SELLERSPRITE_MARKET)
                       a SellerSprite export endpoint / file URL -> dataset.available -> processed as a market
    amazon_sp_api      DIP_AMAZON_SP_REFRESH_TOKEN, DIP_AMAZON_SP_CLIENT_ID, DIP_AMAZON_SP_CLIENT_SECRET
                       needs a vendor SDK and an approved developer account; reports what is missing
    supplier_feed      DIP_SUPPLIER_FEED_URL   CSV / JSON list of suppliers -> imported -> supplier.added
    news_feed          DIP_NEWS_FEED_URL       RSS / Atom -> news.item events, matched to markets by text

HTTP(S) and file:// URLs are read with the standard library. Nothing is
generated when a source is not configured: ``poll`` reports "not configured".
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from dip import events


@dataclass
class ConnectorStatus:
    name: str
    configured: bool
    description: str
    missing: list[str]


def _env(name: str) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or None


def _fetch(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 -- URL comes from the operator's configuration
        return r.read()


class Connector:
    name = "base"
    description = ""
    env: tuple[str, ...] = ()

    def status(self) -> ConnectorStatus:
        missing = [e for e in self.env if not _env(e)]
        return ConnectorStatus(self.name, not missing, self.description, missing)

    def poll(self) -> dict:
        st = self.status()
        if not st.configured:
            return {"connector": self.name, "status": "not_configured", "missing": st.missing}
        return self._poll()

    def _poll(self) -> dict:
        raise NotImplementedError


class SellerSpriteExport(Connector):
    name = "sellersprite_api"
    description = "SellerSprite export URL -> processed as a market dataset"
    env = ("DIP_SELLERSPRITE_EXPORT_URL", "DIP_SELLERSPRITE_MARKET")

    def _poll(self) -> dict:
        url, market = _env("DIP_SELLERSPRITE_EXPORT_URL"), _env("DIP_SELLERSPRITE_MARKET")
        data = _fetch(url)
        suffix = Path(url.split("?")[0]).suffix or ".xlsx"
        path = Path(tempfile.mkdtemp(prefix="dip_conn_")) / f"sellersprite{suffix}"
        path.write_bytes(data)
        ev = events.publish("dataset.available", market, path.name, {"path": str(path), "source_url": url},
                            "info", f"connector:{self.name}")
        return {"connector": self.name, "status": "ok", "event_id": ev, "bytes": len(data)}


class AmazonSPAPI(Connector):
    name = "amazon_sp_api"
    description = "Amazon Selling Partner API (requires an approved developer account and a vendor SDK)"
    env = ("DIP_AMAZON_SP_REFRESH_TOKEN", "DIP_AMAZON_SP_CLIENT_ID", "DIP_AMAZON_SP_CLIENT_SECRET")

    def _poll(self) -> dict:
        return {"connector": self.name, "status": "not_implemented",
                "note": "credentials found; install and wire the SP-API client (catalog + sales reports) here -- "
                        "its reports then go through dataset.available like any export"}


class SupplierFeed(Connector):
    name = "supplier_feed"
    description = "Supplier list (CSV or JSON) -> suppliers table"
    env = ("DIP_SUPPLIER_FEED_URL",)

    def _poll(self) -> dict:
        from dip.pipeline.supplier import import_suppliers

        url = _env("DIP_SUPPLIER_FEED_URL")
        raw = _fetch(url)
        text = raw.decode("utf-8-sig")
        df = pd.DataFrame(json.loads(text)) if text.lstrip()[:1] in "[{" else pd.read_csv(io.StringIO(text))
        n = import_suppliers(df, f"connector:{self.name}")
        names = df.iloc[:, 0].astype(str).head(20).tolist() if len(df.columns) else []
        events.publish("supplier.added", None, f"{n} suppliers", {"count": n, "names": names, "source_url": url},
                       "info", f"connector:{self.name}")
        return {"connector": self.name, "status": "ok", "imported": n}


class NewsFeed(Connector):
    name = "news_feed"
    description = "Industry news (RSS/Atom) -> news.item events matched to markets"
    env = ("DIP_NEWS_FEED_URL",)

    def _poll(self) -> dict:
        url = _env("DIP_NEWS_FEED_URL")
        items = parse_feed(_fetch(url))
        ids = [publish_news(it, f"connector:{self.name}") for it in items]
        return {"connector": self.name, "status": "ok", "items": len(ids)}


REGISTRY: dict[str, Connector] = {c.name: c for c in (SellerSpriteExport(), AmazonSPAPI(), SupplierFeed(), NewsFeed())}


def parse_feed(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    out = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        get = lambda name: next((c.text or "" for c in it if c.tag.split("}")[-1] == name), "")  # noqa: E731
        link = get("link") or next((c.get("href", "") for c in it if c.tag.split("}")[-1] == "link"), "")
        out.append({"title": get("title").strip(), "url": link.strip(),
                    "summary": (get("description") or get("summary")).strip()[:2000],
                    "published": (get("pubDate") or get("updated") or get("published")).strip()})
    return out


def match_markets(text: str, min_score: float = 0.12) -> list[dict]:
    """Markets whose products are similar to a piece of text (news, supplier catalogue)."""
    from dip.storage.vectors import get_vector_store

    try:
        hits = get_vector_store().similar_to_text(text, limit=30)
    except Exception:
        return []
    best: dict[str, float] = {}
    for h in hits:
        if h.get("market") and h["score"] >= min_score:
            best[h["market"]] = max(best.get(h["market"], 0.0), h["score"])
    return [{"market": k, "score": round(v, 3)} for k, v in sorted(best.items(), key=lambda kv: -kv[1])[:3]]


def publish_news(item: dict, source: str) -> list[str]:
    """One news.item event per matched market (so owners are alerted), or one unmatched event."""
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    matches = match_markets(text)
    if not matches:
        return [events.publish("news.item", None, item.get("title"), item, "info", source)]
    return [events.publish("news.item", m["market"], item.get("title"), {**item, "match_score": m["score"]}, "notice", source)
            for m in matches]


@events.on("dataset.available")
def _process_available(ev) -> None:
    """A connector delivered a file: process it as the named market (background job)."""
    from dip.pipeline import runner

    p = (ev.payload or {}).get("path")
    if p and ev.market_name and Path(p).exists():
        if os.environ.get("DIP_EVENTS_SYNC"):   # command line: finish before the process exits
            runner.process_dataset(Path(p), ev.market_name, source_name=Path(p).name)
        else:
            runner.run_in_background(source=Path(p), market=ev.market_name, source_name=Path(p).name)
