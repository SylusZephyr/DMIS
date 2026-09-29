"""Run listing-to-product entity resolution over one category's listings.

Blocking (Stage 5) -> per-pair resolution (Stages 1-4, 6, 7) ->
clustering -> writes match_candidates (full audit trail) and
product_listings (final resolved product_id per listing).

Usage: python scripts/resolve_products.py [category_id]
Defaults to the denture_base pilot category. Scoped to that category's
own listings -- resolution was never meant to match listings across
categories (a denture_base and an implants listing sharing a generic
brand are not "the same product"); this was invisible while
denture_base was the only category with data.
"""

import sys
from collections import Counter

from dmie.database.connection import PROJECT_ROOT, get_connection
from dmie.database.repository import replace_match_candidates, replace_product_listings, replace_products
from dmie.database.runs import finish_run, record_product_resolution_run, start_run
from dmie.matching.candidates import generate_candidate_pairs
from dmie.matching.resolution import MATCHING_VERSION, build_product_listings, build_products, resolve_pair


def _uncertainty_queue_path(category: str):
    return PROJECT_ROOT / "data" / "exports" / category / "product_matching_uncertainty_queue.csv"


def export_uncertainty_queue(con, category: str) -> int:
    """Human-review queue: every match_candidates row still needing a
    look, with enough listing context (title/brand/price on both sides)
    to actually review it. Scoped to `category` so one category's run
    doesn't overwrite another's queue file or mix their rows. Returns
    the row count written."""
    queue = con.execute(
        """
        SELECT
            mc.candidate_id, mc.decision, mc.match_method, mc.match_confidence,
            la.asin AS asin_a, la.title AS title_a, la.brand AS brand_a, la.price AS price_a,
            lb.asin AS asin_b, lb.title AS title_b, lb.brand AS brand_b, lb.price AS price_b
        FROM match_candidates mc
        JOIN listings la ON la.listing_id = mc.listing_id_a
        JOIN listings lb ON lb.listing_id = mc.listing_id_b
        WHERE mc.review_status = 'needs_review' AND la.category_id = ?
        ORDER BY mc.match_confidence DESC
        """,
        [category],
    ).df()
    path = _uncertainty_queue_path(category)
    path.parent.mkdir(parents=True, exist_ok=True)
    queue.to_csv(path, index=False)
    return len(queue)


def run(category: str = "denture_base") -> None:
    con = get_connection()
    run_id = start_run(con, category, "product_resolution", MATCHING_VERSION)
    try:
        rows = con.execute(
            "SELECT listing_id, title, brand, price FROM listings WHERE category_id = ?", [category]
        ).fetchall()
        listings = [{"listing_id": r[0], "title": r[1], "brand": r[2], "price": r[3]} for r in rows]
        listing_ids = [listing["listing_id"] for listing in listings]

        candidate_pairs = generate_candidate_pairs(listings)
        results = [resolve_pair(a, b, method) for a, b, method in candidate_pairs]

        replace_match_candidates(con, category, results)

        product_rows = build_product_listings(listing_ids, results)
        replace_product_listings(con, category, product_rows)

        master_rows = con.execute(
            """
            SELECT l.listing_id, l.category_id, l.title, l.brand, l.image_url,
                   l.monthly_sales,
                   -- UNCERTAIN is not a real vote for any taxonomy type --
                   -- treated as no classification at all (NULL), same as a
                   -- listing that was never classified.
                   CASE WHEN ptc.product_type = 'UNCERTAIN' THEN NULL ELSE ptc.product_type END AS product_type,
                   CASE WHEN ptc.product_type = 'UNCERTAIN' THEN NULL ELSE ptc.confidence END AS product_type_confidence
            FROM listings l
            LEFT JOIN listing_product_type_classification ptc ON ptc.listing_id = l.listing_id
            WHERE l.category_id = ?
            """,
            [category],
        ).fetchall()
        master_listings = [
            {"listing_id": r[0], "category_id": r[1], "title": r[2], "brand": r[3],
             "image_url": r[4], "monthly_sales": r[5], "product_type": r[6],
             "product_type_confidence": r[7]}
            for r in master_rows
        ]
        products = build_products(master_listings, product_rows, results)
        replace_products(con, category, products)

        n_queued = export_uncertainty_queue(con, category)

        decisions = Counter(r.decision for r in results)
        n_products = len({row["product_id"] for row in product_rows})
        record_product_resolution_run(
            con, run_id, category, matching_version=MATCHING_VERSION,
            total_listings=len(listing_ids), candidate_pairs=len(candidate_pairs),
            match_count=decisions.get("MATCH", 0), no_match_count=decisions.get("NO_MATCH", 0),
            uncertain_count=decisions.get("UNCERTAIN", 0), resulting_products=n_products,
        )
        finish_run(con, run_id, summary={"pair_decisions": dict(decisions), "resulting_products": n_products})
    except Exception as exc:
        finish_run(con, run_id, summary={}, error_message=str(exc))
        raise
    finally:
        con.close()

    n_clustered = sum(1 for row in product_rows if row["match_method"] != "singleton")

    print(f"run_id: {run_id}")
    print(f"listings: {len(listing_ids)}")
    print(f"candidate pairs (post-blocking): {len(candidate_pairs)}")
    print(f"pair decisions: {dict(decisions)}")
    print(f"resulting products: {n_products} (listings clustered into a multi-listing product: {n_clustered})")
    print(f"products table (Product Master) rows written: {len(products)}")
    print(f"uncertainty queue: {n_queued} rows -> {_uncertainty_queue_path(category)}")


if __name__ == "__main__":
    category_arg = sys.argv[1] if len(sys.argv) > 1 else "denture_base"
    run(category_arg)
