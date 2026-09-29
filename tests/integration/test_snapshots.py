"""Milestone 16 -- Snapshot System + Change Detection Foundation.
take_snapshot() captures product_market_metrics as a point-in-time JSON
blob; detect_changes() diffs two snapshots into market_changes rows.
Both are pure observation -- nothing here recomputes a market metric,
only reads what src/dmie/market/* already wrote to product_market_metrics.
"""

from datetime import datetime, timezone

import duckdb
import pytest

from dmie.database.connection import PROJECT_ROOT
from dmie.database.snapshots import (
    CHANGE_NEW_LISTING,
    CHANGE_NEW_PRODUCT,
    CHANGE_PRICE,
    CHANGE_REMOVED_PRODUCT,
    CHANGE_SALES,
    detect_changes,
    latest_snapshot,
    list_changes,
    take_snapshot,
)

SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"


@pytest.fixture
def con():
    connection = duckdb.connect(":memory:")
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield connection
    connection.close()


def _seed_product_metrics(con, rows: list[dict]) -> None:
    now = datetime.now(timezone.utc)
    for row in rows:
        con.execute(
            """
            INSERT INTO product_market_metrics (
                product_id, category_id, total_listing_count, listings_with_price_data,
                listings_with_sales_data, best_listing_id, best_listing_observed_monthly_sales,
                best_listing_annualized_observed_sales, observed_monthly_revenue,
                annualized_observed_revenue, min_price, max_price, median_price,
                representative_price, rating, review_count, calculated_at
            ) VALUES (?, ?, ?, 1, 1, ?, ?, NULL, ?, NULL, ?, ?, ?, ?, NULL, NULL, ?)
            """,
            [row["product_id"], row["category_id"], row.get("total_listing_count", 1),
             row["product_id"] + "_listing", row.get("monthly_sales"), row.get("observed_monthly_revenue"),
             row.get("price"), row.get("price"), row.get("price"), row.get("price"), now],
        )


def _seed_listings(con, category_id: str, count: int) -> None:
    now = datetime.now(timezone.utc)
    for i in range(count):
        con.execute(
            "INSERT INTO listings (listing_id, asin, category_id, title, created_at) VALUES (?, ?, ?, ?, ?)",
            [f"listing_{i}", f"ASIN{i}", category_id, f"Product {i}", now],
        )


# --- take_snapshot ---

def test_take_snapshot_of_empty_category_is_a_valid_empty_baseline(con):
    snapshot_id = take_snapshot(con, "denture_base")
    snap = latest_snapshot(con, "denture_base")
    assert snap["snapshot_id"] == snapshot_id
    assert snap["product_count"] == 0
    assert snap["listing_count"] == 0
    assert snap["snapshot_data"] == {}


def test_take_snapshot_captures_real_product_metrics(con):
    _seed_product_metrics(con, [
        {"product_id": "p1", "category_id": "denture_base", "price": 19.99, "monthly_sales": 100,
         "observed_monthly_revenue": 1999.0, "total_listing_count": 2},
    ])
    _seed_listings(con, "denture_base", 2)

    snapshot_id = take_snapshot(con, "denture_base", pipeline_run_id="run-123")
    snap = latest_snapshot(con, "denture_base")
    assert snap["snapshot_id"] == snapshot_id
    assert snap["pipeline_run_id"] == "run-123"
    assert snap["product_count"] == 1
    assert snap["listing_count"] == 2
    assert snap["snapshot_data"]["p1"]["price"] == 19.99
    assert snap["snapshot_data"]["p1"]["monthly_sales"] == 100


def test_latest_snapshot_returns_the_newest_one(con):
    take_snapshot(con, "denture_base")
    _seed_product_metrics(con, [{"product_id": "p1", "category_id": "denture_base", "price": 10.0}])
    newest_id = take_snapshot(con, "denture_base")
    assert latest_snapshot(con, "denture_base")["snapshot_id"] == newest_id


def test_latest_snapshot_returns_none_for_a_category_with_no_snapshots(con):
    assert latest_snapshot(con, "denture_base") is None


# --- detect_changes ---

def test_detect_changes_flags_a_new_product(con):
    before_id = take_snapshot(con, "denture_base")
    _seed_product_metrics(con, [{"product_id": "p1", "category_id": "denture_base", "price": 19.99}])
    after_id = take_snapshot(con, "denture_base")

    changes = detect_changes(con, "denture_base", before_id, after_id)
    assert len(changes) == 1
    assert changes[0]["change_type"] == CHANGE_NEW_PRODUCT
    assert changes[0]["entity_id"] == "p1"


