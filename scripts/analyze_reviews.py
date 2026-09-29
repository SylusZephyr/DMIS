"""Extract review insights into the controlled taxonomy, with
deterministic frequency/severity aggregation.

This pipeline is fully built and tested (see tests/unit/test_extraction.py,
test_themes.py, test_aggregation_reviews.py) but the current
SellerSprite-derived dataset has NO review text at all — only a rating
number, no review count, and no review-text column (see
docs/data_dictionary.md §12). There is nothing real to run this against
yet. Point it at a JSON file shaped
[{"listing_id": ..., "product_id": ..., "review_text": ...}, ...] once a
real review-text source exists (e.g. a separate Amazon-review export).

Usage: python scripts/analyze_reviews.py [reviews.json]
"""

import json
import sys
from pathlib import Path

from dmie.database.connection import PROJECT_ROOT, get_connection
from dmie.database.repository import insert_review_insights
from dmie.reviews.aggregation import rejection_summary, theme_frequency, theme_severity
from dmie.reviews.extraction import extract_insight


def load_reviews(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def run(reviews_path: Path) -> None:
    reviews = load_reviews(reviews_path)
    if not reviews:
        print(f"No review text found at {reviews_path}.")
        print("This dataset's only source so far (SellerSprite) has no review-text column")
        print("(see docs/data_dictionary.md #12) -- nothing to extract yet.")
        print('Supply a JSON file of [{"listing_id": ..., "product_id": ..., "review_text": ...}, ...]')
        print("to run this pipeline for real.")
        return

    insights = [
        extract_insight(r["listing_id"], r.get("product_id"), r["review_text"])
        for r in reviews
    ]

    con = get_connection()
    try:
        insert_review_insights(con, insights)
    finally:
        con.close()

    insight_dicts = [i.__dict__ for i in insights]
    print(f"reviews processed: {len(reviews)}")
    print(f"extracted: {sum(1 for i in insights if i.status == 'extracted')}")
    print(f"rejected/skipped: {rejection_summary(insight_dicts)}")
    print(f"frequency by theme: {theme_frequency(insight_dicts)}")
    print(f"severity by theme: {theme_severity(insight_dicts)}")


if __name__ == "__main__":
    default_path = PROJECT_ROOT / "data" / "raw" / "denture_base" / "reviews.json"
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_path
    run(path)
