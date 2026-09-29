"""AI Market Analyst (Tier 2, Milestone 18): a read-only Q&A layer over
data this project has already computed. Never a second, independent
"AI opinion" -- every fact available to the model is a real number
already sitting in product_market_metrics/category_market_metrics/
opportunity_signals, assembled once into `<context>` and handed to the
model with an explicit instruction not to use anything else. Reuses
dmie.ai.client.call_ai unchanged -- no new AI infrastructure, only a new
prompt and a new (read-only) context-building query.

Same graceful-degradation story as every other AI-using module in this
project: with no ANTHROPIC_API_KEY configured, `ask()` returns a
STATUS_UNAVAILABLE result rather than raising -- this has been true of
every AI stage since before this module existed, and stays true here.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from dmie.ai.client import STATUS_OK, call_ai

AI_MODEL = "claude-sonnet-4-5"
_PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "market_interpretation.md"

_PRODUCT_CONTEXT_COLUMNS = [
    "product_id", "product_name", "product_type", "brand",
    "representative_price", "best_listing_observed_monthly_sales",
    "observed_monthly_revenue", "rating", "total_listing_count", "opportunity_score",
]


def build_context(con: duckdb.DuckDBPyConnection, category_id: str) -> dict:
    """Every field here is a direct read of an already-computed,
    already-documented table -- nothing is derived or interpreted here.
    Returned as a plain dict so the caller (ask(), and the dashboard page
    for transparency) can both serialize it and show it verbatim."""
    category_row = con.execute(
        """
        SELECT total_product_count, total_listing_count, total_observed_monthly_sales,
               total_observed_monthly_revenue, listing_concentration_hhi, product_type_distribution,
               price_distribution, sales_distribution
        FROM category_market_metrics WHERE category_id = ?
        """,
        [category_id],
    ).fetchone()
    category_metrics = None
    if category_row:
        category_metrics = {
            "total_product_count": category_row[0],
            "total_listing_count": category_row[1],
            "total_observed_monthly_sales": category_row[2],
            "total_observed_monthly_revenue": category_row[3],
            "listing_concentration_hhi": category_row[4],
            "product_type_distribution": json.loads(category_row[5]) if category_row[5] else {},
            # Added after a real AI call correctly refused to compute a
            # median itself when only per-product prices were given --
            # these were already sitting in category_market_metrics
            # (Milestone 7), just not previously included here.
            "price_distribution": json.loads(category_row[6]) if category_row[6] else {},
            "sales_distribution": json.loads(category_row[7]) if category_row[7] else {},
        }

    product_rows = con.execute(
        """
        SELECT pmm.product_id, p.product_name, p.product_type, p.brand,
               pmm.representative_price, pmm.best_listing_observed_monthly_sales,
               pmm.observed_monthly_revenue, pmm.rating, pmm.total_listing_count, p.opportunity_score
        FROM product_market_metrics pmm
        LEFT JOIN products p ON p.product_id = pmm.product_id
        WHERE pmm.category_id = ?
        ORDER BY pmm.total_listing_count DESC
        """,
        [category_id],
    ).fetchall()
    products = [dict(zip(_PRODUCT_CONTEXT_COLUMNS, row)) for row in product_rows]

    signal_rows = con.execute(
        "SELECT product_id, signal_type, status, signal_strength FROM opportunity_signals "
        "WHERE category_id = ? AND status = 'signal_present'",
        [category_id],
    ).fetchall()
    signals_by_product: dict[str, list[dict]] = {}
    for product_id, signal_type, status, strength in signal_rows:
        if product_id is None:
            continue
        signals_by_product.setdefault(product_id, []).append(
            {"signal_type": signal_type, "status": status, "signal_strength": strength}
        )
    for product in products:
        product["active_signals"] = signals_by_product.get(product["product_id"], [])

    quality_row = con.execute(
        "SELECT COUNT(*), SUM(CASE WHEN lc.relevance_class = 'RELEVANT' THEN 1 ELSE 0 END) "
        "FROM listings l JOIN listing_classification lc ON lc.listing_id = l.listing_id "
        "WHERE l.category_id = ?",
        [category_id],
    ).fetchone()

    return {
        "category_id": category_id,
        "category_metrics": category_metrics,
        "products": products,
        "data_quality": {
            "total_listings": quality_row[0] if quality_row else 0,
            "relevant_listings": quality_row[1] if quality_row and quality_row[1] is not None else 0,
        },
    }


def ask(con: duckdb.DuckDBPyConnection, category_id: str, question: str) -> dict:
    """Returns a plain dict, always with a `status` key
    (dmie.ai.client's STATUS_* constants) and a `context` key (the exact
    data the model was given, or would have been given, for
    transparency regardless of outcome). On success, also has `answer`,
    `referenced_product_ids`, `insufficient_data`."""
    context = build_context(con, category_id)

    template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        template
        .replace("{MARKET_CONTEXT}", json.dumps(context, indent=2, default=str))
        .replace("{QUESTION}", question)
    )

    outcome = call_ai(prompt, model=AI_MODEL, max_tokens=800)
    result = {"status": outcome.status, "context": context}
    if outcome.status != STATUS_OK:
        result["error"] = outcome.error
        return result

    result["answer"] = outcome.data.get("answer")
    result["referenced_product_ids"] = outcome.data.get("referenced_product_ids", [])
    result["insufficient_data"] = outcome.data.get("insufficient_data", False)
    return result
