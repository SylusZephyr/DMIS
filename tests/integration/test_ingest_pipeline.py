"""End-to-end tests for the normalization + DuckDB + Parquet pipeline.

Uses an isolated in-memory DuckDB connection (schema applied fresh) so
these tests never touch the real project database.
"""

import hashlib

import duckdb
import pytest

from dmie.cleaning.normalize import normalize_dataframe
from dmie.cleaning.validation import find_violations
from dmie.database.connection import PROJECT_ROOT
from dmie.database.repository import export_listings_parquet, insert_decision_log, upsert_category, upsert_listings
from dmie.ingestion.excel_loader import load_raw_excel

RAW_PATH = PROJECT_ROOT / "data" / "raw" / "denture_base" / "denture_base_sellersprite.xlsx"
SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"
RAW_SHA256_BEFORE = "5771a9b3fe47006db153b5a84ae250ec73dc3833d05018ba4e792d100e8694b9"


@pytest.fixture()
def con():
    connection = duckdb.connect(":memory:")
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def normalized():
    df = load_raw_excel(RAW_PATH)
    return normalize_dataframe(
        df, raw_source_file="data/raw/denture_base/denture_base_sellersprite.xlsx", category_id="denture_base"
    )


def test_pilot_export_normalizes_with_one_duplicate_asin_dropped(normalized):
    # 106 raw rows, 1 known duplicate ASIN (see docs/data_dictionary.md)
    assert len(normalized.listings) == 105
    dupe_drops = [d for d in normalized.decisions if d.decision_type == "duplicate_asin_dropped"]
    assert len(dupe_drops) == 1
    assert dupe_drops[0].entity_id == "B0FMK8XB26"


def test_normalized_output_has_no_violations(normalized):
    assert find_violations(normalized.listings) == []


def test_upsert_is_idempotent(con, normalized):
    upsert_listings(con, normalized.listings)
    upsert_listings(con, normalized.listings)  # re-run should not duplicate or error
    count = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    assert count == 105


def test_category_id_survives_a_second_ingest_run(con, normalized):
    """Regression: normalize_dataframe used to hardcode category_id=None
    for every listing regardless of what was requested, so a second real
    ingest run (e.g. via scripts/run_pipeline.py) silently wiped every
    listing's category_id back to NULL on upsert -- breaking every
    downstream `WHERE category_id = ?` query (classify_product_types.py,
    market/aggregation.py, detect_opportunities.py) without raising any
    error. Two full upserts in a row must both leave category_id set."""
    upsert_listings(con, normalized.listings)
    upsert_listings(con, normalized.listings)
    rows = con.execute("SELECT DISTINCT category_id FROM listings").fetchall()
    assert rows == [("denture_base",)]


def test_upsert_category_makes_a_new_category_visible_and_is_idempotent(con):
    """Regression: found live (2026-09-24) that a fully onboarded
    category (ingested, classified, resolved, scored) was completely
    invisible in the dashboard/API's category picker, because
    config/categories.yaml and the `categories` DB table had no sync
    mechanism at all -- no error, just silence."""
    upsert_category(con, "dental_models", leaf_category="牙科模型", marketplace="US")
    row = con.execute(
        "SELECT category_id, leaf_category, marketplace, parent_category FROM categories WHERE category_id = 'dental_models'"
    ).fetchone()
    assert row == ("dental_models", "牙科模型", "US", None)

    upsert_category(con, "dental_models", leaf_category="牙科模型 (updated)", marketplace="US")
    count = con.execute("SELECT COUNT(*) FROM categories WHERE category_id = 'dental_models'").fetchone()[0]
    assert count == 1  # re-run updates in place, doesn't duplicate


def test_cross_category_listing_id_conflict_is_kept_and_logged_not_silently_reassigned(con):
    """Regression: found via a real ~200MB merged multi-category export
    where the same ASIN legitimately appeared under more than one raw
    category label -- a later ingest for a different category used to
    silently steal the listing_id (PRIMARY KEY) from whichever category
    owned it first, with no warning (a real ~666-listing shrinkage in
    denture_base after ingesting two new categories). The conflicting
    category must be rejected (original kept untouched) and logged."""
    rec = {
        "listing_id": "L1", "asin": "B000000001", "category_id": "denture_base",
        "title": "Original title", "brand": "Acme", "url": None, "image_url": None,
        "price": 9.99, "monthly_sales": None, "monthly_revenue": None, "rating": None,
        "review_count": None, "raw_source_file": "a.xlsx", "created_at": None,
    }
    conflicts = upsert_listings(con, [rec])
    assert conflicts == []

    conflicting_rec = {**rec, "category_id": "implants", "title": "A different category's title"}
    conflicts = upsert_listings(con, [conflicting_rec])
    assert len(conflicts) == 1
    assert conflicts[0].decision_type == "category_conflict_skipped"
    assert conflicts[0].old_value == "denture_base"
    assert conflicts[0].new_value == "implants"

    row = con.execute("SELECT category_id, title FROM listings WHERE listing_id = 'L1'").fetchone()
    assert row == ("denture_base", "Original title")  # untouched by the rejected ingest


def test_decision_log_roundtrip(con, normalized):
    insert_decision_log(con, normalized.decisions)
    count = con.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0]
    assert count == len(normalized.decisions)


def test_parquet_export_roundtrip(con, normalized, tmp_path):
    upsert_listings(con, normalized.listings)
    out_path = tmp_path / "export.parquet"
    export_listings_parquet(
        con, str(out_path), raw_source_file="data/raw/denture_base/denture_base_sellersprite.xlsx"
    )
    assert out_path.exists()
    count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{out_path}')").fetchone()[0]
    assert count == 105


def test_parquet_export_refuses_to_target_data_raw(con, normalized):
    upsert_listings(con, normalized.listings)
    forbidden = PROJECT_ROOT / "data" / "raw" / "denture_base" / "denture_base_sellersprite.xlsx"
    with pytest.raises(ValueError, match="refusing to write parquet export into data/raw"):
        export_listings_parquet(con, str(forbidden))


def test_raw_file_untouched_by_full_pipeline_run(con, normalized, tmp_path):
    """Regression test: an earlier version of export_listings_parquet mixed
    a parameterized WHERE clause with a parameterized COPY destination in
    one statement, and DuckDB bound them out of order — silently overwriting
    the raw source file with Parquet bytes. This runs the real pipeline
    end-to-end and confirms the raw file's hash is unchanged afterward."""
    upsert_listings(con, normalized.listings)
    insert_decision_log(con, normalized.decisions)
    export_listings_parquet(
        con, str(tmp_path / "export.parquet"),
        raw_source_file="data/raw/denture_base/denture_base_sellersprite.xlsx",
    )
    digest = hashlib.sha256(RAW_PATH.read_bytes()).hexdigest()
    assert digest == RAW_SHA256_BEFORE
