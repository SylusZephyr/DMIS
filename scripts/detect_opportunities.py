"""Detect evidence-based opportunity signals for every product (and the
category as a whole) — never a single "AI verdict" score. Every signal
carries specific conditions (real values and thresholds) plus separated
supporting_metrics / supporting_review_themes evidence arrays. See
docs/opportunity_signals.md.

Usage: python scripts/detect_opportunities.py [category_id]
Defaults to the denture_base pilot category.
"""

import json
import sys
from collections import Counter

import yaml

from dmie.database.connection import PROJECT_ROOT, get_connection
from dmie.database.repository import replace_opportunity_signals, update_opportunity_scores
from dmie.opportunity.evidence import build_signal_record
from dmie.opportunity.scoring import compute_opportunity_score
from dmie.opportunity.signals import (
    detect_bundle_signal,
    detect_competitive_concentration_signal,
    detect_customer_pain_point_signal,
    detect_price_segment_signal,
    detect_product_improvement_signal,
    detect_underrepresented_product_type_signal,
    looks_like_bundle,
)

_THRESHOLDS_PATH = PROJECT_ROOT / "config" / "thresholds.yaml"

_PRODUCT_METRIC_COLUMNS = [
    "product_id", "category_id", "total_listing_count", "listings_with_price_data",
    "listings_with_sales_data", "best_listing_id", "best_listing_observed_monthly_sales",
    "best_listing_annualized_observed_sales", "observed_monthly_revenue",
    "annualized_observed_revenue", "min_price", "max_price", "median_price",
    "representative_price", "rating", "review_count", "calculated_at",
]


def load_thresholds() -> dict:
    return yaml.safe_load(_THRESHOLDS_PATH.read_text(encoding="utf-8"))["opportunity"]


def run(category_id: str) -> None:
    thresholds = load_thresholds()
    con = get_connection()
    try:
        # fetchall() + manual dict, not .df() -- pandas represents SQL NULL
        # as NaN (a float; `is None` is False for it), which silently broke
        # every downstream "no data" check the first time this was built.
        product_rows = [
            dict(zip(_PRODUCT_METRIC_COLUMNS, row))
            for row in con.execute(
                f"SELECT {', '.join(_PRODUCT_METRIC_COLUMNS)} FROM product_market_metrics WHERE category_id = ?",
                [category_id],
            ).fetchall()
        ]

        category_demand_values = [
            row["best_listing_observed_monthly_sales"]
            if row["best_listing_observed_monthly_sales"] is not None
            else row["observed_monthly_revenue"]
            for row in product_rows
        ]

        insights_by_product: dict[str, list[dict]] = {}
        insight_rows = con.execute(
            "SELECT product_id, pain_point, severity, evidence_text FROM review_insights WHERE product_id IN "
            "(SELECT product_id FROM product_market_metrics WHERE category_id = ?)", [category_id]
        ).fetchall()
        for product_id, pain_point, severity, evidence_text in insight_rows:
            insights_by_product.setdefault(product_id, []).append({
                "product_id": product_id, "status": "extracted",
                "pain_point_category": pain_point.split("/")[0] if pain_point else None,
                "pain_point_subcategory": pain_point.split("/")[1] if pain_point and "/" in pain_point else None,
                "severity": severity, "evidence_text": evidence_text,
            })

        accessory_listing_count = con.execute(
            """
            SELECT COUNT(*) FROM listing_classification lc
            JOIN listings l ON l.listing_id = lc.listing_id
            WHERE l.category_id = ? AND lc.reason = 'ACCESSORY_ONLY'
            """,
            [category_id],
        ).fetchone()[0]

        titles = [row[0] for row in con.execute(
            "SELECT title FROM listings WHERE category_id = ?", [category_id]
        ).fetchall()]
        bundle_listing_fraction = (
            sum(1 for t in titles if looks_like_bundle(t)) / len(titles) if titles else 0.0
        )

        category_row = con.execute(
            "SELECT total_listing_count, price_distribution, product_type_distribution, listing_concentration_hhi "
            "FROM category_market_metrics WHERE category_id = ?", [category_id]
        ).fetchone()

        records = []

        # --- product-level signals ---
        for row in product_rows:
            product_insights = insights_by_product.get(row["product_id"], [])
            severities = [i["severity"] for i in product_insights if i.get("severity") is not None]
            mean_severity = sum(severities) / len(severities) if severities else None

            improvement = detect_product_improvement_signal(row, category_demand_values, len(product_insights), mean_severity, thresholds)
            bundle = detect_bundle_signal(row, category_demand_values, accessory_listing_count, bundle_listing_fraction, thresholds)
            records.append(build_signal_record(improvement, row, product_insights))
            records.append(build_signal_record(bundle, row, product_insights))

            by_theme: dict[str, list[dict]] = {}
            for insight in product_insights:
                if insight["pain_point_category"] and insight["pain_point_subcategory"]:
                    theme = f"{insight['pain_point_category']}/{insight['pain_point_subcategory']}"
                    by_theme.setdefault(theme, []).append(insight)
            for theme, theme_insights in by_theme.items():
                theme_severities = [i["severity"] for i in theme_insights if i.get("severity") is not None]
                theme_mean_severity = sum(theme_severities) / len(theme_severities) if theme_severities else None
                pain_point_signal = detect_customer_pain_point_signal(
                    category_id, row["product_id"], theme, len(theme_insights), theme_mean_severity, thresholds
                )
                records.append(build_signal_record(pain_point_signal, row, theme_insights))

        # --- category-level signals (product_id = None) ---
        if category_row:
            total_listings, price_dist_json, type_dist_json, hhi = category_row
            price_dist = json.loads(price_dist_json) if price_dist_json else {}
            type_dist = json.loads(type_dist_json) if type_dist_json else {}

            for band_label, band_count in price_dist.get("bands", {}).items():
                signal = detect_price_segment_signal(
                    category_id, band_label, band_count, price_dist.get("count", 0), thresholds["min_products_for_percentile"]
                )
                records.append(build_signal_record(signal, {}, []))

            type_signal = detect_underrepresented_product_type_signal(category_id, type_dist)
            records.append(build_signal_record(type_signal, {}, []))

            concentration_signal = detect_competitive_concentration_signal(category_id, hhi, total_listings)
            records.append(build_signal_record(concentration_signal, {}, []))

        replace_opportunity_signals(con, category_id, records)

        # Opportunity Score (Tier 2, M9): aggregate this run's own
        # product-scoped signal records per product_id -- no re-query,
        # no recomputation, purely a summary of what was just persisted.
        records_by_product: dict[str, list[dict]] = {}
        for r in records:
            if r["product_id"]:
                records_by_product.setdefault(r["product_id"], []).append(r)
        scores = {pid: compute_opportunity_score(recs) for pid, recs in records_by_product.items()}
        update_opportunity_scores(con, category_id, scores)
    finally:
        con.close()

    status_counts = Counter(r["status"] for r in records)
    by_type_status = Counter((r["signal_type"], r["status"]) for r in records)

    scored = sum(1 for s in scores.values() if s is not None)
    print(f"category: {category_id}")
    print(f"products evaluated: {len(product_rows)}")
    print(f"signals computed: {len(records)}")
    print(f"status breakdown: {dict(status_counts)}")
    print(f"by type/status: {dict(by_type_status)}")
    print(f"opportunity scores: {scored}/{len(scores)} products scored (rest insufficient_data)")


if __name__ == "__main__":
    category = sys.argv[1] if len(sys.argv) > 1 else "denture_base"
    run(category)
