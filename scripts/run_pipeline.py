"""One reproducible command for the complete DMIE pipeline (Milestone 15).

Usage: python scripts/run_pipeline.py --category denture_base
       python scripts/run_pipeline.py --category denture_base --skip-reviews --skip-dashboard

This module calls existing pipeline scripts' own `run()` functions in
order -- it does not reimplement, duplicate, or alter any calculation.
Its only job is: validate the category, run each stage in the required
order, log what happened, stop at the first failure rather than
continuing past it, and print a readable summary. Every run is recorded
in `pipeline_runs` via the same M14 provenance module the individual
classification/resolution scripts already use -- no new logging
infrastructure was built for this.

The requested 10-step pipeline order maps onto 6 actual callables,
because 2 pairs of steps were already combined in existing scripts
before this milestone existed (ingestion+normalization in
scripts/ingest.py; entity resolution+Product Master generation in
scripts/resolve_products.py) -- splitting them here would mean
duplicating code that already runs them together, which this milestone
explicitly must not do. `dashboard_preparation` is a no-op: the dashboard
reads DuckDB directly (dashboard/components/data.py) and
scripts/build_dashboard_data.py has never contained any code -- see
docs/final_architecture_review.md.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# scripts/ has no __init__.py (every other script here is invoked
# directly, e.g. `python scripts/classify.py`, never imported). Running
# `python scripts/run_pipeline.py` puts scripts/ itself on sys.path, not
# its parent -- `import scripts.<sibling>` would fail without this, since
# there's no `scripts` package visible from inside `scripts/`. Adding the
# project root makes `scripts` resolvable as an implicit namespace
# package regardless of how this file is invoked.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml

import scripts.analyze_reviews as analyze_reviews
import scripts.calculate_market as calculate_market
import scripts.classify as classify
import scripts.classify_product_types as classify_product_types
import scripts.detect_opportunities as detect_opportunities
import scripts.ingest as ingest
import scripts.resolve_products as resolve_products
from dmie.database.connection import PROJECT_ROOT, get_connection
from dmie.database.runs import finish_run, start_run
from dmie.database.snapshots import detect_changes, take_snapshot

PIPELINE_VERSION = "run_pipeline_v1"
_CATEGORIES_PATH = PROJECT_ROOT / "config" / "categories.yaml"


class CategoryNotConfigured(ValueError):
    """Raised when --category isn't a key in config/categories.yaml --
    caught at the top level and reported as a clean, readable error
    rather than a stack trace, and nothing is run at all."""


@dataclass
class StageResult:
    name: str
    covers: list[str]  # which of the 10 named steps this stage satisfies
    status: str = "pending"  # pending | success | failed | skipped
    duration_seconds: float = 0.0
    error: str | None = None


@dataclass
class PipelineReport:
    category: str
    started_at: datetime
    completed_at: datetime | None = None
    stages: list[StageResult] = field(default_factory=list)
    change_summary: dict | None = None  # Milestone 16: {change_type: count}, set once market_snapshot_and_change_detection runs

    @property
    def status(self) -> str:
        if any(s.status == "failed" for s in self.stages):
            return "failed"
        if any(s.status == "pending" for s in self.stages):
            return "incomplete"
        return "success"

    @property
    def duration_seconds(self) -> float:
        if self.completed_at is None:
            return 0.0
        return (self.completed_at - self.started_at).total_seconds()


def _known_categories() -> set[str]:
    data = yaml.safe_load(_CATEGORIES_PATH.read_text(encoding="utf-8"))
    return set(data["categories"].keys())


def _validate_category(category: str) -> None:
    known = _known_categories()
    if category not in known:
        raise CategoryNotConfigured(
            f"'{category}' is not a category defined in config/categories.yaml "
            f"(known categories: {sorted(known)}). Add a category entry before running the pipeline."
        )


def _setup_logging(category: str) -> tuple[logging.Logger, Path]:
    """Logs go to both stdout and a preserved per-run file -- a failed
    run's log must still be readable after the process exits, not just
    scrollback."""
    log_dir = PROJECT_ROOT / "data" / "exports" / category / "pipeline_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.log"

    logger = logging.getLogger(f"dmie.pipeline.{category}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()  # avoid duplicate handlers if run() is called twice in-process (tests)

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    return logger, log_path


def _snapshot_stage(category: str, run_id: str, state: dict) -> Callable[[], None]:
    def _run() -> None:
        con = get_connection()
        try:
            state["snapshot_before"] = take_snapshot(con, category, pipeline_run_id=run_id)
        finally:
            con.close()
    return _run


def _snapshot_and_detect_changes_stage(category: str, run_id: str, state: dict) -> Callable[[], None]:
    def _run() -> None:
        con = get_connection()
        try:
            state["snapshot_after"] = take_snapshot(con, category, pipeline_run_id=run_id)
            if state.get("snapshot_before"):
                state["changes"] = detect_changes(con, category, state["snapshot_before"], state["snapshot_after"])
        finally:
            con.close()
    return _run


def _stage_list(
    category: str, skip_reviews: bool, skip_dashboard: bool,
    run_id: str, snapshot_state: dict, raw_xlsx: Path | None = None,
) -> list[tuple[StageResult, Callable[[], None]]]:
    # raw_xlsx lets a caller (e.g. the Data Ingestion Center, Milestone 16)
    # point ingestion at a specific *already-approved*, version-stamped
    # file under data/raw/ instead of the fixed per-category filename --
    # every existing caller passes nothing and gets the unchanged default.
    raw_xlsx = raw_xlsx or PROJECT_ROOT / "data" / "raw" / category / f"{category}_sellersprite.xlsx"
    out_parquet = PROJECT_ROOT / "data" / "processed" / f"{category}_normalized.parquet"
    reviews_path = PROJECT_ROOT / "data" / "raw" / category / "reviews.json"

    stages: list[tuple[StageResult, Callable[[], None]]] = [
        (
            # Milestone 16: captures the previous run's ending state
            # *before* this run touches anything -- the "before" half of
            # the before/after diff that change detection needs.
            StageResult("market_snapshot", ["market_snapshot"]),
            _snapshot_stage(category, run_id, snapshot_state),
        ),
        (
            StageResult("ingestion_and_normalization", ["ingestion", "normalization"]),
            lambda: ingest.run(raw_xlsx, out_parquet, category_id=category),
        ),
        (
            StageResult("relevance_classification", ["relevance_classification"]),
            lambda: classify.run(category),
        ),
        (
            StageResult("product_type_classification", ["product_type_classification"]),
            lambda: classify_product_types.run(category),
        ),
        (
            StageResult("entity_resolution_and_product_master", ["entity_resolution", "product_master_generation"]),
            lambda: resolve_products.run(category),
        ),
        (
            StageResult("market_calculation", ["market_calculation"]),
            lambda: calculate_market.run(category),
        ),
        (
            # Milestone 16: the "after" snapshot, taken right after fresh
            # market metrics exist -- then immediately diffed against the
            # "before" snapshot into market_changes.
            StageResult("market_snapshot_and_change_detection", ["market_snapshot", "change_detection"]),
            _snapshot_and_detect_changes_stage(category, run_id, snapshot_state),
        ),
    ]

    if skip_reviews:
        stages.append((StageResult("review_analysis", ["review_analysis"], status="skipped"), lambda: None))
    else:
        stages.append((StageResult("review_analysis", ["review_analysis"]), lambda: analyze_reviews.run(reviews_path)))

    stages.append((
        StageResult("opportunity_detection", ["opportunity_detection"]),
        lambda: detect_opportunities.run(category),
    ))

    if skip_dashboard:
        stages.append((StageResult("dashboard_preparation", ["dashboard_preparation"], status="skipped"), lambda: None))
    else:
        # No-op by design: the dashboard reads DuckDB directly and
        # scripts/build_dashboard_data.py has never contained any logic
        # to call (see module docstring). Recorded as "success" (ran, did
        # nothing) rather than "skipped" (deliberately not run) -- the
        # distinction matters for an honest summary.
        stages.append((StageResult("dashboard_preparation", ["dashboard_preparation"]), lambda: None))

    return stages


def run(
    category: str, skip_reviews: bool = False, skip_dashboard: bool = False, raw_xlsx: Path | None = None
) -> PipelineReport:
    _validate_category(category)
    logger, log_path = _setup_logging(category)

    report = PipelineReport(category=category, started_at=datetime.now(timezone.utc))
    logger.info("DMIE pipeline run starting for category=%s (log: %s)", category, log_path)

    con = get_connection()
    try:
        run_id = start_run(con, category, "full_pipeline", PIPELINE_VERSION)
    finally:
        con.close()

    snapshot_state: dict = {}
    stage_plan = _stage_list(category, skip_reviews, skip_dashboard, run_id, snapshot_state, raw_xlsx=raw_xlsx)
    stopped_early = False

    for stage, fn in stage_plan:
        report.stages.append(stage)
        if stage.status == "skipped":
            logger.info("SKIPPED %s (--skip flag)", stage.name)
            continue
        if stopped_early:
            stage.status = "skipped"
            stage.error = "not run: an earlier stage failed"
            logger.warning("SKIPPED %s (%s)", stage.name, stage.error)
            continue

        logger.info("RUNNING %s (covers: %s)", stage.name, ", ".join(stage.covers))
        start = time.monotonic()
        try:
            fn()
            stage.status = "success"
            logger.info("SUCCESS %s", stage.name)
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: any stage failure must
            # stop the pipeline cleanly, never propagate an unhandled
            # traceback past this function's control (the readable
            # summary IS the error report).
            stage.status = "failed"
            stage.error = f"{type(exc).__name__}: {exc}"
            logger.error("FAILED %s: %s", stage.name, stage.error)
            logger.error(traceback.format_exc())
            stopped_early = True
        finally:
            stage.duration_seconds = time.monotonic() - start

    report.completed_at = datetime.now(timezone.utc)

    if "changes" in snapshot_state:
        counts: dict[str, int] = {}
        for c in snapshot_state["changes"]:
            counts[c["change_type"]] = counts.get(c["change_type"], 0) + 1
        report.change_summary = counts

    con = get_connection()
    try:
        summary = {
            "stages": [
                {"name": s.name, "status": s.status, "duration_seconds": round(s.duration_seconds, 3), "error": s.error}
                for s in report.stages
            ],
            "change_summary": report.change_summary,
        }
        failed = next((s for s in report.stages if s.status == "failed"), None)
        finish_run(con, run_id, summary=summary, error_message=failed.error if failed else None)
    finally:
        con.close()

    logger.info("DMIE pipeline run finished: status=%s duration=%.2fs", report.status, report.duration_seconds)
    print(_format_summary(report, run_id, log_path))
    return report


def _format_summary(report: PipelineReport, run_id: str, log_path: Path) -> str:
    lines = [
        "",
        "DMIE Pipeline Run Summary",
        "",
        f"Category:            {report.category}",
        f"Status:               {report.status}",
        f"Run ID:               {run_id}",
        f"Execution time:       {report.duration_seconds:.2f}s",
        f"Log file:             {log_path}",
        "",
        "Stages completed:",
    ]
    for s in report.stages:
        marker = {"success": "[OK]", "failed": "[FAILED]", "skipped": "[SKIPPED]", "pending": "[PENDING]"}[s.status]
        detail = f" -- {s.error}" if s.error else ""
        lines.append(f"  {marker} {s.name} ({s.duration_seconds:.2f}s){detail}")
    lines.append("")
    lines.append("Generated outputs: listing_classification, listing_product_type_classification,")
    lines.append("  match_candidates, product_listings, products, product_market_metrics,")
    lines.append("  category_market_metrics, review_insights, opportunity_signals (per stage actually run)")
    lines.append("")
    if report.change_summary:
        lines.append("Market changes detected since the previous run:")
        for change_type, count in sorted(report.change_summary.items()):
            lines.append(f"  {change_type}: {count}")
    else:
        lines.append("Market changes detected since the previous run: none")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the complete DMIE pipeline for one category.")
    parser.add_argument("--category", required=True, help="Category id from config/categories.yaml, e.g. denture_base")
    parser.add_argument("--skip-reviews", action="store_true", help="Skip the review-analysis stage")
    parser.add_argument("--skip-dashboard", action="store_true", help="Skip the (currently no-op) dashboard-preparation stage")
    parser.add_argument("--raw-file", default=None,
                         help="Override the raw XLSX path (default: data/raw/<category>/<category>_sellersprite.xlsx). "
                              "Used by the Data Ingestion Center to point at a specific approved, versioned upload.")
    args = parser.parse_args()

    raw_xlsx = Path(args.raw_file) if args.raw_file else None
    try:
        report = run(args.category, skip_reviews=args.skip_reviews, skip_dashboard=args.skip_dashboard, raw_xlsx=raw_xlsx)
    except CategoryNotConfigured as exc:
        print(f"ERROR: {exc}")
        return 2

    return 0 if report.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
