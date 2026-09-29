"""Snapshot System + Change Detection Foundation (Milestone 16).

take_snapshot() captures product_market_metrics as it exists right now --
called once before a pipeline run starts (capturing the previous run's
ending state) and once after market_calculation finishes (capturing the
new state). detect_changes() diffs two snapshots and writes market_changes
rows: new/removed products, new listings, price movement, sales movement.

Deliberately reads from product_market_metrics rather than recomputing
anything -- this module observes, it never calculates a market metric
itself (that stays Milestone 7's job, src/dmie/market/*).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import duckdb

CHANGE_NEW_PRODUCT = "NEW_PRODUCT"
CHANGE_REMOVED_PRODUCT = "REMOVED_PRODUCT"
CHANGE_NEW_LISTING = "NEW_LISTING"
CHANGE_PRICE = "PRICE_CHANGE"
CHANGE_SALES = "SALES_CHANGE"


def take_snapshot(con: duckdb.DuckDBPyConnection, category_id: str, pipeline_run_id: str | None = None) -> str:
    """Capture the current product_market_metrics for a category. Safe
    to call on a category with no metrics yet (e.g. the very first
    pipeline run ever) -- produces an empty-but-valid baseline snapshot,
    which detect_changes() then correctly reports as "every product is
    new" against."""
    rows = con.execute(
        """
        SELECT product_id, representative_price AS price,
               best_listing_observed_monthly_sales AS monthly_sales,
               observed_monthly_revenue, total_listing_count
        FROM product_market_metrics
        WHERE category_id = ?
        """,
        [category_id],
    ).fetchall()

    snapshot_data = {
        r[0]: {"price": r[1], "monthly_sales": r[2], "observed_monthly_revenue": r[3], "total_listing_count": r[4]}
        for r in rows
    }
    listing_count = con.execute(
        "SELECT COUNT(*) FROM listings WHERE category_id = ?", [category_id]
    ).fetchone()[0]

    snapshot_id = uuid.uuid4().hex
    con.execute(
        """
        INSERT INTO market_snapshots (
            snapshot_id, category_id, pipeline_run_id, taken_at, product_count, listing_count, snapshot_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [snapshot_id, category_id, pipeline_run_id, datetime.now(timezone.utc),
         len(snapshot_data), listing_count, json.dumps(snapshot_data)],
    )
    return snapshot_id


def latest_snapshot(con: duckdb.DuckDBPyConnection, category_id: str) -> dict | None:
    row = con.execute(
        "SELECT * FROM market_snapshots WHERE category_id = ? ORDER BY taken_at DESC LIMIT 1", [category_id]
    ).fetchone()
    if row is None:
        return None
    columns = [d[0] for d in con.description]
    record = dict(zip(columns, row))
    record["snapshot_data"] = json.loads(record["snapshot_data"])
    return record


def detect_changes(
    con: duckdb.DuckDBPyConnection, category_id: str, snapshot_id_before: str, snapshot_id_after: str,
    price_threshold_pct: float = 0.01, sales_threshold_pct: float = 0.01,
) -> list[dict]:
    """Diff two snapshots and persist one market_changes row per detected
    change. Thresholds exist only to filter out floating-point noise
    (e.g. a price of 19.990000000000002 vs 19.99) -- 1% by default, not a
    business-meaningful "significant change" judgment call, which would
    need real product-owner input before being hardcoded here."""
    before = _load_snapshot_data(con, snapshot_id_before)
    after = _load_snapshot_data(con, snapshot_id_after)

    changes: list[dict] = []
    now = datetime.now(timezone.utc)

    for product_id, after_row in after.items():
        if product_id not in before:
            changes.append(_change(category_id, snapshot_id_before, snapshot_id_after,
                                     CHANGE_NEW_PRODUCT, product_id, None, json.dumps(after_row), now))
            continue

        before_row = before[product_id]

        before_listings = before_row.get("total_listing_count") or 0
        after_listings = after_row.get("total_listing_count") or 0
        if after_listings > before_listings:
            changes.append(_change(category_id, snapshot_id_before, snapshot_id_after,
                                     CHANGE_NEW_LISTING, product_id, str(before_listings), str(after_listings), now))

        price_before, price_after = before_row.get("price"), after_row.get("price")
        if price_before is not None and price_after is not None and price_before > 0:
            if abs(price_after - price_before) / price_before > price_threshold_pct:
                changes.append(_change(category_id, snapshot_id_before, snapshot_id_after,
                                         CHANGE_PRICE, product_id, str(price_before), str(price_after), now))

        sales_before, sales_after = before_row.get("monthly_sales"), after_row.get("monthly_sales")
        if sales_before is not None and sales_after is not None and sales_before > 0:
            if abs(sales_after - sales_before) / sales_before > sales_threshold_pct:
                changes.append(_change(category_id, snapshot_id_before, snapshot_id_after,
                                         CHANGE_SALES, product_id, str(sales_before), str(sales_after), now))

    for product_id, before_row in before.items():
        if product_id not in after:
            changes.append(_change(category_id, snapshot_id_before, snapshot_id_after,
                                     CHANGE_REMOVED_PRODUCT, product_id, json.dumps(before_row), None, now))

    if changes:
        con.executemany(
            """
            INSERT INTO market_changes (
                change_id, category_id, snapshot_id_before, snapshot_id_after,
                change_type, entity_id, old_value, new_value, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [[c["change_id"], c["category_id"], c["snapshot_id_before"], c["snapshot_id_after"],
              c["change_type"], c["entity_id"], c["old_value"], c["new_value"], c["detected_at"]]
             for c in changes],
        )
    return changes


def list_changes(con: duckdb.DuckDBPyConnection, category_id: str, limit: int = 200) -> list[dict]:
    rows = con.execute(
        "SELECT * FROM market_changes WHERE category_id = ? ORDER BY detected_at DESC LIMIT ?",
        [category_id, limit],
    ).fetchall()
    columns = [d[0] for d in con.description]
    return [dict(zip(columns, row)) for row in rows]


def _load_snapshot_data(con: duckdb.DuckDBPyConnection, snapshot_id: str) -> dict:
    row = con.execute("SELECT snapshot_data FROM market_snapshots WHERE snapshot_id = ?", [snapshot_id]).fetchone()
    if row is None:
        raise ValueError(f"snapshot not found: {snapshot_id}")
    return json.loads(row[0])


def _change(category_id, snap_before, snap_after, change_type, entity_id, old_value, new_value, now) -> dict:
    return {
        "change_id": uuid.uuid4().hex, "category_id": category_id,
        "snapshot_id_before": snap_before, "snapshot_id_after": snap_after,
        "change_type": change_type, "entity_id": entity_id,
        "old_value": old_value, "new_value": new_value, "detected_at": now,
    }
