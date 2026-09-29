"""M15 -- pipeline orchestration. These tests exercise run_pipeline.py's
own responsibilities (ordering, parameter passing, logging, failure
handling, reporting) by mocking every underlying stage's `run()` function
-- the stages' own business logic already has its own tests elsewhere
(test_ingest_pipeline.py, test_classifier.py, test_resolution_pipeline.py,
...). Uses category="micromotor" (a real, valid config/categories.yaml
entry) so test-generated log files don't land in denture_base's real
data/exports/ folder -- no real ingestion ever runs here regardless,
every stage function is mocked.
"""

import logging
import shutil
from unittest.mock import patch

import duckdb
import pytest

import scripts.run_pipeline as run_pipeline
from dmie.database.connection import PROJECT_ROOT

SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"
_TEST_LOG_DIR = PROJECT_ROOT / "data" / "exports" / "micromotor" / "pipeline_logs"


@pytest.fixture(autouse=True)
def _cleanup_test_log_files():
    """Every test here runs the real _setup_logging() against
    data/exports/micromotor/ (gitignored, but not cleaned up on its own).
    Release the file handler(s) so Windows will allow deletion, then
    remove the directory this test file created."""
    yield
    logger = logging.getLogger("dmie.pipeline.micromotor")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    shutil.rmtree(_TEST_LOG_DIR, ignore_errors=True)


@pytest.fixture
def in_memory_db():
    """run_pipeline.run() opens its own connections internally (start_run/
    finish_run/market_snapshot/change_detection, each open-then-close
    around a single call, same pattern every real stage script uses) --
    patch get_connection so none of this ever touches the real project
    database.

    Each call returns a *cursor* on one shared in-memory catalog, not a
    brand-new `:memory:` database -- `duckdb.connect(":memory:")` creates
    an independent, empty database on every call, so a naive
    `side_effect` returning a fresh one each time silently isolates every
    stage from every other stage's writes (a row inserted through one
    "connection" is invisible to the next). That was latent even before
    Milestone 16: `finish_run`'s UPDATE just silently affected zero rows
    against its own private empty snapshot. Milestone 16's
    detect_changes() raises instead of no-op'ing on a missing row, which
    is what surfaced it. `.cursor()` shares the same underlying database
    and can be closed independently, matching how repeated
    get_connection() calls against the real file-based DB actually
    behave."""
    keeper = duckdb.connect(":memory:")
    keeper.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    connections = []

    def _make_connection(*args, **kwargs):
        con = keeper.cursor()
        connections.append(con)
        return con

    with patch("scripts.run_pipeline.get_connection", side_effect=_make_connection):
        yield connections
    keeper.close()


@pytest.fixture
def mocked_stages():
    """Patches every real stage callable with a no-op mock. Individual
    tests override side_effect/return_value on whichever mock they need."""
    with patch("scripts.run_pipeline.ingest.run") as m_ingest, \
         patch("scripts.run_pipeline.classify.run") as m_classify, \
         patch("scripts.run_pipeline.classify_product_types.run") as m_classify_types, \
         patch("scripts.run_pipeline.resolve_products.run") as m_resolve, \
         patch("scripts.run_pipeline.calculate_market.run") as m_market, \
         patch("scripts.run_pipeline.analyze_reviews.run") as m_reviews, \
         patch("scripts.run_pipeline.detect_opportunities.run") as m_opportunities:
        yield {
            "ingest": m_ingest, "classify": m_classify, "classify_product_types": m_classify_types,
            "resolve_products": m_resolve, "calculate_market": m_market,
            "analyze_reviews": m_reviews, "detect_opportunities": m_opportunities,
        }


# --- 1. Successful full pipeline execution ---

def test_successful_full_pipeline_execution(in_memory_db, mocked_stages):
    report = run_pipeline.run("micromotor")
    assert report.status == "success"
    assert [s.status for s in report.stages] == ["success"] * len(report.stages)
    for mock in mocked_stages.values():
        mock.assert_called_once()


# --- 2. Failure in one stage ---

def test_failure_in_one_stage_stops_dependent_stages(in_memory_db, mocked_stages):
    mocked_stages["resolve_products"].side_effect = RuntimeError("simulated entity-resolution failure")

    report = run_pipeline.run("micromotor")

    assert report.status == "failed"
    by_name = {s.name: s for s in report.stages}
    assert by_name["ingestion_and_normalization"].status == "success"
    assert by_name["relevance_classification"].status == "success"
    assert by_name["product_type_classification"].status == "success"
    assert by_name["entity_resolution_and_product_master"].status == "failed"
    assert "simulated entity-resolution failure" in by_name["entity_resolution_and_product_master"].error

    # Everything after the failure must be skipped, never silently run.
    assert by_name["market_calculation"].status == "skipped"
    assert by_name["review_analysis"].status == "skipped"
    assert by_name["opportunity_detection"].status == "skipped"
    mocked_stages["calculate_market"].assert_not_called()
    mocked_stages["analyze_reviews"].assert_not_called()
    mocked_stages["detect_opportunities"].assert_not_called()


# --- 3. Invalid category handling ---

