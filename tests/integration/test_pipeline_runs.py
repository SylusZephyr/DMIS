"""M14 -- run versioning and data provenance. pipeline_runs/
classification_runs/product_resolution_runs are append-only: unlike the
DELETE-and-regenerate DATA tables (match_candidates, products, ...), a
second run must never overwrite or remove the first run's row.
"""

import duckdb
import pytest

from dmie.database.connection import PROJECT_ROOT
from dmie.database.runs import (
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    compare_runs,
    finish_run,
    list_runs,
    record_classification_run,
    record_product_resolution_run,
    start_run,
)

SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"


@pytest.fixture
def con():
    connection = duckdb.connect(":memory:")
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield connection
    connection.close()


def test_start_run_records_running_status(con):
    run_id = start_run(con, "denture_base", "relevance_classification", "hybrid_v1")
    row = con.execute("SELECT status, category_id, pipeline_stage, pipeline_version FROM pipeline_runs WHERE run_id = ?", [run_id]).fetchone()
    assert row == (STATUS_RUNNING, "denture_base", "relevance_classification", "hybrid_v1")


def test_finish_run_success_records_summary(con):
    run_id = start_run(con, "denture_base", "relevance_classification", "hybrid_v1")
    finish_run(con, run_id, summary={"total": 105})
    row = con.execute("SELECT status, summary, completed_at FROM pipeline_runs WHERE run_id = ?", [run_id]).fetchone()
    assert row[0] == STATUS_SUCCESS
    assert '"total": 105' in row[1]
    assert row[2] is not None


def test_finish_run_with_error_records_failed_status(con):
    run_id = start_run(con, "denture_base", "relevance_classification", "hybrid_v1")
    finish_run(con, run_id, summary={}, error_message="boom")
    row = con.execute("SELECT status, error_message FROM pipeline_runs WHERE run_id = ?", [run_id]).fetchone()
    assert row == (STATUS_FAILED, "boom")


def test_two_runs_never_overwrite_each_other(con):
    """The exact requirement this milestone exists for: do not delete
    previous results automatically."""
    run_1 = start_run(con, "denture_base", "relevance_classification", "hybrid_v1")
    finish_run(con, run_1, summary={"total": 100})
    run_2 = start_run(con, "denture_base", "relevance_classification", "hybrid_v1")
    finish_run(con, run_2, summary={"total": 105})

    rows = con.execute("SELECT run_id FROM pipeline_runs ORDER BY started_at").fetchall()
    assert [r[0] for r in rows] == [run_1, run_2]  # both present, in order


def test_list_runs_filters_by_category_and_stage(con):
    start_run(con, "denture_base", "relevance_classification", "hybrid_v1")
    start_run(con, "denture_base", "product_resolution", "matching_v1")
    start_run(con, "micromotor", "relevance_classification", "hybrid_v1")

    denture_relevance = list_runs(con, category_id="denture_base", pipeline_stage="relevance_classification")
    assert len(denture_relevance) == 1
    assert denture_relevance[0]["category_id"] == "denture_base"

    all_denture = list_runs(con, category_id="denture_base")
    assert len(all_denture) == 2


def test_list_runs_is_newest_first(con):
    run_1 = start_run(con, "denture_base", "relevance_classification", "hybrid_v1")
    run_2 = start_run(con, "denture_base", "relevance_classification", "hybrid_v1")
    runs = list_runs(con, category_id="denture_base")
    assert [r["run_id"] for r in runs] == [run_2, run_1]


def test_record_classification_run_stores_comparable_counts(con):
    run_id = start_run(con, "denture_base", "relevance_classification", "hybrid_v1")
    record_classification_run(
        con, run_id, "denture_base", stage="relevance", classifier_version="hybrid_v1",
        total_classified=105, class_counts={"RELEVANT": 12, "IRRELEVANT": 65, "UNCERTAIN": 28},
        auto_accepted_count=77, needs_review_count=28,
    )
    row = con.execute("SELECT total_classified, auto_accepted_count, needs_review_count FROM classification_runs WHERE run_id = ?", [run_id]).fetchone()
    assert row == (105, 77, 28)


def test_record_product_resolution_run_stores_comparable_counts(con):
    run_id = start_run(con, "denture_base", "product_resolution", "matching_v1")
    record_product_resolution_run(
        con, run_id, "denture_base", matching_version="matching_v1",
        total_listings=105, candidate_pairs=91, match_count=14,
        no_match_count=1, uncertain_count=76, resulting_products=92,
    )
    row = con.execute("SELECT total_listings, match_count, resulting_products FROM product_resolution_runs WHERE run_id = ?", [run_id]).fetchone()
    assert row == (105, 14, 92)


# --- Comparing two runs (the M14 brief's explicit "how to compare two
# pipeline runs" requirement) ---

def test_compare_runs_finds_no_differences_for_identical_reruns(con):
    run_1 = start_run(con, "denture_base", "relevance_classification", "hybrid_v1")
    record_classification_run(con, run_1, "denture_base", "relevance", "hybrid_v1", 105, {"RELEVANT": 12}, 77, 28)
    finish_run(con, run_1, summary={})

    run_2 = start_run(con, "denture_base", "relevance_classification", "hybrid_v1")
    record_classification_run(con, run_2, "denture_base", "relevance", "hybrid_v1", 105, {"RELEVANT": 12}, 77, 28)
    finish_run(con, run_2, summary={})

    comparison = compare_runs(con, run_1, run_2)
    assert comparison["differences"] == {}


def test_compare_runs_surfaces_real_differences(con):
    run_1 = start_run(con, "denture_base", "relevance_classification", "hybrid_v1")
    record_classification_run(con, run_1, "denture_base", "relevance", "hybrid_v1", 105, {"RELEVANT": 12}, 77, 28)
    finish_run(con, run_1, summary={})

    run_2 = start_run(con, "denture_base", "relevance_classification", "hybrid_v1")
    record_classification_run(con, run_2, "denture_base", "relevance", "hybrid_v1", 105, {"RELEVANT": 14}, 79, 26)
    finish_run(con, run_2, summary={})

    comparison = compare_runs(con, run_1, run_2)
    assert comparison["differences"]["auto_accepted_count"] == (77, 79)
    assert comparison["differences"]["needs_review_count"] == (28, 26)
    assert comparison["differences"]["class_counts"] == ('{"RELEVANT": 12}', '{"RELEVANT": 14}')
    # run_id/created_at trivially differ between any two runs -- not a
    # real business difference, must not be reported as one.
    assert "run_id" not in comparison["differences"]
    assert "created_at" not in comparison["differences"]


def test_compare_runs_raises_a_clear_error_for_an_unknown_run_id(con):
    run_1 = start_run(con, "denture_base", "relevance_classification", "hybrid_v1")
    with pytest.raises(ValueError, match="run not found"):
        compare_runs(con, run_1, "does-not-exist")
