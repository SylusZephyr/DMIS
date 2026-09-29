"""Normalize a raw SellerSprite export and load it into DuckDB + Parquet.

Usage: python scripts/ingest.py [xlsx_path] [out_parquet_path] [category_id]
Defaults to the denture_base pilot export.
"""

import sys
from pathlib import Path

import yaml

from dmie.cleaning.normalize import normalize_dataframe
from dmie.cleaning.validation import find_violations
from dmie.database.connection import PROJECT_ROOT, get_connection
from dmie.database.repository import export_listings_parquet, insert_decision_log, upsert_category, upsert_listings
from dmie.ingestion.excel_loader import load_raw_excel

_CATEGORIES_CONFIG_PATH = PROJECT_ROOT / "config" / "categories.yaml"


def _sync_category_row(con, category_id: str) -> None:
    """Ensures `categories` has a row for this ingest's category_id,
    read from config/categories.yaml -- found live that this sync never
    existed, so a fully-onboarded category (ingested, classified,
    resolved, scored) could still be completely invisible in the
    dashboard/API's category picker with no error at all (see
    DECISIONS.md). A category_id ingest.py is invoked with that isn't
    in the config is a real error, not silently skipped -- every
    category this pipeline touches must be traceable to real config."""
    config = yaml.safe_load(_CATEGORIES_CONFIG_PATH.read_text(encoding="utf-8"))["categories"]
    if category_id not in config:
        raise ValueError(f"'{category_id}' is not defined in {_CATEGORIES_CONFIG_PATH} -- add it before ingesting.")
    entry = config[category_id]
    upsert_category(con, category_id, leaf_category=entry["leaf_category_zh"], marketplace=entry["marketplace"])


def run(xlsx_path: Path, out_parquet: Path, category_id: str | None = None) -> None:
    raw_source_file = str(xlsx_path.relative_to(PROJECT_ROOT))
    df = load_raw_excel(xlsx_path)
    result = normalize_dataframe(df, raw_source_file=raw_source_file, category_id=category_id)

    violations = find_violations(result.listings)
    if violations:
        raise ValueError("normalization validation failed:\n" + "\n".join(violations))

    con = get_connection()
    try:
        if category_id:
            _sync_category_row(con, category_id)
        conflicts = upsert_listings(con, result.listings)
        insert_decision_log(con, result.decisions + conflicts)
        out_parquet.parent.mkdir(parents=True, exist_ok=True)
        export_listings_parquet(con, str(out_parquet), raw_source_file=raw_source_file)
    finally:
        con.close()

    print(f"source: {xlsx_path}")
    print(f"normalized listings: {len(result.listings)}")
    print(f"decisions logged: {len(result.decisions)}")
    if conflicts:
        print(f"WARNING: {len(conflicts)} listing_id(s) already belonged to a different category "
              f"and were left untouched (not reassigned) -- see decision_log entity_type='listing', "
              f"decision_type='category_conflict_skipped'")
    print(f"parquet: {out_parquet}")


if __name__ == "__main__":
    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        PROJECT_ROOT / "data" / "raw" / "denture_base" / "denture_base_sellersprite.xlsx"
    )
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else (
        PROJECT_ROOT / "data" / "processed" / "denture_base_normalized.parquet"
    )
    category = sys.argv[3] if len(sys.argv) > 3 else "denture_base"
    run(xlsx, out, category_id=category)
