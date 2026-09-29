"""Event-driven layer: event log, in-process bus, change detection, alert routing.

    source (pipeline run / connector poll / POST /events)
        -> publish(kind, ...)            stored in the `events` table
        -> subscribers for that kind     (in-process; a failing handler never breaks the publisher)
        -> route_alerts                  notice/important events -> an Alert for every employee
                                         who owns a category of that market

Change detection runs after every processing job: it compares the market's new
state (competitors, segments, listings, trend) with the state before the run
and publishes one event per change -- new competitor, share shift, new launch,
price move, opportunity change, trend change. The run itself already refreshed
the graph and opportunity scores; the events tell the responsible people.

Swapping the in-process bus for a broker (Redis streams, Kafka) only changes
``publish``; handlers and the event schema stay the same.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Callable

import pandas as pd

from dip.storage import business as b

log = logging.getLogger("dip.events")
_handlers: dict[str, list[Callable[[b.Event], None]]] = defaultdict(list)
SEVERITIES = ("info", "notice", "important")



def ops_config() -> dict:
    """config/platform/operations.yaml (thresholds for change events, scheduler, summaries, projects)."""
    import yaml

    from dip.settings import PROJECT_ROOT

    return yaml.safe_load((PROJECT_ROOT / "config" / "platform" / "operations.yaml").read_text(encoding="utf-8"))


# thresholds for change events (config/platform/operations.yaml -> changes; alerts must not be noise)
_C = ops_config()["changes"]
OPPORTUNITY_POINTS = float(_C["opportunity_points"])
HIGH_OPPORTUNITY = float(_C["high_opportunity"])
SHARE_POINTS = float(_C["share_points"])
NEW_BRAND_SHARE = float(_C["new_brand_share"])
PRICE_MOVE = float(_C["price_move"])
MARKET_MOVE = float(_C["market_move"])
LAUNCH_WINDOW_DAYS = int(_C["launch_window_days"])


def on(kind: str):
    """Register a handler for an event kind ('*' = every event)."""
    def deco(fn):
        _handlers[kind].append(fn)
        return fn
    return deco


def publish(kind: str, market: str | None = None, subject: str | None = None, payload: dict | None = None,
            severity: str = "info", source: str = "pipeline") -> str:
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}")
    clean = json.loads(json.dumps(payload or {}, default=lambda x: x.item() if hasattr(x, "item") else str(x)))
    with b.session() as s:
        ev = b.Event(kind=kind, market_name=market, subject=(subject or "")[:500] or None, severity=severity,
                     source=source, payload=clean)
        s.add(ev)
        s.flush()
        event_id = ev.id
    with b.session() as s:
        ev = s.get(b.Event, event_id)
    for fn in _handlers.get(kind, []) + _handlers.get("*", []):
        try:
            fn(ev)
        except Exception:  # a subscriber must never break the publisher
            log.exception("event handler %s failed for %s", getattr(fn, "__name__", fn), kind)
    return event_id


def owners_of(market: str | None) -> list[str]:
    if not market:
        return []
    with b.session() as s:
        rows = (s.query(b.Ownership.employee_id).join(b.Category, b.Category.id == b.Ownership.category_id)
                .filter(b.Category.market_name == market).distinct().all())
    return [r[0] for r in rows]


@on("*")
def route_alerts(ev: b.Event) -> None:
    """notice / important events reach every employee who owns a category of the market."""
    if ev.severity == "info":
        return
    emps = owners_of(ev.market_name)
    if not emps:
        return
    with b.session() as s:
        for e in emps:
            if not s.query(b.Alert).filter_by(event_id=ev.id, employee_id=e).first():
                s.add(b.Alert(event_id=ev.id, employee_id=e))
    from dip.operations import deliver_alert   # important alerts also become messages / e-mail

    deliver_alert(ev, emps)


# ------------------------------------------------------------------ change detection
def snapshot_state(market: str) -> dict:
    """The market's current state, read before a run overwrites it."""
    from dip.storage import lake

    st: dict = {}
    if lake.has_curated("competitors", market):
        st["competitors"] = lake.read_curated("competitors", market, columns=["brand", "share", "median_price", "position"])
    if lake.has_curated("segments", market):
        cols = lake.curated_columns("segments", market)
        st["segments"] = lake.read_curated("segments", market,
                                           columns=[c for c in ["segment_id", "segment_label", "opportunity_score", "top_terms"] if c in cols])
    if lake.has_curated("listings", market):
        st["listing_ids"] = set(lake.read_curated("listings", market, columns=["id"])["id"])
    with b.session() as s:
        m = s.get(b.Market, market)
        st["trend"] = ((m.summary or {}).get("trend") or {}).get("trend") if m else None
        st["category"] = dict((m.summary or {}).get("category") or {}) if m else {}
        st["exists"] = m is not None
    return st


def _segment_key(row) -> str:
    """Segments are rediscovered each run; match them by their label terms, not their ids."""
    return str(row.get("segment_label") or "")


