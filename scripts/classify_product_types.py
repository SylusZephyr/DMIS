"""Run the hybrid product-type classifier (M11 Stage 2) over every
listing already classified RELEVANT, and write results into the
listing_product_type_classification table.

Deliberately scoped to RELEVANT listings only -- product type is a
property of an in-category product, not a way to decide category
membership (PRINCIPLES.md "Listing != Product"). Does not touch
listing_classification (relevance) or the products table -- re-run
scripts/resolve_products.py afterward to flow these classifications into
the Product Master's product_type/product_type_confidence/
product_type_conflict fields (M11 Stage 3).

Usage: python scripts/classify_product_types.py [category_id]
Defaults to the denture_base pilot category.
"""

import sys
from collections import Counter

from dmie.classification.product_type_classifier import (
    CLASSIFIER_VERSION,
    DEFAULT_TAXONOMY_VERSION,
    ListingContext,
    classify_product_type,
)
from dmie.database.connection import get_connection
from dmie.database.repository import replace_listing_product_type_classifications
from dmie.database.runs import finish_run, record_classification_run, start_run


def run(category: str = "denture_base") -> None:
    con = get_connection()
    run_id = start_run(con, category, "product_type_classification", CLASSIFIER_VERSION)
    try:
        rows = con.execute(
            """
            SELECT l.listing_id, l.title, l.brand
            FROM listings l
            JOIN listing_classification lc ON lc.listing_id = l.listing_id
            WHERE lc.relevance_class = 'RELEVANT' AND l.category_id = ?
            """,
            [category],
        ).fetchall()
        contexts = [ListingContext(listing_id=r[0], title=r[1], brand=r[2]) for r in rows]
        results = [classify_product_type(ctx, category=category, taxonomy_version=DEFAULT_TAXONOMY_VERSION) for ctx in contexts]
        replace_listing_product_type_classifications(con, category, results)

        type_counts = Counter(r.product_type for r in results)
        method_counts = Counter(r.classifier_method for r in results)
        review_counts = Counter(r.review_status for r in results)
        record_classification_run(
            con, run_id, category, stage="product_type", classifier_version=CLASSIFIER_VERSION,
            total_classified=len(results), class_counts=dict(type_counts),
            auto_accepted_count=review_counts.get("auto_accepted", 0),
            needs_review_count=review_counts.get("needs_review", 0),
        )
        finish_run(con, run_id, summary={"product_type": dict(type_counts), "classifier_method": dict(method_counts)})
    except Exception as exc:
        finish_run(con, run_id, summary={}, error_message=str(exc))
        raise
    finally:
        con.close()

    print(f"run_id: {run_id}")
    print(f"classified: {len(results)} RELEVANT listings for category={category}")
    print(f"product_type: {dict(type_counts)}")
    print(f"classifier_method: {dict(method_counts)}")
    print(f"review_status: {dict(review_counts)}")


if __name__ == "__main__":
    category_arg = sys.argv[1] if len(sys.argv) > 1 else "denture_base"
    run(category_arg)
