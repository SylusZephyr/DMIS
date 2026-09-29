"""Run versioning and data provenance (Milestone 14).

Every pipeline execution gets a `run_id` and is recorded in
`pipeline_runs` (append-only -- never deleted or overwritten by a later
run), plus a stage-specific detail row in `classification_runs` or
`product_resolution_runs` with the comparable counts a human would
actually want when asking "what changed between run A and run B."

This module does NOT change how the pipeline's DATA tables themselves are
written (they still regenerate in place on each run -- see
DECISIONS.md "M14" for why that's a deliberately separate, larger change
not part of this milestone). It only adds a permanent log of what each
run produced.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import duckdb

STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"


def start_run(con: duckdb.DuckDBPyConnection, category_id: str, pipeline_stage: str, pipeline_version: str) -> str:
    """Call at the very start of a pipeline script. Returns the new
    run_id -- pass it to record_classification_run/
    record_product_resolution_run and finish_run."""
    run_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    con.execute(
        """
        INSERT INTO pipeline_runs (
            run_id, category_id, pipeline_stage, pipeline_version,
            started_at, completed_at, status, error_message, summary, created_at
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?)
        """,
        [run_id, category_id, pipeline_stage, pipeline_version, now, STATUS_RUNNING, now],
    )
    return run_id


def finish_run(con: duckdb.DuckDBPyConnection, run_id: str, summary: dict, error_message: str | None = None) -> None:
    """Call once at the end of a pipeline script, in a `finally` block so
    a run that raised is still recorded as `failed` rather than left
    stuck at `running` forever."""
    status = STATUS_FAILED if error_message else STATUS_SUCCESS
    con.execute(
        "UPDATE pipeline_runs SET completed_at = ?, status = ?, error_message = ?, summary = ? WHERE run_id = ?",
        [datetime.now(timezone.utc), status, error_message, json.dumps(summary), run_id],
    )


def record_classification_run(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    category_id: str,
    stage: str,  # "relevance" | "product_type"
    classifier_version: str,
    total_classified: int,
    class_counts: dict,
    auto_accepted_count: int,
    needs_review_count: int,
) -> None:
    con.execute(
        """
        INSERT INTO classification_runs (
            run_id, category_id, stage, classifier_version,
            total_classified, class_counts, auto_accepted_count, needs_review_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [run_id, category_id, stage, classifier_version, total_classified,
         json.dumps(class_counts), auto_accepted_count, needs_review_count, datetime.now(timezone.utc)],
    )


def record_product_resolution_run(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    category_id: str,
    matching_version: str,
    total_listings: int,
    candidate_pairs: int,
    match_count: int,
    no_match_count: int,
    uncertain_count: int,
    resulting_products: int,
) -> None:
    con.execute(
        """
        INSERT INTO product_resolution_runs (
            run_id, category_id, matching_version, total_listings, candidate_pairs,
            match_count, no_match_count, uncertain_count, resulting_products, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [run_id, category_id, matching_version, total_listings, candidate_pairs,
         match_count, no_match_count, uncertain_count, resulting_products, datetime.now(timezone.utc)],
    )


def list_runs(con: duckdb.DuckDBPyConnection, category_id: str | None = None, pipeline_stage: str | None = None) -> list[dict]:
    """Every run recorded so far, newest first -- "what did the last N
    runs of category X produce" starts here."""
    query = "SELECT * FROM pipeline_runs"
    conditions, params = [], []
    if category_id is not None:
        conditions.append("category_id = ?")
        params.append(category_id)
    if pipeline_stage is not None:
        conditions.append("pipeline_stage = ?")
        params.append(pipeline_stage)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY started_at DESC"
    rows = con.execute(query, params).fetchall()
    columns = [d[0] for d in con.description]
    return [dict(zip(columns, row)) for row in rows]


def compare_runs(con: duckdb.DuckDBPyConnection, run_id_a: str, run_id_b: str) -> dict:
    """How to compare two pipeline runs: look up each run's
    `pipeline_runs` row plus whichever stage-detail row exists for it
    (classification_runs or product_resolution_runs), then diff every
    shared numeric field. Returns {run_a, run_b, differences}, where
    `differences` maps field -> (value_in_a, value_in_b) for every field
    that actually changed -- an empty dict means the two runs produced
    identical counts."""
    run_a = _get_run_with_detail(con, run_id_a)
    run_b = _get_run_with_detail(con, run_id_b)
    if run_a is None or run_b is None:
        raise ValueError(f"run not found: {run_id_a if run_a is None else run_id_b}")

    # run_id/created_at trivially differ between any two runs -- not a
    # meaningful business difference, so excluded from the comparison.
    identity_fields = {"run_id", "created_at"}

    differences = {}
    shared_fields = (set(run_a["detail"] or {}) & set(run_b["detail"] or {})) - identity_fields
    for field in sorted(shared_fields):
        value_a, value_b = run_a["detail"][field], run_b["detail"][field]
        if value_a != value_b:
            differences[field] = (value_a, value_b)

    return {"run_a": run_a, "run_b": run_b, "differences": differences}


def _get_run_with_detail(con: duckdb.DuckDBPyConnection, run_id: str) -> dict | None:
    row = con.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", [run_id]).fetchone()
    if row is None:
        return None
    columns = [d[0] for d in con.description]
    run = dict(zip(columns, row))

    detail_row = con.execute("SELECT * FROM classification_runs WHERE run_id = ?", [run_id]).fetchone()
    if detail_row is not None:
        detail_columns = [d[0] for d in con.description]
        run["detail"] = dict(zip(detail_columns, detail_row))
        return run

    detail_row = con.execute("SELECT * FROM product_resolution_runs WHERE run_id = ?", [run_id]).fetchone()
    if detail_row is not None:
        detail_columns = [d[0] for d in con.description]
        run["detail"] = dict(zip(detail_columns, detail_row))
        return run

    run["detail"] = None
    return run
