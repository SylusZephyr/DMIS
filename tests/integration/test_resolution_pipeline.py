"""End-to-end: run entity resolution over the real denture_base listings
in an isolated in-memory DB, and check blocking + the 5 manually-verified
pairs from docs/entity_resolution.md all come out correctly.
"""

import duckdb
import pytest

from dmie.classification.product_type_classifier import load_taxonomy
from dmie.cleaning.normalize import make_listing_id
from dmie.database.connection import PROJECT_ROOT, get_connection
from dmie.database.repository import replace_match_candidates, replace_product_listings, replace_products
from dmie.matching.candidates import generate_candidate_pairs
from dmie.matching.resolution import build_product_listings, build_products, resolve_pair

SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"

KNOWN_PAIRS = {
    ("B0H14R7BBY", "B0H14ZK7HR"): "MATCH",
    ("B0GVB7J6PV", "B0GW5CNJ6F"): "UNCERTAIN",
    ("B0FQ1LTCSS", "B0GWTP6GQF"): "UNCERTAIN",
    ("B0GJTGD6HH", "B0GKB2KPHM"): "UNCERTAIN",
    ("B00VQTLM74", "B00E4MPAIW"): "UNCERTAIN",
}


@pytest.fixture(scope="module")
def resolved():
    prod_con = get_connection()
    try:
        rows = prod_con.execute(
            "SELECT listing_id, title, brand, price FROM listings WHERE category_id = 'denture_base'"
        ).fetchall()
        master_rows = prod_con.execute(
            """
            SELECT l.listing_id, l.category_id, l.title, l.brand, l.image_url,
                   l.monthly_sales,
                   CASE WHEN ptc.product_type = 'UNCERTAIN' THEN NULL ELSE ptc.product_type END,
                   CASE WHEN ptc.product_type = 'UNCERTAIN' THEN NULL ELSE ptc.confidence END
            FROM listings l
            LEFT JOIN listing_product_type_classification ptc ON ptc.listing_id = l.listing_id
            WHERE l.category_id = 'denture_base'
            """
        ).fetchall()
    finally:
        prod_con.close()

    listings = [{"listing_id": r[0], "title": r[1], "brand": r[2], "price": r[3]} for r in rows]
    listing_ids = [listing["listing_id"] for listing in listings]
    master_listings = [
        {"listing_id": r[0], "category_id": r[1], "title": r[2], "brand": r[3],
         "image_url": r[4], "monthly_sales": r[5], "product_type": r[6],
         "product_type_confidence": r[7]}
        for r in master_rows
    ]

    pairs = generate_candidate_pairs(listings)
    results = [resolve_pair(a, b, method) for a, b, method in pairs]
    product_rows = build_product_listings(listing_ids, results)
    products = build_products(master_listings, product_rows, results)

    con = duckdb.connect(":memory:")
    con.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    replace_match_candidates(con, "denture_base", results)
    replace_product_listings(con, "denture_base", product_rows)
    replace_products(con, "denture_base", products)

    return con, listing_ids, pairs, results, product_rows


def test_blocking_drastically_reduces_comparisons(resolved):
    _, listing_ids, pairs, _, _ = resolved
    all_pairs = len(listing_ids) * (len(listing_ids) - 1) // 2
    assert len(pairs) < all_pairs * 0.1  # at least a 90% reduction


def test_every_listing_has_exactly_one_product_listings_row(resolved):
    _, listing_ids, _, _, product_rows = resolved
    assert len(product_rows) == len(listing_ids)
    assert len({r["listing_id"] for r in product_rows}) == len(listing_ids)


def test_known_pairs_get_expected_decision(resolved):
    con, *_ = resolved
    for (asin_a, asin_b), expected_decision in KNOWN_PAIRS.items():
        lid_a, lid_b = make_listing_id(asin_a), make_listing_id(asin_b)
        row = con.execute(
            "SELECT decision FROM match_candidates "
            "WHERE (listing_id_a = ? AND listing_id_b = ?) OR (listing_id_a = ? AND listing_id_b = ?)",
            [lid_a, lid_b, lid_b, lid_a],
        ).fetchone()
        assert row is not None, f"{asin_a}/{asin_b} did not survive blocking"
        assert row[0] == expected_decision, f"{asin_a}/{asin_b}: expected {expected_decision}, got {row[0]}"


def test_confirmed_pair_shares_a_product_id(resolved):
    con, *_ = resolved
    lid_a = make_listing_id("B0H14R7BBY")
    lid_b = make_listing_id("B0H14ZK7HR")
    pid_a = con.execute("SELECT product_id FROM product_listings WHERE listing_id = ?", [lid_a]).fetchone()[0]
    pid_b = con.execute("SELECT product_id FROM product_listings WHERE listing_id = ?", [lid_b]).fetchone()[0]
    assert pid_a == pid_b


def test_uncertain_pairs_are_not_merged_into_same_product(resolved):
    con, *_ = resolved
    for (asin_a, asin_b), expected_decision in KNOWN_PAIRS.items():
        if expected_decision != "UNCERTAIN":
            continue
        lid_a, lid_b = make_listing_id(asin_a), make_listing_id(asin_b)
        pid_a = con.execute("SELECT product_id FROM product_listings WHERE listing_id = ?", [lid_a]).fetchone()[0]
        pid_b = con.execute("SELECT product_id FROM product_listings WHERE listing_id = ?", [lid_b]).fetchone()[0]
        assert pid_a != pid_b, f"{asin_a}/{asin_b} should not share a product_id"


def test_uncertainty_queue_is_nonempty_and_queryable(resolved):
    con, *_ = resolved
    queue = con.execute(
        "SELECT * FROM match_candidates WHERE review_status = 'needs_review'"
    ).df()
    assert len(queue) > 0


# --- Product Master (M12): every resolved product_id must have a real
# products row -- the exact gap this milestone fixed (products existed as
# a table but nothing ever wrote to it, so the dashboard had no choice but
# to reconstruct product identity ad hoc from listings on every load).

def test_every_resolved_product_id_has_a_products_row(resolved):
    con, *_ = resolved
    distinct_ids = con.execute("SELECT COUNT(DISTINCT product_id) FROM product_listings").fetchone()[0]
    product_rows = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    assert product_rows == distinct_ids


def test_products_have_a_name_and_no_invented_type(resolved):
    """This fixture reads real listing_product_type_classification rows
    from the live DB (M11 Stage 2/3 has been run for real -- see
    PROGRESS.md), so some products now have a real, classified type. Every
    non-null one must be an actual frozen taxonomy id -- never a value the
    classifier could not have produced."""
    con, *_ = resolved
    rows = con.execute("SELECT product_name, product_type FROM products").df()
    assert rows["product_name"].notna().all()
    taxonomy = load_taxonomy("denture_base", 1)
    classified = rows["product_type"].dropna()
    assert not classified.empty  # confirms this test isn't accidentally vacuous
    assert classified.isin(taxonomy.ids).all()
