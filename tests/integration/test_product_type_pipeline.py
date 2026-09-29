"""End-to-end for M11 Stage 3: real classifier output -> its own DB table
(listing_product_type_classification) -> the Product Master (products),
against a real schema in an isolated in-memory DB. Confirms the new table
round-trips correctly and doesn't collide with listing_classification's
existing, differently-scoped confidence/reason/review_status columns.
"""

from unittest.mock import patch

import duckdb
import pytest

from dmie.classification.product_type_classifier import (
    ListingContext,
    classify_product_type,
)
from dmie.database.connection import PROJECT_ROOT
from dmie.database.repository import replace_listing_product_type_classifications
from dmie.matching.resolution import build_product_listings, build_products

SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"


@pytest.fixture
def con():
    connection = duckdb.connect(":memory:")
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield connection
    connection.close()


def _seed_listings(con, *listing_ids, category="denture_base"):
    """replace_listing_product_type_classifications scopes its delete via
    a join to listings.category_id (that table has no category_id of its
    own) -- real callers always have a backing listings row, so tests
    that exercise the scoping need one too."""
    con.executemany(
        "INSERT INTO listings (listing_id, category_id) VALUES (?, ?)",
        [[lid, category] for lid in listing_ids],
    )


def test_classification_results_round_trip_through_their_own_table(con):
    _seed_listings(con, "L1", "L2")
    with patch("dmie.classification.product_type_classifier.classify_with_ai", return_value=None):
        results = [
            classify_product_type(ListingContext("L1", "Antinsky Denture Base Resin")),
            classify_product_type(ListingContext("L2", "Hard Denture Reline Kit")),
        ]
    replace_listing_product_type_classifications(con, "denture_base", results)

    rows = con.execute(
        "SELECT listing_id, taxonomy_version, product_type, confidence, classifier_method, review_status "
        "FROM listing_product_type_classification ORDER BY listing_id"
    ).fetchall()
    assert rows == [
        ("L1", 1, "DB_RESIN", 0.95, "rules", "auto_accepted"),
        ("L2", 1, "DB_RELINE", 0.95, "rules", "auto_accepted"),
    ]


def test_replace_regenerates_from_scratch_not_appends(con):
    _seed_listings(con, "L1", "L2")
    with patch("dmie.classification.product_type_classifier.classify_with_ai", return_value=None):
        first = [classify_product_type(ListingContext("L1", "Antinsky Denture Base Resin"))]
        replace_listing_product_type_classifications(con, "denture_base", first)
        second = [classify_product_type(ListingContext("L2", "Hard Denture Reline Kit"))]
        replace_listing_product_type_classifications(con, "denture_base", second)

    rows = con.execute("SELECT listing_id FROM listing_product_type_classification").fetchall()
    assert rows == [("L2",)]  # L1's row from the first run is gone, not accumulated


def test_writing_product_type_classifications_never_touches_listing_classification(con):
    """The whole reason this is a separate table: listing_classification's
    confidence/reason/review_status already mean something else
    (relevance). Writing product-type results must leave any existing
    listing_classification row completely untouched."""
    _seed_listings(con, "L1")
    con.execute(
        "INSERT INTO listing_classification (listing_id, relevant, relevance_class, confidence, reason, review_status) "
        "VALUES ('L1', true, 'RELEVANT', 0.95, 'EXACT_MATCH', 'auto_accepted')"
    )
    with patch("dmie.classification.product_type_classifier.classify_with_ai", return_value=None):
        results = [classify_product_type(ListingContext("L1", "Hard Denture Reline Kit"))]
    replace_listing_product_type_classifications(con, "denture_base", results)

    relevance_row = con.execute(
        "SELECT confidence, reason, review_status FROM listing_classification WHERE listing_id = 'L1'"
    ).fetchone()
    assert relevance_row == (0.95, "EXACT_MATCH", "auto_accepted")  # untouched by product-type write


def test_build_products_consumes_the_new_table_via_a_left_join(con):
    """Simulates what scripts/resolve_products.py does: LEFT JOIN
    listing_product_type_classification onto listings, mapping UNCERTAIN
    to NULL, and feed that into build_products."""
    con.execute("INSERT INTO listings (listing_id, category_id, title, brand) VALUES "
                "('L1', 'denture_base', 'Antinsky Denture Base Resin', 'Antinsky')")
    with patch("dmie.classification.product_type_classifier.classify_with_ai", return_value=None):
        results = [classify_product_type(ListingContext("L1", "Antinsky Denture Base Resin"))]
    replace_listing_product_type_classifications(con, "denture_base", results)

    rows = con.execute(
        """
        SELECT l.listing_id, l.category_id, l.title, l.brand, l.image_url, l.monthly_sales,
               CASE WHEN ptc.product_type = 'UNCERTAIN' THEN NULL ELSE ptc.product_type END,
               CASE WHEN ptc.product_type = 'UNCERTAIN' THEN NULL ELSE ptc.confidence END
        FROM listings l
        LEFT JOIN listing_product_type_classification ptc ON ptc.listing_id = l.listing_id
        """
    ).fetchall()
    master_listings = [
        {"listing_id": r[0], "category_id": r[1], "title": r[2], "brand": r[3],
         "image_url": r[4], "monthly_sales": r[5], "product_type": r[6], "product_type_confidence": r[7]}
        for r in rows
    ]
    product_rows = build_product_listings(["L1"], [])
    products = build_products(master_listings, product_rows, [])
    assert products[0]["product_type"] == "DB_RESIN"
    assert products[0]["product_type_confidence"] == 0.95
