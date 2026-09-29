"""Trend/Momentum Engine (architecture directive's Trend Engine):
tested against a real isolated in-memory schema, same pattern as every
other repository-adjacent test.
"""

import json
from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from dmie.database.connection import PROJECT_ROOT
from dmie.market.trends import (
    MIN_SNAPSHOTS_FOR_TREND,
    compute_category_market_trend,
    compute_product_trend,
)

SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"


@pytest.fixture
def con():
    connection = duckdb.connect(":memory:")
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield connection
    connection.close()


def _insert_snapshot(con, category_id, hours_ago, snapshot_data):
    taken_at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    con.execute(
        "INSERT INTO market_snapshots (snapshot_id, category_id, pipeline_run_id, taken_at, "
        "product_count, listing_count, snapshot_data) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [f"snap_{hours_ago}", category_id, None, taken_at, len(snapshot_data),
         sum(p.get("total_listing_count") or 0 for p in snapshot_data.values()), json.dumps(snapshot_data)],
    )


def test_category_trend_is_insufficient_data_below_the_minimum_snapshot_count(con):
    _insert_snapshot(con, "denture_base", 2, {"P1": {"monthly_sales": 100}})
    _insert_snapshot(con, "denture_base", 1, {"P1": {"monthly_sales": 150}})
    assert MIN_SNAPSHOTS_FOR_TREND > 2  # sanity: this test's premise holds

    result = compute_category_market_trend(con, "denture_base")
    assert result["status"] == "insufficient_data"
    assert result["snapshot_count"] == 2


def test_category_trend_computes_real_growth_from_three_real_snapshots(con):
    _insert_snapshot(con, "denture_base", 3, {
        "P1": {"price": 10.0, "monthly_sales": 100, "observed_monthly_revenue": 1000.0, "total_listing_count": 1},
    })
    _insert_snapshot(con, "denture_base", 2, {
        "P1": {"price": 10.0, "monthly_sales": 150, "observed_monthly_revenue": 1500.0, "total_listing_count": 1},
        "P2": {"price": 20.0, "monthly_sales": 50, "observed_monthly_revenue": 1000.0, "total_listing_count": 1},
    })
    _insert_snapshot(con, "denture_base", 1, {
        "P1": {"price": 10.0, "monthly_sales": 200, "observed_monthly_revenue": 2000.0, "total_listing_count": 1},
        "P2": {"price": 20.0, "monthly_sales": 60, "observed_monthly_revenue": 1200.0, "total_listing_count": 1},
    })

    result = compute_category_market_trend(con, "denture_base")
    assert result["status"] == "ok"
    assert result["snapshot_count"] == 3
    assert result["time_span_hours"] == pytest.approx(2.0, abs=0.01)
    # sales: 100 -> 260, revenue: 1000 -> 3200
    assert result["sales_growth_pct"] == pytest.approx(160.0)
    assert result["revenue_growth_pct"] == pytest.approx(220.0)
    assert result["product_count_growth_pct"] == pytest.approx(100.0)  # 1 -> 2 products


def test_product_trend_only_counts_snapshots_where_the_product_exists(con):
    _insert_snapshot(con, "denture_base", 3, {"P1": {"monthly_sales": 100, "observed_monthly_revenue": 500.0}})
    _insert_snapshot(con, "denture_base", 2, {"P1": {"monthly_sales": 100, "observed_monthly_revenue": 500.0}})
    # P2 only appears once -- must not be treated as having a trend
    _insert_snapshot(con, "denture_base", 1, {
        "P1": {"monthly_sales": 120, "observed_monthly_revenue": 600.0}, "P2": {"monthly_sales": 999},
    })

    p1 = compute_product_trend(con, "denture_base", "P1")
    assert p1["status"] == "ok"
    assert p1["snapshot_count"] == 3
    assert p1["sales_growth_pct"] == pytest.approx(20.0)

    p2 = compute_product_trend(con, "denture_base", "P2")
    assert p2["status"] == "insufficient_data"
    assert p2["snapshot_count"] == 1


def test_growth_pct_never_divides_by_zero_or_fabricates_a_value(con):
    _insert_snapshot(con, "denture_base", 3, {"P1": {"monthly_sales": 0, "observed_monthly_revenue": None}})
    _insert_snapshot(con, "denture_base", 2, {"P1": {"monthly_sales": 0, "observed_monthly_revenue": None}})
    _insert_snapshot(con, "denture_base", 1, {"P1": {"monthly_sales": 50, "observed_monthly_revenue": None}})

    result = compute_product_trend(con, "denture_base", "P1")
    assert result["status"] == "ok"
    assert result["sales_growth_pct"] is None  # base was 0 -- not a fabricated infinite/0% growth
    assert result["revenue_growth_pct"] is None  # no real revenue data at either endpoint
