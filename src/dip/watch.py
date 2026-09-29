"""Competitor watchlist: listings someone chose to follow, observed more often than whole markets, with an alert when
something that matters changes (price, sales badge tier or estimate, best-sellers rank, rating, review count).

Two kinds of observation, one history:

* **snapshot** -- every processed snapshot of the market (a SellerSprite upload or a live run) already records the
  listing's price, rating and sales in the lake's ``observation_history``; nothing is copied.
* **live** -- ``refresh()`` asks the configured Amazon detail provider (src/dip/acquire) for just the watched ASINs and
  stores the answer in ``watch_observations``. It runs on the scheduler's cadence when a provider is set up.

Changes are computed here, deterministically, between the two latest observations of the same metric, against the
thresholds in ``config/platform/acquisition.yaml`` (``watchlist.alerts``), and published as ``watch.change`` events
(which reach category owners as alerts). Settings: ``watchlist`` in config/platform/acquisition.yaml.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from dip.storage import business as b

log = logging.getLogger(__name__)

METRICS = ("price", "sales", "bsr", "rating", "reviews")


def config() -> dict:
    from dip.acquire.base import config as acq

    return acq()["watchlist"]


def _ladder() -> set[float]:
    from dip.metrics import config as mcfg

    return {float(x) for x in mcfg()["sales_observation"]["badge_ladder"]}


def _asin(v: str) -> str:
    a = (v or "").strip().upper()
    if not (len(a) == 10 and a.isalnum()):
        raise ValueError(f"'{v}' is not an ASIN (10 letters and digits)")
    return a


# ---------------------------------------------------------------- the list
def add(asin: str, market: str | None = None, label: str | None = None, note: str | None = None,
        by: str | None = None) -> dict:
    a = _asin(asin)
    with b.session() as s:
        have = s.query(b.WatchItem).filter_by(asin=a, market_name=market).first()
        if have is not None:
            have.active = True
            if label:
                have.label = label
            if note:
                have.note = note
            return b.row_dict(have)
        if s.query(b.WatchItem).filter_by(active=True).count() >= int(config()["max_items"]):
            raise ValueError(f"the watchlist holds at most {config()['max_items']} listings (watchlist.max_items)")
        it = b.WatchItem(asin=a, market_name=market, label=label or _title(a, market), note=note, added_by=by)
        s.add(it)
        s.flush()
        out = b.row_dict(it)
    from dip import audit
    audit.record("watch.add", by, "watch_items", out["id"], {"asin": a, "market": market})
    return out


def remove(item_id: str, by: str | None = None) -> bool:
    with b.session() as s:
        it = s.get(b.WatchItem, item_id)
        if it is None:
            return False
        it.active = False
    from dip import audit
    audit.record("watch.remove", by, "watch_items", item_id, {})
    return True


def _title(asin: str, market: str | None) -> str | None:
    from dip.storage import lake

    if not market or not lake.has_curated("listings", market):
        return None
    df = lake.read_curated("listings", market, columns=["id", "title"], where="id = ?", params=[asin])
    return str(df["title"].iat[0])[:500] if len(df) and pd.notna(df["title"].iat[0]) else None


# ---------------------------------------------------------------- history
def _snapshot_points(asin: str, market: str | None) -> list[dict]:
    from dip.storage import lake

    if not market or not lake.has_curated("observation_history", market):
        return []
    h = lake.read_curated("observation_history", market, where="entity_type = 'listing' AND entity_id = ?", params=[asin])
    if h.empty:
        return []
    h = h[h["metric"].isin(METRICS)]
    h = h.assign(value=pd.to_numeric(h["value"], errors="coerce"))
    wide = h.pivot_table(index="observed_at", columns="metric", values="value", aggfunc="last")
    src = h.groupby("observed_at")["source"].last()
    out = []
    for when, row in wide.sort_index().iterrows():
        p: dict = {"observed_at": str(when)[:10], "source": f"snapshot:{src.get(when) or 'upload'}"}
        p.update({m: (float(row[m]) if m in row and pd.notna(row[m]) else None) for m in METRICS})
        out.append(p)
    return out


def _live_points(asin: str) -> list[dict]:
    with b.session() as s:
        rows = s.query(b.WatchObservation).filter_by(asin=asin).order_by(b.WatchObservation.observed_at).all()
        return [{"observed_at": r.observed_at.isoformat(), "source": f"live:{r.source}",
                 **{m: getattr(r, m) for m in METRICS}} for r in rows]


def history(asin: str, market: str | None = None) -> list[dict]:
    """Every observation of the listing, oldest first (snapshots of the market and live observations)."""
    pts = _snapshot_points(asin, market) + _live_points(asin)
    return sorted(pts, key=lambda p: p["observed_at"])


# ---------------------------------------------------------------- changes (deterministic)
def _last_two(points: list[dict], metric: str) -> tuple[dict, dict] | None:
    seen = [p for p in points if p.get(metric) is not None]
    return (seen[-2], seen[-1]) if len(seen) >= 2 else None


def changes(points: list[dict]) -> list[dict]:
    """What moved between the two latest observations of each metric, when it moved enough to report."""
    a = config()["alerts"]
    ladder = _ladder()
    out: list[dict] = []
    for m in METRICS:
        pair = _last_two(points, m)
        if pair is None:
            continue
        p0, p1 = pair
        v0, v1 = float(p0[m]), float(p1[m])
        if v0 == v1:
            continue
        rel = (v1 - v0) / v0 if v0 else None
        hit = False
        if m == "price":
            hit = rel is not None and abs(rel) >= float(a["price_move"])
        elif m == "sales":
            badges = v0 in ladder and v1 in ladder
            hit = badges or (rel is not None and abs(rel) >= float(a["sales_move"]))
        elif m == "bsr":
            hit = rel is not None and abs(rel) >= float(a["bsr_move"])
        elif m == "rating":
            hit = v0 - v1 >= float(a["rating_drop"])
        elif m == "reviews":
            hit = rel is not None and rel >= float(a["review_surge"])
        if hit:
            out.append({"metric": m, "from": v0, "to": v1, "change": round(rel, 4) if rel is not None else None,
                        "from_at": p0["observed_at"], "to_at": p1["observed_at"], "source": p1["source"],
                        "badge_tier": m == "sales" and v0 in ladder and v1 in ladder})
    return out


def _severity(ch: list[dict]) -> str:
    deep = float(config()["important_price_drop"])
    return "important" if any(c["metric"] == "price" and (c["change"] or 0) <= -deep for c in ch) else "notice"


def _describe(c: dict) -> str:
    if c["metric"] == "price":
        return f"price ${c['from']:.2f} -> ${c['to']:.2f} ({c['change']:+.0%})"
    if c["metric"] == "sales":
        return (f"sales badge {c['from']:.0f}+ -> {c['to']:.0f}+" if c["badge_tier"]
                else f"sales {c['from']:.0f} -> {c['to']:.0f}/month ({c['change']:+.0%})")
    if c["metric"] == "bsr":
        return f"best-sellers rank {c['from']:.0f} -> {c['to']:.0f}"
    if c["metric"] == "rating":
        return f"rating {c['from']:.1f} -> {c['to']:.1f}"
    return f"reviews {c['from']:.0f} -> {c['to']:.0f} ({c['change']:+.0%})"


def _publish(it: b.WatchItem, ch: list[dict]) -> str | None:
    """One event per item and observation (re-processing the same snapshot does not repeat it)."""
    if not ch:
        return None
    from dip import events

    key = f"{it.asin}|{it.market_name}|{max(c['to_at'] for c in ch)}"
    with b.session() as s:
        recent = s.query(b.Event).filter(b.Event.kind == "watch.change").order_by(b.Event.created_at.desc()).limit(500).all()
        if any((e.payload or {}).get("key") == key for e in recent):
            return None
    subject = f"{it.label or it.asin}: " + "; ".join(_describe(c) for c in ch)
    return events.publish("watch.change", it.market_name, subject,
                          {"key": key, "asin": it.asin, "watch_id": it.id, "changes": ch}, severity=_severity(ch), source="watchlist")


# ---------------------------------------------------------------- what the page shows
def items(market: str | None = None, points: int = 24) -> list[dict]:
    with b.session() as s:
        q = s.query(b.WatchItem).filter_by(active=True)
        if market:
            q = q.filter(b.WatchItem.market_name == market)
        rows = q.order_by(b.WatchItem.created_at.desc()).all()
        its = [(b.row_dict(r), r.asin, r.market_name) for r in rows]
    out = []
    for d, asin, m in its:
        pts = history(asin, m)
        latest = {k: next((p[k] for p in reversed(pts) if p.get(k) is not None), None) for k in METRICS}
        out.append({**d, "latest": latest, "observed_at": pts[-1]["observed_at"] if pts else None,
                    "observations": len(pts), "changes": changes(pts), "points": pts[-points:]})
    return out


# ---------------------------------------------------------------- observing
def after_processing(market: str) -> int:
    """After a snapshot of the market was processed: report what moved for the market's watched listings."""
    with b.session() as s:
        its = s.query(b.WatchItem).filter_by(market_name=market, active=True).all()
        s.expunge_all()
    n = 0
    for it in its:
        if _publish(it, changes(history(it.asin, it.market_name))):
            n += 1
    return n