def detect_changes(market: str, before: dict, competitors: pd.DataFrame, segments: pd.DataFrame,
                   listings: pd.DataFrame, trend: str | None, as_of: pd.Timestamp,
                   category: dict | None = None) -> list[dict]:
    """Events (dicts) describing what changed between ``before`` and the new state."""
    out: list[dict] = []
    if not before.get("exists"):
        out.append({"kind": "market.created", "subject": market, "severity": "notice",
                    "payload": {"segments": int(len(segments)), "brands": int(len(competitors))}})
        return out
    # market-level movement: observed revenue and units (same coverage basis only)
    old_c, new_c = before.get("category") or {}, category or {}
    for key, label in (("monthly_revenue", "revenue"), ("monthly_sales", "units")):
        a, z = old_c.get(key), new_c.get(key)
        if a and z is not None and a > 0:
            ch = float(z / a - 1)
            if abs(ch) >= MARKET_MOVE:
                cov_a, cov_z = old_c.get("sales_coverage"), new_c.get("sales_coverage")
                out.append({"kind": "market.size_change", "subject": f"{market} {label} {ch:+.0%}",
                            "severity": "important" if abs(ch) >= 2 * MARKET_MOVE else "notice",
                            "payload": {"metric": key, "before": float(a), "after": float(z), "change": round(ch, 4),
                                        "coverage_before": cov_a, "coverage_after": cov_z,
                                        "note": "observed values; compare coverage before reading it as demand change"}})
    pc = before.get("competitors")
    if pc is not None and len(competitors):
        prev = pc.set_index("brand")
        for r in competitors.itertuples():
            if r.brand not in prev.index:
                if r.share >= NEW_BRAND_SHARE:
                    out.append({"kind": "competitor.new_brand", "subject": r.brand, "severity": "important" if r.share >= 0.05 else "notice",
                                "payload": {"brand": r.brand, "share": r.share, "position": r.position, "products": r.products}})
                continue
            p = prev.loc[r.brand]
            ds = float(r.share - p["share"])
            if abs(ds) >= SHARE_POINTS:
                out.append({"kind": "competitor.share_change", "subject": r.brand, "severity": "notice",
                            "payload": {"brand": r.brand, "share_before": float(p["share"]), "share_after": r.share, "change": round(ds, 4)}})
            if pd.notna(p["median_price"]) and pd.notna(r.median_price) and p["median_price"] > 0:
                mv = float(r.median_price / p["median_price"] - 1)
                if abs(mv) >= PRICE_MOVE:
                    out.append({"kind": "competitor.price_change", "subject": r.brand, "severity": "notice",
                                "payload": {"brand": r.brand, "price_before": float(p["median_price"]),
                                            "price_after": float(r.median_price), "change": round(mv, 4)}})
            if p["position"] != r.position and "Leader" in (p["position"], r.position):
                out.append({"kind": "competitor.position_change", "subject": r.brand, "severity": "important",
                            "payload": {"brand": r.brand, "before": p["position"], "after": r.position}})
    ps = before.get("segments")
    if ps is not None and len(segments):
        prev = {_segment_key(r): r for r in ps.to_dict("records")}
        for r in segments.to_dict("records"):
            old = prev.get(_segment_key(r))
            new = float(r.get("opportunity_score") or 0)
            if old is None:
                if new >= HIGH_OPPORTUNITY:
                    out.append({"kind": "opportunity.new_segment", "subject": r["segment_label"], "severity": "important",
                                "payload": {"segment_id": r["segment_id"], "opportunity_score": new}})
                continue
            was = float(old.get("opportunity_score") or 0)
            crossed = (was < HIGH_OPPORTUNITY) != (new < HIGH_OPPORTUNITY)
            if abs(new - was) >= OPPORTUNITY_POINTS or crossed:
                out.append({"kind": "opportunity.change", "subject": r["segment_label"],
                            "severity": "important" if crossed and new >= HIGH_OPPORTUNITY else "notice",
                            "payload": {"segment_id": r["segment_id"], "before": round(was, 1), "after": round(new, 1)}})
    ids = before.get("listing_ids")
    if ids is not None and len(listings):
        new_ids = listings.loc[~listings["id"].isin(ids)]
        if len(new_ids):
            ld = pd.to_datetime(new_ids.get("launch_date"), errors="coerce")
            launched = new_ids[(as_of - ld).dt.days.between(0, LAUNCH_WINDOW_DAYS)] if ld is not None else new_ids.iloc[0:0]
            for r in launched.head(50).itertuples():
                out.append({"kind": "product.new_launch", "subject": str(r.title)[:200], "severity": "notice",
                            "payload": {"listing": r.id, "brand": r.brand, "price": r.price, "launch_date": str(getattr(r, "launch_date", ""))}})
            out.append({"kind": "listings.new", "subject": f"{len(new_ids)} new listings", "severity": "info",
                        "payload": {"count": int(len(new_ids)), "sample": new_ids["id"].head(20).tolist()}})
    if before.get("trend") and trend and before["trend"] != trend:
        out.append({"kind": "trend.change", "subject": market, "severity": "important",
                    "payload": {"before": before["trend"], "after": trend}})
    return out


def publish_changes(market: str, changes: list[dict], dataset_id: str | None, job_id: str | None) -> int:
    for c in changes:
        publish(c["kind"], market, c.get("subject"), {**c.get("payload", {}), "dataset_id": dataset_id, "job_id": job_id},
                c.get("severity", "info"), "pipeline")
    return len(changes)
