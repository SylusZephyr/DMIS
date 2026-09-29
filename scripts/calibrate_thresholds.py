"""Empirically calibrate the human-review confidence tiers against the
gold dataset, instead of assuming the 95%/70% example thresholds.
See docs/human_review_workflow.md.

Usage: python scripts/calibrate_thresholds.py
"""

from dmie.classification.calibration import recommend_tiers, sweep_thresholds
from dmie.classification.evaluation import load_gold, load_predictions
from dmie.database.connection import get_connection


def calibrate_relevance_classification() -> None:
    con = get_connection()
    try:
        gold = load_gold()
        predictions = load_predictions(con)
    finally:
        con.close()

    merged = gold.merge(predictions, on="listing_id", how="inner")
    confidences = merged["confidence"].tolist()
    correct = (merged["gold_class"] == merged["pred_class"]).tolist()

    points = sweep_thresholds(confidences, correct)
    recommendation = recommend_tiers(points)

    print("=== Relevance classification (gold set, n=%d) ===" % len(merged))
    print(f"{'threshold':>10}{'n>=t':>8}{'correct':>10}{'precision':>12}{'coverage':>10}")
    for p in points:
        precision_str = f"{p.precision:.1%}" if p.precision is not None else "n/a"
        print(f"{p.threshold:>10.2f}{p.n_at_or_above:>8}{p.n_correct_at_or_above:>10}{precision_str:>12}{p.coverage:>10.1%}")
    print()
    print(f"Recommended automatic threshold: {recommendation.automatic_threshold} "
          f"(precision {recommendation.automatic_precision:.1%})" if recommendation.automatic_threshold is not None
          else "Recommended automatic threshold: none met the target")
    print(f"Recommended sampling threshold: {recommendation.sampling_threshold} "
          f"(precision {recommendation.sampling_precision:.1%})" if recommendation.sampling_threshold is not None
          else "Recommended sampling threshold: none met the target")
    for w in recommendation.warnings:
        print(f"WARNING: {w}")


# The 5 manually-verified entity-resolution pairs from docs/entity_resolution.md
# -- a real but very small (n=5) gold reference for matching confidence.
_VERIFIED_PAIRS = [
    ("B0H14R7BBY", "B0H14ZK7HR", "MATCH"),
    ("B0GVB7J6PV", "B0GW5CNJ6F", "UNCERTAIN"),
    ("B0FQ1LTCSS", "B0GWTP6GQF", "UNCERTAIN"),
    ("B0GJTGD6HH", "B0GKB2KPHM", "UNCERTAIN"),
    ("B00VQTLM74", "B00E4MPAIW", "UNCERTAIN"),
]


def calibrate_entity_resolution() -> None:
    from dmie.cleaning.normalize import make_listing_id

    con = get_connection()
    try:
        rows = []
        for asin_a, asin_b, expected in _VERIFIED_PAIRS:
            lid_a, lid_b = make_listing_id(asin_a), make_listing_id(asin_b)
            row = con.execute(
                "SELECT decision, match_confidence FROM match_candidates "
                "WHERE (listing_id_a = ? AND listing_id_b = ?) OR (listing_id_a = ? AND listing_id_b = ?)",
                [lid_a, lid_b, lid_b, lid_a],
            ).fetchone()
            if row:
                rows.append((row[1], row[0] == expected))
    finally:
        con.close()

    if not rows:
        print("=== Entity resolution (verified pairs) === no data")
        return

    confidences = [r[0] for r in rows]
    correct = [r[1] for r in rows]
    points = sweep_thresholds(confidences, correct)
    recommendation = recommend_tiers(points)

    print(f"\n=== Entity resolution (verified pairs, n={len(rows)} -- too small for a robust threshold, directional only) ===")
    for p in points:
        precision_str = f"{p.precision:.1%}" if p.precision is not None else "n/a"
        print(f"threshold {p.threshold:.2f}: n>=t={p.n_at_or_above}, correct={p.n_correct_at_or_above}, precision={precision_str}")
    for w in recommendation.warnings:
        print(f"WARNING: {w}")


if __name__ == "__main__":
    calibrate_relevance_classification()
    calibrate_entity_resolution()
