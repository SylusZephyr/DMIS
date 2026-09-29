"""Milestone 16 -- dataset upload lifecycle + version registry.
dataset_uploads tracks one upload through staged -> validated -> approved
-> ingested; dataset_versions is the permanent record of what actually
reached `listings`. Same in-memory-DB pattern as test_pipeline_runs.py.
"""

import duckdb
import pytest

from dmie.database.connection import PROJECT_ROOT
from dmie.database.datasets import (
    STATUS_APPROVED,
    STATUS_INGESTED,
    STATUS_REJECTED,
    STATUS_STAGED,
    STATUS_VALIDATED,
    approve_upload,
    create_upload,
    get_upload,
    list_dataset_versions,
    list_uploads,
    mark_ingested,
    next_version_label,
    record_validation,
    register_dataset_version,
    reject_upload,
)

SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"


@pytest.fixture
def con():
    connection = duckdb.connect(":memory:")
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield connection
    connection.close()


def _stage(con, **overrides):
    kwargs = dict(
        category_id="denture_base", dataset_type="full_export", version="v1",
        original_filename="export.xlsx", staging_path="/tmp/fake/export.xlsx", row_count=105,
    )
    kwargs.update(overrides)
    return create_upload(con, **kwargs)


# --- Upload lifecycle ---

def test_create_upload_starts_in_staged_status(con):
    upload_id = _stage(con)
    record = get_upload(con, upload_id)
    assert record["status"] == STATUS_STAGED
    assert record["category_id"] == "denture_base"
    assert record["raw_path"] is None
    assert record["uploaded_at"] is not None


def test_create_upload_accepts_a_caller_supplied_id():
    """The Data Ingestion Center needs the same id for the staged
    filename and the DB row -- create_upload must use exactly the id
    it's given, not generate a different one."""
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    returned_id = create_upload(
        con, category_id="denture_base", dataset_type="full_export", version="v1",
        original_filename="export.xlsx", staging_path="/tmp/fake/export.xlsx", row_count=105,
        upload_id="my-fixed-id",
    )
    assert returned_id == "my-fixed-id"
    assert get_upload(con, "my-fixed-id") is not None
    con.close()


def test_record_validation_stores_report_and_advances_status(con):
    upload_id = _stage(con)
    report = {"status": "WARNING", "row_count": 105, "checks": [{"name": "duplicate_asins", "severity": "WARNING", "message": "1 dup", "details": []}]}
    record_validation(con, upload_id, "WARNING", report, quality_score=93.8)

    record = get_upload(con, upload_id)
    assert record["status"] == STATUS_VALIDATED
    assert record["validation_status"] == "WARNING"
    assert record["validation_report"]["checks"][0]["name"] == "duplicate_asins"
    assert record["validated_at"] is not None
    assert record["quality_score"] == 93.8


def test_approve_upload_records_raw_path_and_status(con):
    upload_id = _stage(con)
    approve_upload(con, upload_id, "/data/raw/denture_base/denture_base_sellersprite_v1.xlsx")
    record = get_upload(con, upload_id)
    assert record["status"] == STATUS_APPROVED
    assert record["raw_path"] == "/data/raw/denture_base/denture_base_sellersprite_v1.xlsx"
    assert record["approved_at"] is not None


def test_reject_upload_sets_rejected_status(con):
    upload_id = _stage(con)
    reject_upload(con, upload_id)
    assert get_upload(con, upload_id)["status"] == STATUS_REJECTED


def test_mark_ingested_records_pipeline_run_id(con):
    upload_id = _stage(con)
    mark_ingested(con, upload_id, "some-run-id")
    record = get_upload(con, upload_id)
    assert record["status"] == STATUS_INGESTED
    assert record["pipeline_run_id"] == "some-run-id"
    assert record["ingested_at"] is not None


def test_get_upload_returns_none_for_unknown_id(con):
    assert get_upload(con, "does-not-exist") is None


def test_list_uploads_filters_by_category_and_is_newest_first(con):
    old = _stage(con, category_id="denture_base")
    new = _stage(con, category_id="denture_base")
    _stage(con, category_id="micromotor")

    denture_uploads = list_uploads(con, category_id="denture_base")
    assert [u["upload_id"] for u in denture_uploads] == [new, old]

    all_uploads = list_uploads(con)
    assert len(all_uploads) == 3


# --- Version registry ---

def test_register_dataset_version_creates_a_permanent_row(con):
    upload_id = _stage(con)
    version_id = register_dataset_version(con, "denture_base", "sellersprite", "v1", upload_id, 105)
    versions = list_dataset_versions(con, "denture_base")
    assert len(versions) == 1
    assert versions[0]["version_id"] == version_id
    assert versions[0]["source"] == "sellersprite"
    assert versions[0]["record_count"] == 105


def test_list_dataset_versions_is_newest_first_and_filters_by_category(con):
    u1 = _stage(con, category_id="denture_base", version="v1")
    u2 = _stage(con, category_id="denture_base", version="v2")
    u3 = _stage(con, category_id="micromotor", version="v1")
    v1 = register_dataset_version(con, "denture_base", "sellersprite", "v1", u1, 100)
    v2 = register_dataset_version(con, "denture_base", "sellersprite", "v2", u2, 110)
    register_dataset_version(con, "micromotor", "sellersprite", "v1", u3, 50)

    denture_versions = list_dataset_versions(con, "denture_base")
    assert [v["version_id"] for v in denture_versions] == [v2, v1]


def test_next_version_label_counts_existing_versions(con):
    assert next_version_label(con, "denture_base", "full_export") == "v1"
    upload_id = _stage(con)
    register_dataset_version(con, "denture_base", "sellersprite", "v1", upload_id, 100)
    assert next_version_label(con, "denture_base", "full_export") == "v2"
