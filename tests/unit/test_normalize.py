import math

import pandas as pd
import pytest

from dmie.cleaning.normalize import (
    make_listing_id,
    normalize_dataframe,
    normalize_nonnegative,
    normalize_price,
    normalize_rating,
    normalize_whitespace,
    parse_numeric,
)

# --- parse_numeric: malformed / missing input ---------------------------

@pytest.mark.parametrize("raw, expected", [
    (None, None),
    (float("nan"), None),
    ("", None),
    ("   ", None),
    ("N/A", None),
    ("-", None),
    (True, None),  # bool is not a numeric value here
    (12.5, 12.5),
    (12, 12.0),
    ("12.5", 12.5),
    ("$12.99", 12.99),
    ("1,234.5", 1234.5),
    ("  8.99  ", 8.99),
])
def test_parse_numeric(raw, expected):
    result = parse_numeric(raw)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


# --- normalize_whitespace -------------------------------------------------

def test_normalize_whitespace_collapses_and_strips():
    assert normalize_whitespace("  Hello   World  ") == "Hello World"


def test_normalize_whitespace_strips_excel_control_artifact():
    assert normalize_whitespace("Foo_x000D_\nBar") == "Foo Bar"


def test_normalize_whitespace_blank_is_none():
    assert normalize_whitespace("   ") is None
    assert normalize_whitespace(None) is None
    assert normalize_whitespace(float("nan")) is None


# --- bounded field normalizers: malformed / out-of-range input -----------

def test_normalize_price_rejects_zero_and_negative():
    decisions = []
    assert normalize_price(0, "A1", decisions) is None
    assert normalize_price(-5.0, "A1", decisions) is None
    assert len(decisions) == 2
    assert all(d.decision_type == "normalization_rejected_value" for d in decisions)


def test_normalize_price_accepts_valid():
    decisions = []
    assert normalize_price("$19.99", "A1", decisions) == pytest.approx(19.99)
    assert decisions == []


def test_normalize_rating_rejects_out_of_range():
    decisions = []
    assert normalize_rating(7, "A1", decisions) is None
    assert normalize_rating(-1, "A1", decisions) is None
    assert len(decisions) == 2


def test_normalize_rating_accepts_boundaries():
    decisions = []
    assert normalize_rating(0, "A1", decisions) == 0
    assert normalize_rating(5, "A1", decisions) == 5
    assert decisions == []


def test_normalize_nonnegative_rejects_negative():
    decisions = []
    assert normalize_nonnegative(-1, "monthly_sales", "A1", decisions) is None
    assert len(decisions) == 1
    assert "monthly_sales" in decisions[0].reason


def test_normalize_nonnegative_missing_is_none_without_decision():
    decisions = []
    assert normalize_nonnegative(None, "monthly_sales", "A1", decisions) is None
    assert decisions == []


# --- make_listing_id -------------------------------------------------------

def test_make_listing_id_is_stable():
    assert make_listing_id("B0ABC12345") == make_listing_id("B0ABC12345")


def test_make_listing_id_differs_by_asin():
    assert make_listing_id("B0ABC12345") != make_listing_id("B0XYZ99999")


def test_make_listing_id_differs_by_marketplace():
    assert make_listing_id("B0ABC12345", "US") != make_listing_id("B0ABC12345", "UK")


# --- normalize_dataframe: end-to-end with malformed/missing rows ---------

def _row(**overrides):
    base = {
        "ASIN": "B0AAA00001",
        "商品标题": "  Some   Title  ",
        "品牌": "BrandX",
        "商品详情页链接": "https://example.com/dp/B0AAA00001",
        "商品主图": "https://example.com/img.jpg",
        "价格($)": 19.99,
        "评分": 4.5,
        "子体销量": 100.0,
        "子体销售额($)": 1999.0,
    }
    base.update(overrides)
    return base


def test_normalize_dataframe_drops_missing_asin_with_decision():
    df = pd.DataFrame([_row(ASIN=None), _row(ASIN="B0AAA00002")])
    result = normalize_dataframe(df, raw_source_file="test.xlsx")
    assert len(result.listings) == 1
    assert any(d.decision_type == "row_excluded" for d in result.decisions)


def test_normalize_dataframe_dedupes_duplicate_asin():
    df = pd.DataFrame([_row(), _row()])
    result = normalize_dataframe(df, raw_source_file="test.xlsx")
    assert len(result.listings) == 1
    assert any(d.decision_type == "duplicate_asin_dropped" for d in result.decisions)


def test_normalize_dataframe_nulls_invalid_price_but_keeps_row():
    df = pd.DataFrame([_row(**{"价格($)": -10.0})])
    result = normalize_dataframe(df, raw_source_file="test.xlsx")
    assert len(result.listings) == 1
    assert result.listings[0]["price"] is None
    assert any(d.decision_type == "normalization_rejected_value" for d in result.decisions)


def test_normalize_dataframe_handles_nan_sales_and_revenue():
    df = pd.DataFrame([_row(**{"子体销量": math.nan, "子体销售额($)": math.nan})])
    result = normalize_dataframe(df, raw_source_file="test.xlsx")
    assert result.listings[0]["monthly_sales"] is None
    assert result.listings[0]["monthly_revenue"] is None


def test_normalize_dataframe_review_count_always_none():
    df = pd.DataFrame([_row()])
    result = normalize_dataframe(df, raw_source_file="test.xlsx")
    assert result.listings[0]["review_count"] is None


def test_normalize_dataframe_listing_id_stable_across_calls():
    df = pd.DataFrame([_row()])
    r1 = normalize_dataframe(df, raw_source_file="test.xlsx")
    r2 = normalize_dataframe(df, raw_source_file="test.xlsx")
    assert r1.listings[0]["listing_id"] == r2.listings[0]["listing_id"]


# --- category_id: regression for a real bug found by scripts/run_pipeline.py's
# first end-to-end run (M15). normalize_dataframe hardcoded category_id to
# None for every listing since this function was first written -- nothing
# had ever re-run scripts/ingest.py against the live DB since some earlier,
# undocumented one-time fix had set listings.category_id correctly, so the
# gap stayed invisible until the orchestrator re-ran ingestion for real and
# upsert_listings overwrote every listing's category_id back to NULL. ---

def test_normalize_dataframe_sets_category_id_when_given():
    df = pd.DataFrame([_row()])
    result = normalize_dataframe(df, raw_source_file="test.xlsx", category_id="denture_base")
    assert result.listings[0]["category_id"] == "denture_base"


def test_normalize_dataframe_category_id_defaults_to_none_not_a_guess():
    """No category_id passed -- must stay None, never silently invent one."""
    df = pd.DataFrame([_row()])
    result = normalize_dataframe(df, raw_source_file="test.xlsx")
    assert result.listings[0]["category_id"] is None