def due(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    with b.session() as s:
        last = (s.query(b.WatchItem.last_checked_at).filter(b.WatchItem.active.is_(True))
                .order_by(b.WatchItem.last_checked_at.asc().nullsfirst()).first())
        if last is None:
            return False                                 # nothing to watch
    t = last[0]
    if t is None:
        return True
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return now - t >= timedelta(hours=float(config()["every_hours"]))


def refresh(asins: list[str] | None = None, ledger=None, provider_kw: dict | None = None, by: str | None = None) -> dict:
    """Observe watched listings now with the configured Amazon detail provider; report what changed."""
    from dip.acquire import Ledger, provider_for
    from dip.acquire.base import AcquireError

    ledger = ledger or Ledger(budget_usd=float(config()["budget_usd_per_run"]))
    with b.session() as s:
        q = s.query(b.WatchItem).filter_by(active=True)
        if asins:
            q = q.filter(b.WatchItem.asin.in_([_asin(a) for a in asins]))
        its = q.all()
        s.expunge_all()
    with b.session() as s:
        run = b.AcquisitionRun(kind="watchlist", market_name=None, status="running",
                               params={"asins": len({i.asin for i in its}), "by": by})
        s.add(run)
        s.flush()
        run_id = run.id
    prov = provider_for("amazon_detail", ledger=ledger, **(provider_kw or {}))
    status, error, observed, events_n = "done", None, 0, 0
    if prov is None:
        status, error = "failed", "no Amazon detail provider is configured (see Live data & marketplaces)"
    elif its:
        unique = sorted({i.asin for i in its})
        got: dict = {}
        try:
            for k in range(0, len(unique), 20):
                for li in prov.details(unique[k:k + 20]):
                    got[li.asin] = li
        except AcquireError as exc:          # budget or provider failure: keep what was fetched, say so
            status, error = "partial", str(exc)
        now = datetime.now(timezone.utc)
        with b.session() as s:
            for a, li in got.items():
                s.add(b.WatchObservation(asin=a, observed_at=now, title=li.title, price=li.price, sales=li.sales, bsr=li.bsr,
                                         rating=li.rating, reviews=li.reviews, source=li.source or prov.name, run_id=run_id))
                observed += 1
            for it in s.query(b.WatchItem).filter(b.WatchItem.asin.in_(list(got))).all():
                it.last_checked_at = now
        for it in its:
            if it.asin in got and _publish(it, changes(history(it.asin, it.market_name))):
                events_n += 1
        if not got and status == "done":
            status, error = "failed", "the provider returned none of the watched listings"
    result = {"listings": observed, "watched": len({i.asin for i in its}), "events": events_n}
    with b.session() as s:
        r = s.get(b.AcquisitionRun, run_id)
        r.status, r.error, r.result, r.ledger = status, error, result, ledger.to_dict()
        r.providers = {"amazon_detail": prov.name if prov else None}
        r.finished_at = datetime.now(timezone.utc)
    return {"id": run_id, "status": status, "error": error, **result, "ledger": ledger.to_dict()}
