"""End-to-end: opportunity detection against the real denture_base data
in an isolated in-memory DB. Also locks down the pandas NaN-vs-None bug
found while building this milestone (an early version used .df() which
turns SQL NULL into float NaN, silently breaking every `is None` check).
"""

import json
import math

import duckdb
import pytest

from dmie.database.connection import PROJECT_ROOT, get_connection
from dmie.database.repository import replace_opportunity_signals
from dmie.opportunity.evidence import build_signal_record
from dmie.opportunity.signals import (
    detect_bundle_signal,
    detect_competitive_concentration_signal,
    detect_price_segment_signal,
    detect_product_improvement_signal,
    detect_underrepresented_product_type_signal,
)

SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"
CATEGORY_ID = "denture_base"

THRESHOLDS = {
    "demand_percentile": 0.75,
    "min_products_for_percentile": 4,
    "many_listings_threshold": 3,
    "high_complaint_count_threshold": 3,
    "high_complaint_severity_threshold": 3.5,
    "low_differentiation_price_cov_threshold": 0.15,
    "accessory_activity_threshold": 2,
    "limited_bundle_offering_threshold": 0.2,
}


def test_fetchall_preserves_null_as_none_not_nan():
    """Regression test for the exact bug: pandas .df() turned SQL NULL
    into NaN, and `NaN is None` is False, so demand data that didn't
    exist was silently treated as if it did."""
    con = get_connection()
    try:
        row = con.execute(
            "SELECT best_listing_observed_monthly_sales FROM product_market_metrics "
            "WHERE best_listing_observed_monthly_sales IS NULL LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    assert row is not None, "expected at least one product with no sales data in the real pilot"
    assert row[0] is None
    assert not (isinstance(row[0], float) and math.isnan(row[0]))


@pytest.fixture(scope="module")
def records():
    con = get_connection()
    try:
        rows = con.execute(
            "SELECT product_id, category_id, total_listing_count, best_listing_observed_monthly_sales, "
            "observed_monthly_revenue, representative_price FROM product_market_metrics WHERE category_id = ?",
            [CATEGORY_ID],
        ).fetchall()
        category_row = con.execute(
            "SELECT total_listing_count, price_distribution, product_type_distribution, listing_concentration_hhi "
            "FROM category_market_metrics WHERE category_id = ?", [CATEGORY_ID],
        ).fetchone()
    finally:
        con.close()

    columns = ["product_id", "category_id", "total_listing_count", "best_listing_observed_monthly_sales",
               "observed_monthly_revenue", "representative_price"]
    product_rows = [dict(zip(columns, row)) for row in rows]
    demand_values = [
        r["best_listing_observed_monthly_sales"] if r["best_listing_observed_monthly_sales"] is not None
        else r["observed_monthly_revenue"]
        for r in product_rows
    ]

    result = []
    for row in product_rows:
        improvement = detect_product_improvement_signal(row, demand_values, 0, None, THRESHOLDS)
        bundle = detect_bundle_signal(row, demand_values, 0, 0.0, THRESHOLDS)
        result.append(build_signal_record(improvement, row, []))
        result.append(build_signal_record(bundle, row, []))

    total_listings, price_dist_json, type_dist_json, hhi = category_row
    price_dist = json.loads(price_dist_json)
    type_dist = json.loads(type_dist_json)
    for band_label, band_count in price_dist["bands"].items():
        signal = detect_price_segment_signal(CATEGORY_ID, band_label, band_count, price_dist["count"], THRESHOLDS["min_products_for_percentile"])
        result.append(build_signal_record(signal, {}, []))
    result.append(build_signal_record(detect_underrepresented_product_type_signal(CATEGORY_ID, type_dist), {}, []))
    result.append(build_signal_record(detect_competitive_concentration_signal(CATEGORY_ID, hhi, total_listings), {}, []))

    return product_rows, result


def test_real_data_produces_product_and_category_level_signals(records):
    _, result = records
    assert any(r["product_id"] is not None for r in result)  # PRODUCT_IMPROVEMENT/BUNDLE
    assert any(r["product_id"] is None for r in result)      # PRICE_SEGMENT/TYPE/CONCENTRATION


def test_real_price_segment_signals_fire_on_actual_gaps(records):
    """PRICE_SEGMENT must fire on exactly the price bands that are empty in
    the database under test -- no more, no fewer. Checked against the
    category's own price_distribution rather than a count pinned to one
    pipeline run, so it holds for AI-enabled and rules-only builds alike."""
    _, result = records
    present = sorted(r["supporting_metrics"][0]["name"] for r in result
                     if r["signal_type"] == "PRICE_SEGMENT" and r["status"] == "signal_present")
    con = get_connection()
    try:
        dist = json.loads(con.execute(
            "SELECT price_distribution FROM category_market_metrics WHERE category_id = ?", [CATEGORY_ID]).fetchone()[0])
    finally:
        con.close()
    empty = sorted(f"empty_price_band[{band}]" for band, n in dist["bands"].items() if n <= 0)
    assert present == empty


def test_real_competitive_concentration_is_absent_below_dojftc_threshold(records):
    """Real pilot HHI ~464 as of the first real AI-enabled pipeline run
    (2026-09-23) -- down from ~972 in the rules-only baseline, since AI
    resolved many more products as distinct and RELEVANT. Still well
    below the 2500 'highly concentrated' threshold either way."""
    _, result = records
    concentration = next(r for r in result if r["signal_type"] == "COMPETITIVE_CONCENTRATION")
    assert concentration["status"] == "signal_absent"
    assert concentration["product_id"] is None


def test_real_product_improvement_reports_insufficient_data_honestly(records):
    """4/23 products have demand data as of the first real AI-enabled
    pipeline run (2026-09-23, up from 1/11 in the rules-only baseline)
    -- still insufficient_data for every product regardless, because
    0 review insights exist (no review text in this project's only data
    source, see docs/data_dictionary.md #12) and
    has_high_complaint_frequency always returns None with zero insights,
    which alone forces every PRODUCT_IMPROVEMENT signal's status."""
    _, result = records
    improvements = [r for r in result if r["signal_type"] == "PRODUCT_IMPROVEMENT"]
    assert all(r["status"] == "insufficient_data" for r in improvements)


def test_records_are_stored_and_retrievable(records):
    _, result = records
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    replace_opportunity_signals(con, CATEGORY_ID, result)

    count = con.execute("SELECT COUNT(*) FROM opportunity_signals").fetchone()[0]
    assert count == len(result)

    # PRICE_SEGMENT no longer has any signal_present row as of the first
    # real AI-enabled pipeline run (see test_real_price_segment_signals_fire_on_actual_gaps) --
    # UNDERREPRESENTED_PRODUCT_TYPE is the real signal_present row available
    # to check storage shape against instead.
    row = con.execute(
        "SELECT signal_strength, confidence, supporting_metrics, supporting_review_themes FROM opportunity_signals "
        "WHERE signal_type = 'UNDERREPRESENTED_PRODUCT_TYPE' AND status = 'signal_present' LIMIT 1"
    ).fetchone()
    assert row[1] == pytest.approx(1.0)  # confidence: 23 total products / reference 10, capped at 1.0
    metrics = json.loads(row[2])
    assert len(metrics) == 1
    con.close()
