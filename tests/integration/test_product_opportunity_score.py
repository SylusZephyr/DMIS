"""Tier 2 -- products.product_family (M4) and products.opportunity_score
(M9) round-trip through the real repository functions against an
isolated in-memory schema, same pattern as every other repository test.
"""


import duckdb
import pytest

from dmie.database.connection import PROJECT_ROOT
from dmie.database.repository import replace_products, update_opportunity_scores

SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"


@pytest.fixture
def con():
    connection = duckdb.connect(":memory:")
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield connection
    connection.close()


def _product_row(product_id: str, **overrides) -> dict:
    row = {
        "product_id": product_id, "category_id": "denture_base", "product_name": "Test Product",
        "product_type": None, "product_type_confidence": None, "product_type_conflict": False,
        "product_family": None, "brand": "Acme", "model": None,
        "representative_image": None, "confidence": None,
    }
    row.update(overrides)
    return row


def test_replace_products_persists_product_family_as_null_by_default(con):
    replace_products(con, "denture_base", [_product_row("P1")])
    row = con.execute("SELECT product_family FROM products WHERE product_id = 'P1'").fetchone()
    assert row == (None,)


def test_new_product_has_no_opportunity_score_until_scored(con):
    replace_products(con, "denture_base", [_product_row("P1")])
    row = con.execute("SELECT opportunity_score FROM products WHERE product_id = 'P1'").fetchone()
    assert row == (None,)


def test_update_opportunity_scores_sets_real_values(con):
    replace_products(con, "denture_base", [_product_row("P1"), _product_row("P2")])
    update_opportunity_scores(con, "denture_base", {"P1": 72.5, "P2": None})

    rows = dict(con.execute("SELECT product_id, opportunity_score FROM products ORDER BY product_id").fetchall())
    assert rows == {"P1": 72.5, "P2": None}


def test_update_opportunity_scores_clears_stale_scores_not_covered_by_the_new_run(con):
    """A product that had a score in a previous run but whose signals are
    now insufficient_data must not stay stuck at the old value."""
    replace_products(con, "denture_base", [_product_row("P1")])
    update_opportunity_scores(con, "denture_base", {"P1": 90.0})
    assert con.execute("SELECT opportunity_score FROM products WHERE product_id = 'P1'").fetchone() == (90.0,)

    update_opportunity_scores(con, "denture_base", {"P1": None})
    assert con.execute("SELECT opportunity_score FROM products WHERE product_id = 'P1'").fetchone() == (None,)


def test_update_opportunity_scores_handles_empty_dict(con):
    replace_products(con, "denture_base", [_product_row("P1")])
    update_opportunity_scores(con, "denture_base", {})  # must not raise
    assert con.execute("SELECT opportunity_score FROM products WHERE product_id = 'P1'").fetchone() == (None,)