def test_detect_changes_flags_a_removed_product(con):
    _seed_product_metrics(con, [{"product_id": "p1", "category_id": "denture_base", "price": 19.99}])
    before_id = take_snapshot(con, "denture_base")
    con.execute("DELETE FROM product_market_metrics WHERE product_id = 'p1'")
    after_id = take_snapshot(con, "denture_base")

    changes = detect_changes(con, "denture_base", before_id, after_id)
    assert len(changes) == 1
    assert changes[0]["change_type"] == CHANGE_REMOVED_PRODUCT
    assert changes[0]["entity_id"] == "p1"


def test_detect_changes_flags_a_price_movement_above_threshold(con):
    _seed_product_metrics(con, [{"product_id": "p1", "category_id": "denture_base", "price": 10.00}])
    before_id = take_snapshot(con, "denture_base")
    con.execute("UPDATE product_market_metrics SET representative_price = 15.00 WHERE product_id = 'p1'")
    after_id = take_snapshot(con, "denture_base")

    changes = detect_changes(con, "denture_base", before_id, after_id)
    price_changes = [c for c in changes if c["change_type"] == CHANGE_PRICE]
    assert len(price_changes) == 1
    assert price_changes[0]["old_value"] == "10.0"
    assert price_changes[0]["new_value"] == "15.0"


def test_detect_changes_ignores_price_movement_within_threshold(con):
    _seed_product_metrics(con, [{"product_id": "p1", "category_id": "denture_base", "price": 10.00}])
    before_id = take_snapshot(con, "denture_base")
    con.execute("UPDATE product_market_metrics SET representative_price = 10.001 WHERE product_id = 'p1'")
    after_id = take_snapshot(con, "denture_base")

    changes = detect_changes(con, "denture_base", before_id, after_id)
    assert not any(c["change_type"] == CHANGE_PRICE for c in changes)


def test_detect_changes_flags_a_sales_movement(con):
    _seed_product_metrics(con, [{"product_id": "p1", "category_id": "denture_base", "price": 10.0, "monthly_sales": 50}])
    before_id = take_snapshot(con, "denture_base")
    con.execute("UPDATE product_market_metrics SET best_listing_observed_monthly_sales = 200 WHERE product_id = 'p1'")
    after_id = take_snapshot(con, "denture_base")

    changes = detect_changes(con, "denture_base", before_id, after_id)
    sales_changes = [c for c in changes if c["change_type"] == CHANGE_SALES]
    assert len(sales_changes) == 1
    assert sales_changes[0]["old_value"] == "50.0"
    assert sales_changes[0]["new_value"] == "200.0"


def test_detect_changes_flags_a_new_listing(con):
    _seed_product_metrics(con, [{"product_id": "p1", "category_id": "denture_base", "price": 10.0, "total_listing_count": 1}])
    before_id = take_snapshot(con, "denture_base")
    con.execute("UPDATE product_market_metrics SET total_listing_count = 2 WHERE product_id = 'p1'")
    after_id = take_snapshot(con, "denture_base")

    changes = detect_changes(con, "denture_base", before_id, after_id)
    listing_changes = [c for c in changes if c["change_type"] == CHANGE_NEW_LISTING]
    assert len(listing_changes) == 1
    assert listing_changes[0]["old_value"] == "1"
    assert listing_changes[0]["new_value"] == "2"


def test_detect_changes_finds_nothing_for_two_identical_snapshots(con):
    _seed_product_metrics(con, [{"product_id": "p1", "category_id": "denture_base", "price": 19.99, "monthly_sales": 100}])
    before_id = take_snapshot(con, "denture_base")
    after_id = take_snapshot(con, "denture_base")

    changes = detect_changes(con, "denture_base", before_id, after_id)
    assert changes == []


def test_detect_changes_persists_rows_queryable_via_list_changes(con):
    before_id = take_snapshot(con, "denture_base")
    _seed_product_metrics(con, [{"product_id": "p1", "category_id": "denture_base", "price": 19.99}])
    after_id = take_snapshot(con, "denture_base")
    detect_changes(con, "denture_base", before_id, after_id)

    persisted = list_changes(con, "denture_base")
    assert len(persisted) == 1
    assert persisted[0]["change_type"] == CHANGE_NEW_PRODUCT
    assert persisted[0]["snapshot_id_before"] == before_id
    assert persisted[0]["snapshot_id_after"] == after_id


def test_detect_changes_raises_for_an_unknown_snapshot_id(con):
    valid_id = take_snapshot(con, "denture_base")
    with pytest.raises(ValueError, match="snapshot not found"):
        detect_changes(con, "denture_base", valid_id, "does-not-exist")
