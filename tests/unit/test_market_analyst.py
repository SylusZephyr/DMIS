"""Tier 2, Milestone 18 -- AI Market Analyst. build_context() is tested
against a real isolated in-memory schema (same pattern as every other
repository-adjacent test); ask() is tested with call_ai mocked at
dmie.ai.market_analyst's own import (same convention
test_ai_failure_handling.py already established for classifier.py/
product_type_classifier.py), never touching the real network.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import duckdb
import pytest

from dmie.ai.client import STATUS_ERROR, STATUS_OK, STATUS_UNAVAILABLE, AICallResult
from dmie.ai.market_analyst import ask, build_context
from dmie.database.connection import PROJECT_ROOT

SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"


@pytest.fixture
def con():
    connection = duckdb.connect(":memory:")
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield connection
    connection.close()


def _seed(con):
    now = datetime.now(timezone.utc)
    con.execute(
        "INSERT INTO category_market_metrics (category_id, total_product_count, total_listing_count, "
        "total_observed_monthly_sales, total_observed_monthly_revenue, listing_concentration_hhi, "
        "product_type_distribution, price_distribution, sales_distribution, calculated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["denture_base", 1, 1, 100.0, 449.0, 972.0, '{"DB_WAX_PLATE": 1}',
         '{"count": 1, "min": 8.99, "max": 8.99, "median": 8.99, "mean": 8.99, "bands": {}}',
         '{"count": 1, "min": 50, "max": 50, "median": 50, "mean": 50}', now],
    )
    con.execute(
        "INSERT INTO products (product_id, category_id, product_name, brand, product_type, "
        "opportunity_score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["P1", "denture_base", "Test Wax Plate", "Acme", "DB_WAX_PLATE", 72.5, now],
    )
    con.execute(
        "INSERT INTO product_market_metrics (product_id, category_id, total_listing_count, "
        "listings_with_price_data, listings_with_sales_data, representative_price, "
        "best_listing_observed_monthly_sales, observed_monthly_revenue, rating, calculated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["P1", "denture_base", 2, 2, 1, 8.99, 100.0, 449.0, 4.5, now],
    )
    con.execute(
        "INSERT INTO opportunity_signals (signal_id, product_id, category_id, signal_type, status, "
        "signal_strength, confidence, evidence, supporting_metrics, supporting_review_themes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["S1", "P1", "denture_base", "PRODUCT_IMPROVEMENT", "signal_present", "HIGH", 1.0, "{}", "[]", "[]", now],
    )
    con.execute(
        "INSERT INTO listings (listing_id, asin, category_id, created_at) VALUES (?, ?, ?, ?)",
        ["L1", "ASIN1", "denture_base", now],
    )
    con.execute(
        "INSERT INTO listing_classification (listing_id, relevant, relevance_class, confidence, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ["L1", True, "RELEVANT", 0.95, now],
    )


# --- build_context ---

def test_build_context_includes_real_category_metrics(con):
    _seed(con)
    context = build_context(con, "denture_base")
    assert context["category_metrics"]["total_product_count"] == 1
    assert context["category_metrics"]["listing_concentration_hhi"] == 972.0


def test_build_context_includes_price_and_sales_distribution_for_median_questions(con):
    """A real AI call once correctly refused to answer 'what's the
    median price' because this field wasn't included -- pin it so it
    can't quietly disappear again."""
    _seed(con)
    context = build_context(con, "denture_base")
    assert context["category_metrics"]["price_distribution"]["median"] == 8.99
    assert context["category_metrics"]["sales_distribution"]["median"] == 50


def test_build_context_includes_products_with_opportunity_score(con):
    _seed(con)
    context = build_context(con, "denture_base")
    assert len(context["products"]) == 1
    assert context["products"][0]["product_id"] == "P1"
    assert context["products"][0]["opportunity_score"] == 72.5


def test_build_context_attaches_active_signals_to_the_right_product(con):
    _seed(con)
    context = build_context(con, "denture_base")
    assert context["products"][0]["active_signals"] == [
        {"signal_type": "PRODUCT_IMPROVEMENT", "status": "signal_present", "signal_strength": "HIGH"}
    ]


def test_build_context_data_quality_counts_real_listings(con):
    _seed(con)
    context = build_context(con, "denture_base")
    assert context["data_quality"]["total_listings"] == 1
    assert context["data_quality"]["relevant_listings"] == 1


def test_build_context_handles_a_category_with_no_data_at_all(con):
    context = build_context(con, "nonexistent_category")
    assert context["category_metrics"] is None
    assert context["products"] == []
    assert context["data_quality"]["total_listings"] == 0


# --- ask() ---

def test_ask_returns_unavailable_status_and_still_includes_context(con):
    _seed(con)
    with patch("dmie.ai.market_analyst.call_ai") as mock_call:
        mock_call.return_value = AICallResult(status=STATUS_UNAVAILABLE, error="no API key")
        result = ask(con, "denture_base", "What's the best product?")
    assert result["status"] == STATUS_UNAVAILABLE
    assert "context" in result
    assert result["context"]["products"][0]["product_id"] == "P1"
    assert "answer" not in result


def test_ask_returns_error_status_on_failed_call(con):
    _seed(con)
    with patch("dmie.ai.market_analyst.call_ai") as mock_call:
        mock_call.return_value = AICallResult(status=STATUS_ERROR, error="network timeout")
        result = ask(con, "denture_base", "What's the best product?")
    assert result["status"] == STATUS_ERROR
    assert result["error"] == "network timeout"


def test_ask_extracts_answer_and_referenced_products_on_success(con):
    _seed(con)
    with patch("dmie.ai.market_analyst.call_ai") as mock_call:
        mock_call.return_value = AICallResult(
            status=STATUS_OK,
            data={"answer": "P1 looks strong.", "referenced_product_ids": ["P1"], "insufficient_data": False},
        )
        result = ask(con, "denture_base", "What's the best product?")
    assert result["status"] == STATUS_OK
    assert result["answer"] == "P1 looks strong."
    assert result["referenced_product_ids"] == ["P1"]
    assert result["insufficient_data"] is False


def test_ask_passes_the_real_context_and_question_into_the_prompt(con):
    _seed(con)
    with patch("dmie.ai.market_analyst.call_ai") as mock_call:
        mock_call.return_value = AICallResult(status=STATUS_OK, data={"answer": "ok", "referenced_product_ids": [], "insufficient_data": False})
        ask(con, "denture_base", "unique_question_marker_xyz")
    prompt_arg = mock_call.call_args.args[0]
    assert "unique_question_marker_xyz" in prompt_arg
    assert "P1" in prompt_arg
    assert "72.5" in prompt_arg
