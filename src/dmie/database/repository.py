"""DuckDB read/write helpers for normalized listing records and the
decision log."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from dmie.classification.classifier import ClassificationResult
from dmie.cleaning.normalize import Decision
from dmie.market.aggregation import CategoryMetrics, ProductMetrics
from dmie.matching.resolution import CandidateResult
from dmie.reviews.extraction import ReviewInsight

_LISTING_COLUMNS = [
    "listing_id", "asin", "category_id", "title", "brand", "url", "image_url",
    "price", "monthly_sales", "monthly_revenue", "rating", "review_count",
    "raw_source_file", "created_at",
]

_CLASSIFICATION_COLUMNS = [
    "listing_id", "relevant", "relevance_class", "confidence", "product_type",
    "reason", "classifier_version", "prompt_version", "review_status", "created_at",
]

_PRODUCT_LISTING_COLUMNS = [
    "product_id", "listing_id", "match_method", "match_confidence",
    "is_best_listing", "review_status",
]

_PRODUCT_COLUMNS = [
    "product_id", "category_id", "product_name", "product_type",
    "product_type_confidence", "product_type_conflict", "product_family",
    "brand", "model", "representative_image", "confidence", "created_at",
]

_LISTING_PRODUCT_TYPE_COLUMNS = [
    "listing_id", "taxonomy_version", "product_type", "confidence",
    "reason", "classifier_method", "review_status", "created_at",
]


def upsert_category(con: duckdb.DuckDBPyConnection, category_id: str, leaf_category: str, marketplace: str,
                     parent_category: str | None = None) -> None:
    """Ensures `categories` has a row for `category_id` -- found live
    (2026-09-24, via the dashboard's Category Overview dropdown) that
    config/categories.yaml (the relevance/taxonomy config) and the
    `categories` DB table (what the dashboard and API actually read for
    their category picker) had no sync mechanism at all: a category
    could be fully onboarded -- ingested, classified, resolved, scored --
    and still be completely invisible in every UI, silently, with no
    error. parent_category is left NULL rather than guessed when no real
    grouping label exists in config (PRINCIPLES.md "None never a guess")."""
    now = datetime.now(timezone.utc)
    con.execute(
        """
        INSERT INTO categories (category_id, parent_category, leaf_category, marketplace, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (category_id) DO UPDATE SET
            parent_category = excluded.parent_category,
            leaf_category = excluded.leaf_category,
            marketplace = excluded.marketplace
        """,
        [category_id, parent_category, leaf_category, marketplace, now],
    )


def upsert_listings(con: duckdb.DuckDBPyConnection, listings: list[dict]) -> list:
    """Returns any Decision objects logging a cross-category conflict
    (caller is responsible for passing these to insert_decision_log,
    same as normalize_dataframe's own decisions) -- found via a real
    ~200MB merged multi-category export where the same ASIN legitimately
    appears under more than one raw category label (a cross-listed
    product, or overlapping source categories). Without this check, a
    later ingest for a different category silently stole the listing_id
    (its PRIMARY KEY) from whichever category owned it first -- no
    warning, no log, just a quietly shrinking category count (found via
    a real ~666-listing discrepancy in denture_base after ingesting two
    new categories). A conflicting row is left completely untouched
    (kept with its original category and fields) rather than guessing
    which category should win; the conflict is logged so it's visible
    and reviewable, never silently resolved either way."""
    if not listings:
        return []

    from dmie.cleaning.normalize import Decision

    incoming_ids = [rec["listing_id"] for rec in listings]
    placeholders = ", ".join("?" for _ in incoming_ids)
    existing = dict(con.execute(
        f"SELECT listing_id, category_id FROM listings WHERE listing_id IN ({placeholders})",
        incoming_ids,
    ).fetchall())

    conflicts = []
    safe = []
    for rec in listings:
        existing_category = existing.get(rec["listing_id"])
        if existing_category is not None and existing_category != rec["category_id"]:
            conflicts.append(Decision(
                entity_type="listing", entity_id=rec["listing_id"], decision_type="category_conflict_skipped",
                old_value=existing_category, new_value=rec["category_id"],
                reason=(
                    f"listing_id already exists under category '{existing_category}'; "
                    f"this ingest wanted to reassign it to '{rec['category_id']}' -- same ASIN present "
                    "under more than one raw category label in the source export. Kept the original "
                    "category untouched rather than silently reassigning it."
                ),
                actor="upsert_listings",
            ))
        else:
            safe.append(rec)

    if safe:
        set_clause = ", ".join(f"{c} = excluded.{c}" for c in _LISTING_COLUMNS if c != "listing_id")
        con.executemany(
            f"""
            INSERT INTO listings ({", ".join(_LISTING_COLUMNS)})
            VALUES ({", ".join("?" for _ in _LISTING_COLUMNS)})
            ON CONFLICT (listing_id) DO UPDATE SET {set_clause}
            """,
            [[rec[c] for c in _LISTING_COLUMNS] for rec in safe],
        )

    return conflicts


def insert_decision_log(con: duckdb.DuckDBPyConnection, decisions: list[Decision]) -> None:
    if not decisions:
        return
    now = datetime.now(timezone.utc)
    con.executemany(
        """
        INSERT INTO decision_log (
            decision_id, entity_type, entity_id, decision_type,
            old_value, new_value, reason, actor, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            [uuid.uuid4().hex, d.entity_type, d.entity_id, d.decision_type,
             d.old_value, d.new_value, d.reason, d.actor, now]
            for d in decisions
        ],
    )


def upsert_listing_classifications(
    con: duckdb.DuckDBPyConnection, results: list[ClassificationResult]
) -> None:
    if not results:
        return
    now = datetime.now(timezone.utc)
    set_clause = ", ".join(
        f"{c} = excluded.{c}" for c in _CLASSIFICATION_COLUMNS if c != "listing_id"
    )
    con.executemany(
        f"""
        INSERT INTO listing_classification ({", ".join(_CLASSIFICATION_COLUMNS)})
        VALUES ({", ".join("?" for _ in _CLASSIFICATION_COLUMNS)})
        ON CONFLICT (listing_id) DO UPDATE SET {set_clause}
        """,
        [
            [r.listing_id, r.relevant, r.relevance_class, r.confidence, r.product_type,
             r.reason, r.classifier_version, r.prompt_version, r.review_status, now]
            for r in results
        ],
    )


def replace_match_candidates(con: duckdb.DuckDBPyConnection, category: str, results: list[CandidateResult]) -> None:
    """match_candidates is a fully-derived audit trail of one resolution
    run — regenerated from scratch each run rather than accumulated.
    Scoped to `category`: match_candidates has no category_id column of
    its own (a pair is between two listings), so the scope is expressed
    via listings.category_id -- an unscoped DELETE here would wipe every
    other category's candidate pairs every time one category re-runs
    resolution (found when a second real category was added; see
    docs/decisions and DECISIONS.md)."""
    now = datetime.now(timezone.utc)
    con.execute(
        """
        DELETE FROM match_candidates
        WHERE listing_id_a IN (SELECT listing_id FROM listings WHERE category_id = ?)
           OR listing_id_b IN (SELECT listing_id FROM listings WHERE category_id = ?)
        """,
        [category, category],
    )
    if not results:
        return
    con.executemany(
        """
        INSERT INTO match_candidates (
            candidate_id, listing_id_a, listing_id_b, blocking_method,
            match_method, match_confidence, decision, review_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            [uuid.uuid4().hex, r.listing_id_a, r.listing_id_b, r.blocking_method,
             r.match_method, r.match_confidence, r.decision, r.review_status, now]
            for r in results
        ],
    )


def replace_product_listings(con: duckdb.DuckDBPyConnection, category: str, rows: list[dict]) -> None:
    """product_listings is fully derived from the current clustering —
    regenerated from scratch each run (product_id assignments can shift
    as new matches are found, so upserting by stale keys would leave
    orphaned rows behind). Scoped to `category` via listings.category_id,
    same reasoning as replace_match_candidates."""
    con.execute(
        "DELETE FROM product_listings WHERE listing_id IN "
        "(SELECT listing_id FROM listings WHERE category_id = ?)",
        [category],
    )
    if not rows:
        return
    con.executemany(
        f"""
        INSERT INTO product_listings ({", ".join(_PRODUCT_LISTING_COLUMNS)})
        VALUES ({", ".join("?" for _ in _PRODUCT_LISTING_COLUMNS)})
        """,
        [[r[c] for c in _PRODUCT_LISTING_COLUMNS] for r in rows],
    )


def replace_products(con: duckdb.DuckDBPyConnection, category: str, rows: list[dict]) -> None:
    """products is the Product Master (M12) -- fully derived from the
    current clustering, same regenerate-from-scratch pattern as
    product_listings/match_candidates. Scoped to `category`: products
    has its own category_id column, so this is a direct filter.

    Also deletes any existing row whose product_id collides with one of
    `rows`, regardless of that row's own category -- product_id is a
    global primary key derived purely from a listing_id's hash (see
    dmie.matching.resolution.cluster_products), not scoped to category.
    A listing that has moved category (found live: the ~200MB merged
    export's cross-category ASIN collisions, see PROGRESS.md) leaves a
    stale products row under its OLD category rooted on the same
    listing_id -- that row is definitionally stale once the listing
    itself has moved, so removing it here is safe, not merely a
    workaround for the resulting PRIMARY KEY conflict."""
    now = datetime.now(timezone.utc)
    con.execute("DELETE FROM products WHERE category_id = ?", [category])
    if not rows:
        return
    incoming_ids = [r["product_id"] for r in rows]
    placeholders = ", ".join("?" for _ in incoming_ids)
    con.execute(f"DELETE FROM products WHERE product_id IN ({placeholders})", incoming_ids)
    con.executemany(
        f"""
        INSERT INTO products ({", ".join(_PRODUCT_COLUMNS)})
        VALUES ({", ".join("?" for _ in _PRODUCT_COLUMNS)})
        """,
        [[{**r, "created_at": now}[c] for c in _PRODUCT_COLUMNS] for r in rows],
    )


def replace_listing_product_type_classifications(con: duckdb.DuckDBPyConnection, category: str, results: list) -> None:
    """listing_product_type_classification (M11 Stage 2/3) -- one row per
    classified listing, regenerated from scratch each classification run
    (same pattern as match_candidates/product_listings). Deliberately its
    own table, not a write into listing_classification's confidence/
    reason/review_status columns -- see schema.sql's comment on this
    table for why reusing those would silently corrupt the relevance
    pipeline's own data. `results`: ProductTypeResult objects. Scoped to
    `category` via listings.category_id, same reasoning as
    replace_match_candidates."""
    now = datetime.now(timezone.utc)
    con.execute(
        "DELETE FROM listing_product_type_classification WHERE listing_id IN "
        "(SELECT listing_id FROM listings WHERE category_id = ?)",
        [category],
    )
    if not results:
        return
    con.executemany(
        f"""
        INSERT INTO listing_product_type_classification ({", ".join(_LISTING_PRODUCT_TYPE_COLUMNS)})
        VALUES ({", ".join("?" for _ in _LISTING_PRODUCT_TYPE_COLUMNS)})
        """,
        [
            [{**r.__dict__, "created_at": now}[c] for c in _LISTING_PRODUCT_TYPE_COLUMNS]
            for r in results
        ],
    )


_PRODUCT_METRIC_COLUMNS = [
    "product_id", "category_id", "total_listing_count", "listings_with_price_data",
    "listings_with_sales_data", "best_listing_id", "best_listing_observed_monthly_sales",
    "best_listing_annualized_observed_sales", "observed_monthly_revenue",
    "annualized_observed_revenue", "min_price", "max_price", "median_price",
    "representative_price", "rating", "review_count", "calculated_at",
]

_CATEGORY_METRIC_COLUMNS = [
    "category_id", "total_product_count", "total_listing_count",
    "total_observed_monthly_sales", "annualized_observed_sales",
    "total_observed_monthly_revenue", "annualized_observed_revenue",
    "price_distribution", "sales_distribution", "listing_concentration_hhi",
    "product_type_distribution", "calculated_at",
]


def replace_product_market_metrics(con: duckdb.DuckDBPyConnection, category: str, metrics: list[ProductMetrics]) -> None:
    """Fully derived from the current listings/classification/product
    clustering — regenerated from scratch each run, like match_candidates
    and product_listings. Scoped to `category`: product_market_metrics
    has its own category_id column, so this is a direct filter."""
    now = datetime.now(timezone.utc)
    con.execute("DELETE FROM product_market_metrics WHERE category_id = ?", [category])
    if not metrics:
        return
    values = []
    for m in metrics:
        row = {**m.__dict__, "calculated_at": now}
        values.append([row[c] for c in _PRODUCT_METRIC_COLUMNS])
    con.executemany(
        f"INSERT INTO product_market_metrics ({', '.join(_PRODUCT_METRIC_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in _PRODUCT_METRIC_COLUMNS)})",
        values,
    )


def replace_category_market_metrics(con: duckdb.DuckDBPyConnection, category: str, metrics: list[CategoryMetrics]) -> None:
    """Scoped to `category` (direct category_id filter) -- an unscoped
    DELETE here previously wiped every other category's row on each run,
    invisible until a second real category existed."""
    now = datetime.now(timezone.utc)
    con.execute("DELETE FROM category_market_metrics WHERE category_id = ?", [category])
    if not metrics:
        return
    values = []
    for m in metrics:
        row = {**m.__dict__, "calculated_at": now}
        row["price_distribution"] = json.dumps(row["price_distribution"])
        row["sales_distribution"] = json.dumps(row["sales_distribution"])
        row["product_type_distribution"] = json.dumps(row["product_type_distribution"])
        values.append([row[c] for c in _CATEGORY_METRIC_COLUMNS])
    con.executemany(
        f"INSERT INTO category_market_metrics ({', '.join(_CATEGORY_METRIC_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in _CATEGORY_METRIC_COLUMNS)})",
        values,
    )


def update_best_listing_flags(con: duckdb.DuckDBPyConnection, category: str, metrics: list[ProductMetrics]) -> None:
    """Sets product_listings.is_best_listing for every listing based on
    this run's best-selling-listing determination (sales.py::best_selling_listing).
    Cleared to FALSE first so a listing that loses its "best" status in a
    later run doesn't stay stuck TRUE -- scoped to `category` via
    listings.category_id so this doesn't clear other categories' flags."""
    con.execute(
        "UPDATE product_listings SET is_best_listing = FALSE WHERE listing_id IN "
        "(SELECT listing_id FROM listings WHERE category_id = ?)",
        [category],
    )
    best_ids = [m.best_listing_id for m in metrics if m.best_listing_id]
    if not best_ids:
        return
    con.executemany(
        "UPDATE product_listings SET is_best_listing = TRUE WHERE listing_id = ?",
        [[lid] for lid in best_ids],
    )


def insert_review_insights(con: duckdb.DuckDBPyConnection, insights: list[ReviewInsight]) -> None:
    """Only status='extracted' insights (passed the evidence + taxonomy
    guardrails) go into review_insights. Everything else (rejected,
    ai_unavailable, no_pain_point) is logged to decision_log instead of
    silently dropped — PRINCIPLES.md principle 8."""
    now = datetime.now(timezone.utc)
    extracted = [i for i in insights if i.status == "extracted"]
    other = [i for i in insights if i.status != "extracted"]

    if extracted:
        con.executemany(
            """
            INSERT INTO review_insights (
                insight_id, product_id, listing_id, pain_point, attribute,
                severity, evidence_text, improvement_opportunity, confidence,
                model_version, prompt_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                [uuid.uuid4().hex, i.product_id, i.listing_id,
                 f"{i.pain_point_category}/{i.pain_point_subcategory}", i.affected_attribute,
                 i.severity, i.evidence_text, i.improvement_opportunity, i.confidence,
                 i.model_version, i.prompt_version, now]
                for i in extracted
            ],
        )

    if other:
        con.executemany(
            """
            INSERT INTO decision_log (
                decision_id, entity_type, entity_id, decision_type,
                old_value, new_value, reason, actor, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                [uuid.uuid4().hex, "review_insight", i.listing_id, i.status,
                 i.evidence_text, None, i.rejection_reason, "review_extraction_pipeline", now]
                for i in other
            ],
        )


def replace_opportunity_signals(con: duckdb.DuckDBPyConnection, category: str, records: list[dict]) -> None:
    """Fully derived from the current market/review data -- regenerated
    from scratch each run, same pattern as match_candidates and the
    market metrics tables. `records` are evidence.py::build_signal_record()
    dicts: product_id, category_id, signal_type, status, signal_strength,
    confidence, evidence, supporting_metrics, supporting_review_themes.
    Scoped to `category` (direct category_id filter)."""
    now = datetime.now(timezone.utc)
    con.execute("DELETE FROM opportunity_signals WHERE category_id = ?", [category])
    if not records:
        return
    con.executemany(
        """
        INSERT INTO opportunity_signals (
            signal_id, product_id, category_id, signal_type, status, signal_strength,
            confidence, evidence, supporting_metrics, supporting_review_themes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            [uuid.uuid4().hex, r["product_id"], r["category_id"], r["signal_type"], r["status"],
             r["signal_strength"], r["confidence"], json.dumps(r["evidence"]),
             json.dumps(r["supporting_metrics"]), json.dumps(r["supporting_review_themes"]), now]
            for r in records
        ],
    )


def update_opportunity_scores(con: duckdb.DuckDBPyConnection, category: str, scores: dict[str, float | None]) -> None:
    """Sets products.opportunity_score (Tier 2, Milestone 9) from
    src/dmie/opportunity/scoring.py::compute_opportunity_score(), one
    per product. Cleared to NULL first -- same reasoning as
    update_best_listing_flags: a product whose signals no longer
    support a score (e.g. now insufficient_data) must not stay stuck at
    a stale value from a previous run. `scores` maps product_id -> score
    (score may be None, meaning "not enough evaluated signals yet" --
    explicitly written as NULL, not skipped, so a product that used to
    have a score and no longer does is correctly cleared). Scoped to
    `category` (direct category_id filter) so this doesn't clear other
    categories' scores."""
    con.execute("UPDATE products SET opportunity_score = NULL WHERE category_id = ?", [category])
    if not scores:
        return
    con.executemany(
        "UPDATE products SET opportunity_score = ? WHERE product_id = ?",
        [[score, product_id] for product_id, score in scores.items()],
    )


def export_listings_parquet(
    con: duckdb.DuckDBPyConnection, out_path: str, raw_source_file: str | None = None
) -> None:
    """Export listings to Parquet. Never targets data/raw/ (see PRINCIPLES.md
    principle 3) — mixing a parameterized WHERE clause with a parameterized
    COPY destination in one statement was found to bind them out of order in
    DuckDB, so the destination is always a separately-escaped literal, never
    a bound parameter."""
    resolved = Path(out_path).resolve()
    if "data" in resolved.parts and "raw" in resolved.parts:
        raise ValueError(f"refusing to write parquet export into data/raw/: {resolved}")

    escaped_path = str(resolved).replace("'", "''")
    if raw_source_file:
        escaped_source = raw_source_file.replace("'", "''")
        con.execute(
            f"COPY (SELECT * FROM listings WHERE raw_source_file = '{escaped_source}') "
            f"TO '{escaped_path}' (FORMAT PARQUET)"
        )
    else:
        con.execute(f"COPY listings TO '{escaped_path}' (FORMAT PARQUET)")
