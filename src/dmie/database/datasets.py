"""Dataset upload lifecycle + version registry (Milestone 16).

Same style as runs.py's pipeline-run provenance: plain dict rows in,
plain dict rows out, one function per state transition. dataset_uploads
tracks one upload through staged -> validated -> approved -> ingested (or
rejected); dataset_versions is the permanent, append-only record of every
dataset that actually reached `listings` -- see schema.sql for why these
are two separate tables.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import duckdb

STATUS_STAGED = "staged"
STATUS_VALIDATED = "validated"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_INGESTED = "ingested"


def create_upload(
    con: duckdb.DuckDBPyConnection,
    category_id: str,
    dataset_type: str,
    version: str,
    original_filename: str,
    staging_path: str,
    row_count: int,
    upload_id: str | None = None,
) -> str:
    """Call right after a file is saved to data/staging/ -- never
    data/raw/ yet. Returns the upload_id (generated here unless the
    caller already needed one, e.g. to name the staged file, in which
    case it passes that same id through)."""
    upload_id = upload_id or uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    con.execute(
        """
        INSERT INTO dataset_uploads (
            upload_id, category_id, dataset_type, version, original_filename,
            staging_path, raw_path, row_count, status, validation_status,
            validation_report, quality_score, pipeline_run_id, uploaded_at, validated_at, approved_at, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, NULL, NULL, NULL, ?, NULL, NULL, NULL)
        """,
        [upload_id, category_id, dataset_type, version, original_filename,
         staging_path, row_count, STATUS_STAGED, now],
    )
    return upload_id


def record_validation(
    con: duckdb.DuckDBPyConnection, upload_id: str, validation_status: str, report: dict, quality_score: float = 0.0
) -> None:
    con.execute(
        "UPDATE dataset_uploads SET status = ?, validation_status = ?, validation_report = ?, "
        "quality_score = ?, validated_at = ? WHERE upload_id = ?",
        [STATUS_VALIDATED, validation_status, json.dumps(report), quality_score,
         datetime.now(timezone.utc), upload_id],
    )


def approve_upload(con: duckdb.DuckDBPyConnection, upload_id: str, raw_path: str) -> None:
    """Call once the staged file has been copied to its permanent,
    immutable data/raw/ location (see PRINCIPLES.md principle 3) -- raw_path
    is that final location, never the staging path."""
    con.execute(
        "UPDATE dataset_uploads SET status = ?, raw_path = ?, approved_at = ? WHERE upload_id = ?",
        [STATUS_APPROVED, raw_path, datetime.now(timezone.utc), upload_id],
    )


def reject_upload(con: duckdb.DuckDBPyConnection, upload_id: str) -> None:
    con.execute("UPDATE dataset_uploads SET status = ? WHERE upload_id = ?", [STATUS_REJECTED, upload_id])


def mark_ingested(con: duckdb.DuckDBPyConnection, upload_id: str, pipeline_run_id: str) -> None:
    con.execute(
        "UPDATE dataset_uploads SET status = ?, pipeline_run_id = ?, ingested_at = ? WHERE upload_id = ?",
        [STATUS_INGESTED, pipeline_run_id, datetime.now(timezone.utc), upload_id],
    )


def get_upload(con: duckdb.DuckDBPyConnection, upload_id: str) -> dict | None:
    row = con.execute("SELECT * FROM dataset_uploads WHERE upload_id = ?", [upload_id]).fetchone()
    if row is None:
        return None
    columns = [d[0] for d in con.description]
    record = dict(zip(columns, row))
    if record["validation_report"]:
        record["validation_report"] = json.loads(record["validation_report"])
    return record


def list_uploads(con: duckdb.DuckDBPyConnection, category_id: str | None = None) -> list[dict]:
    query = "SELECT * FROM dataset_uploads"
    params: list = []
    if category_id is not None:
        query += " WHERE category_id = ?"
        params.append(category_id)
    query += " ORDER BY uploaded_at DESC"
    rows = con.execute(query, params).fetchall()
    columns = [d[0] for d in con.description]
    return [dict(zip(columns, row)) for row in rows]


def register_dataset_version(
    con: duckdb.DuckDBPyConnection,
    category_id: str,
    source: str,
    version: str,
    upload_id: str,
    record_count: int,
) -> str:
    """Call once an upload has fully completed the pipeline (i.e. right
    after mark_ingested) -- this is the permanent version-history entry,
    never updated or deleted afterward."""
    version_id = uuid.uuid4().hex
    con.execute(
        """
        INSERT INTO dataset_versions (version_id, category_id, source, version, upload_id, record_count, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [version_id, category_id, source, version, upload_id, record_count, datetime.now(timezone.utc)],
    )
    return version_id


def list_dataset_versions(con: duckdb.DuckDBPyConnection, category_id: str | None = None) -> list[dict]:
    query = "SELECT * FROM dataset_versions"
    params: list = []
    if category_id is not None:
        query += " WHERE category_id = ?"
        params.append(category_id)
    query += " ORDER BY created_at DESC"
    rows = con.execute(query, params).fetchall()
    columns = [d[0] for d in con.description]
    return [dict(zip(columns, row)) for row in rows]


def next_version_label(con: duckdb.DuckDBPyConnection, category_id: str, dataset_type: str) -> str:
    """Suggests "v1", "v2", ... for the dashboard's version field --
    counts existing dataset_versions rows for this category, doesn't
    invent a numbering scheme the user can't override."""
    count = con.execute(
        "SELECT COUNT(*) FROM dataset_versions WHERE category_id = ?", [category_id]
    ).fetchone()[0]
    return f"v{count + 1}"