def test_invalid_category_is_rejected_before_anything_runs(in_memory_db, mocked_stages):
    with pytest.raises(run_pipeline.CategoryNotConfigured, match="not a category defined"):
        run_pipeline.run("totally_fake_category_xyz")
    for mock in mocked_stages.values():
        mock.assert_not_called()


def test_main_reports_invalid_category_as_a_clean_error_not_a_crash(in_memory_db, mocked_stages, capsys):
    with patch("sys.argv", ["run_pipeline.py", "--category", "totally_fake_category_xyz"]):
        exit_code = run_pipeline.main()
    assert exit_code == 2
    assert "ERROR" in capsys.readouterr().out


# --- 4. Logging creation ---

def test_logging_creates_a_preserved_log_file(in_memory_db, mocked_stages):
    report = run_pipeline.run("micromotor")
    logger = __import__("logging").getLogger("dmie.pipeline.micromotor")
    log_paths = [h.baseFilename for h in logger.handlers if hasattr(h, "baseFilename")]
    assert len(log_paths) == 1
    from pathlib import Path
    log_path = Path(log_paths[0])
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "RUNNING ingestion_and_normalization" in content
    assert "SUCCESS ingestion_and_normalization" in content
    assert report.status == "success"


# --- 5. Parameter passing ---

def test_category_is_passed_through_to_every_stage(in_memory_db, mocked_stages):
    run_pipeline.run("micromotor")
    assert mocked_stages["classify"].call_args.args == ("micromotor",)
    assert mocked_stages["classify_product_types"].call_args.args == ("micromotor",)
    assert mocked_stages["resolve_products"].call_args.args == ("micromotor",)
    assert mocked_stages["calculate_market"].call_args.args == ("micromotor",)
    assert mocked_stages["detect_opportunities"].call_args.args == ("micromotor",)
    # ingest.run gets category_id as a keyword, plus category-derived paths
    ingest_kwargs = mocked_stages["ingest"].call_args.kwargs
    assert ingest_kwargs["category_id"] == "micromotor"
    xlsx_arg = mocked_stages["ingest"].call_args.args[0]
    assert "micromotor" in str(xlsx_arg)


def test_skip_reviews_and_skip_dashboard_flags_prevent_those_stages_running(in_memory_db, mocked_stages):
    report = run_pipeline.run("micromotor", skip_reviews=True, skip_dashboard=True)
    by_name = {s.name: s for s in report.stages}
    assert by_name["review_analysis"].status == "skipped"
    assert by_name["dashboard_preparation"].status == "skipped"
    mocked_stages["analyze_reviews"].assert_not_called()
    # Every other stage still ran normally.
    assert by_name["market_calculation"].status == "success"
    assert by_name["opportunity_detection"].status == "success"


def test_main_parses_cli_arguments_and_flags(in_memory_db, mocked_stages):
    with patch("sys.argv", ["run_pipeline.py", "--category", "micromotor", "--skip-reviews", "--skip-dashboard"]):
        exit_code = run_pipeline.main()
    assert exit_code == 0
    mocked_stages["analyze_reviews"].assert_not_called()


# --- 6. Milestone 16: snapshot + change-detection stages ---

def test_snapshot_stages_run_before_ingestion_and_after_market_calculation(in_memory_db, mocked_stages):
    report = run_pipeline.run("micromotor")
    names = [s.name for s in report.stages]
    assert names[0] == "market_snapshot"
    assert names.index("market_snapshot_and_change_detection") == names.index("market_calculation") + 1
    by_name = {s.name: s for s in report.stages}
    assert by_name["market_snapshot"].status == "success"
    assert by_name["market_snapshot_and_change_detection"].status == "success"


def test_change_summary_is_empty_when_nothing_changed(in_memory_db, mocked_stages):
    """No product_market_metrics rows exist in the fixture DB either
    before or after (every real stage is mocked to a no-op) -- both
    snapshots are identically empty, so detect_changes must find nothing,
    not fabricate a change out of missing data."""
    report = run_pipeline.run("micromotor")
    assert report.change_summary is None or report.change_summary == {}


def test_a_failure_before_the_after_snapshot_leaves_change_summary_unset(in_memory_db, mocked_stages):
    mocked_stages["resolve_products"].side_effect = RuntimeError("simulated failure")
    report = run_pipeline.run("micromotor")
    by_name = {s.name: s for s in report.stages}
    assert by_name["market_snapshot_and_change_detection"].status == "skipped"
    assert report.change_summary is None


def test_raw_xlsx_override_is_passed_through_to_ingest(in_memory_db, mocked_stages, tmp_path):
    custom_path = tmp_path / "some_other_export.xlsx"
    run_pipeline.run("micromotor", raw_xlsx=custom_path)
    xlsx_arg = mocked_stages["ingest"].call_args.args[0]
    assert xlsx_arg == custom_path


def test_default_raw_xlsx_is_unchanged_when_no_override_given(in_memory_db, mocked_stages):
    run_pipeline.run("micromotor")
    xlsx_arg = mocked_stages["ingest"].call_args.args[0]
    assert xlsx_arg == run_pipeline.PROJECT_ROOT / "data" / "raw" / "micromotor" / "micromotor_sellersprite.xlsx"
