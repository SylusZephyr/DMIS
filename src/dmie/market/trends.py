"""Trend / Momentum Engine: reads the real historical time series already
captured by dmie.database.snapshots (market_snapshots, one row per
pipeline run) and computes genuine, evidence-based growth metrics --
never a single-snapshot guess about direction.

Deliberately separate from dmie.database.snapshots: that module owns
capturing and diffing two adjacent snapshots (market_changes' discrete
NEW_PRODUCT/PRICE_CHANGE/etc. events). This module reads the same
snapshot_data across ALL available snapshots for a category and computes
continuous growth rates -- a different question ("is this moving up
overall, and how confidently can we say that") than "what changed since
last time."

Per PRINCIPLES.md ("AI is for semantic judgment" / "deterministic
calculations") and the architecture directive's Trend Engine: pure
arithmetic over real observed numbers, no AI involved anywhere here.
"""

from __future__ import annotations

import json

import duckdb

# A trend claim needs at least this many real snapshots to be reported
# as anything other than "insufficient data" -- 2 points can only show
# a single before/after delta, not a genuine trend (could be noise).
MIN_SNAPSHOTS_FOR_TREND = 3


def list_snapshot_series(con: duckdb.DuckDBPyConnection, category_id: str) -> list[dict]:
    """Every real snapshot for `category_id`, oldest first, with
    snapshot_data parsed from JSON."""
    rows = con.execute(
        "SELECT snapshot_id, taken_at, snapshot_data FROM market_snapshots "
        "WHERE category_id = ? ORDER BY taken_at ASC",
        [category_id],
    ).fetchall()
    return [{"snapshot_id": r[0], "taken_at": r[1], "snapshot_data": json.loads(r[2])} for r in rows]


def _growth_pct(first: float | None, last: float | None) -> float | None:
    """None when either endpoint is missing or the base is zero --
    never a fabricated 0%/infinite growth."""
    if first is None or last is None or first == 0:
        return None
    return (last - first) / first * 100.0


def compute_category_market_trend(con: duckdb.DuckDBPyConnection, category_id: str) -> dict:
    """Category-level time series + growth metrics, aggregated across
    every product present in each snapshot. `status` is explicit about
    data sufficiency (PRINCIPLES.md "None never a guess"):

    - "insufficient_data": fewer than MIN_SNAPSHOTS_FOR_TREND real
      snapshots exist -- no trend can be honestly claimed yet.
    - "ok": real growth metrics computed from the real time series.

    `time_span_hours` is always reported alongside any growth number --
    a 9-snapshot series spanning 22 hours is real data, but must never
    be presented as if it were a multi-month trend."""
    series = list_snapshot_series(con, category_id)

    points = []
    for snap in series:
        data = snap["snapshot_data"]
        sales_values = [p.get("monthly_sales") for p in data.values() if p.get("monthly_sales") is not None]
        revenue_values = [p.get("observed_monthly_revenue") for p in data.values() if p.get("observed_monthly_revenue") is not None]
        listing_counts = [p.get("total_listing_count") or 0 for p in data.values()]
        points.append({
            "snapshot_id": snap["snapshot_id"],
            "taken_at": snap["taken_at"],
            "product_count": len(data),
            "total_listing_count": sum(listing_counts),
            "total_observed_monthly_sales": sum(sales_values) if sales_values else None,
            "total_observed_monthly_revenue": sum(revenue_values) if revenue_values else None,
        })

    if len(points) < MIN_SNAPSHOTS_FOR_TREND:
        return {
            "category_id": category_id,
            "status": "insufficient_data",
            "reason": f"only {len(points)} real snapshot(s) exist; need at least {MIN_SNAPSHOTS_FOR_TREND} to report a trend",
            "snapshot_count": len(points),
            "points": points,
        }

    first, last = points[0], points[-1]
    span_seconds = (last["taken_at"] - first["taken_at"]).total_seconds()

    return {
        "category_id": category_id,
        "status": "ok",
        "snapshot_count": len(points),
        "time_span_hours": round(span_seconds / 3600, 2),
        "points": points,
        "product_count_growth_pct": _growth_pct(first["product_count"], last["product_count"]),
        "listing_count_growth_pct": _growth_pct(first["total_listing_count"], last["total_listing_count"]),
        "sales_growth_pct": _growth_pct(first["total_observed_monthly_sales"], last["total_observed_monthly_sales"]),
        "revenue_growth_pct": _growth_pct(first["total_observed_monthly_revenue"], last["total_observed_monthly_revenue"]),
    }


def compute_product_trend(con: duckdb.DuckDBPyConnection, category_id: str, product_id: str) -> dict:
    """Same idea, scoped to one product -- only counts snapshots where
    this specific product_id is actually present (a product that didn't
    exist yet, or was removed, correctly has fewer data points than the
    category as a whole, never backfilled with an invented value)."""
    series = list_snapshot_series(con, category_id)
    points = []
    for snap in series:
        row = snap["snapshot_data"].get(product_id)
        if row is None:
            continue
        points.append({
            "snapshot_id": snap["snapshot_id"],
            "taken_at": snap["taken_at"],
            "price": row.get("price"),
            "monthly_sales": row.get("monthly_sales"),
            "observed_monthly_revenue": row.get("observed_monthly_revenue"),
            "total_listing_count": row.get("total_listing_count"),
        })

    if len(points) < MIN_SNAPSHOTS_FOR_TREND:
        return {
            "category_id": category_id,
            "product_id": product_id,
            "status": "insufficient_data",
            "reason": f"only {len(points)} real snapshot(s) contain this product; need at least {MIN_SNAPSHOTS_FOR_TREND}",
            "snapshot_count": len(points),
            "points": points,
        }

    first, last = points[0], points[-1]
    span_seconds = (last["taken_at"] - first["taken_at"]).total_seconds()

    return {
        "category_id": category_id,
        "product_id": product_id,
        "status": "ok",
        "snapshot_count": len(points),
        "time_span_hours": round(span_seconds / 3600, 2),
        "points": points,
        "price_growth_pct": _growth_pct(first["price"], last["price"]),
        "sales_growth_pct": _growth_pct(first["monthly_sales"], last["monthly_sales"]),
        "revenue_growth_pct": _growth_pct(first["observed_monthly_revenue"], last["observed_monthly_revenue"]),
    }
