"""ML/similarity tier (architecture directive's cascade: rules -> ML/
similarity -> LLM -> human): runs AFTER scripts/classify_product_types.py,
as an additive resolution pass over whatever it left UNCERTAIN.

Builds a TF-IDF reference index from this category's real, already-
confidently-resolved listings (classifier_method='rules', the ones
apply_rules already trusted), then tries nearest-neighbor similarity on
every UNCERTAIN listing. A listing that closely resembles an
already-labeled example gets promoted out of the review queue; anything
that doesn't stays exactly as UNCERTAIN/needs_review as before -- this
script never invents a type for a listing that doesn't clearly resemble
a real labeled example (min_confidence is a real, inspectable
threshold, not silently lowered to force more matches through).

Writes results with review_status='sample_for_qa' (not 'auto_accepted')
-- a nearest-neighbor match is real signal, but weaker than an exact
rule match or a real AI judgment, so it stays flagged for periodic
human spot-checking rather than treated as equally trustworthy.

Usage: python scripts/apply_similarity_tier.py <category_id> [min_confidence]
"""

import sys

from dmie.classification.similarity_tier import build_similarity_index, classify_by_similarity
from dmie.database.connection import get_connection

DEFAULT_MIN_CONFIDENCE = 0.6


def run(category: str, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> None:
    con = get_connection()
    try:
        reference_rows = con.execute(
            """
            SELECT l.title, ptc.product_type
            FROM listing_product_type_classification ptc
            JOIN listings l ON l.listing_id = ptc.listing_id
            WHERE l.category_id = ? AND ptc.classifier_method = 'rules'
            """,
            [category],
        ).fetchall()
        uncertain_rows = con.execute(
            """
            SELECT ptc.listing_id, l.title
            FROM listing_product_type_classification ptc
            JOIN listings l ON l.listing_id = ptc.listing_id
            WHERE l.category_id = ? AND ptc.classifier_method = 'unavailable' AND ptc.product_type = 'UNCERTAIN'
            """,
            [category],
        ).fetchall()

        if not reference_rows:
            print(f"no rule-matched reference listings exist yet for category={category} -- nothing to build an index from")
            return
        if not uncertain_rows:
            print(f"no UNCERTAIN listings to resolve for category={category}")
            return

        titles, labels = zip(*reference_rows)
        index = build_similarity_index(list(titles), list(labels))

        now_resolved = []
        still_uncertain = 0
        for listing_id, title in uncertain_rows:
            result = classify_by_similarity(title, index, min_confidence=min_confidence, reference_titles=list(titles))
            if result.status == "ok":
                now_resolved.append((listing_id, result.label, result.confidence, result.basis))
            else:
                still_uncertain += 1

        if now_resolved:
            con.executemany(
                "UPDATE listing_product_type_classification SET product_type = ?, confidence = ?, "
                "reason = ?, classifier_method = 'similarity', review_status = 'sample_for_qa' "
                "WHERE listing_id = ?",
                [[label, conf, basis, lid] for lid, label, conf, basis in now_resolved],
            )
    finally:
        con.close()

    print(f"category: {category}")
    print(f"reference (rule-matched) listings: {len(reference_rows)}")
    print(f"UNCERTAIN listings considered: {len(uncertain_rows)}")
    print(f"resolved by similarity tier: {len(now_resolved)} (min_confidence={min_confidence})")
    print(f"still UNCERTAIN (deferred, would need real AI): {still_uncertain}")


if __name__ == "__main__":
    category_arg = sys.argv[1]
    min_conf_arg = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MIN_CONFIDENCE
    run(category_arg, min_conf_arg)
