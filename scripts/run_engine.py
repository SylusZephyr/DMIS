"""Run the Universal Market Intelligence Engine on any dataset.

Usage:
    python scripts/run_engine.py data/raw/dental_models/dental_models_sellersprite.xlsx --market dental_models
    python scripts/run_engine.py my_export.csv --market micromotor --reviews reviews.csv
    python scripts/run_engine.py https://example.com/api/products.json --market wax
    python scripts/run_engine.py --all-raw            # every data/raw/<market>/*.xlsx, latest file per folder
    python scripts/run_engine.py --import-suppliers suppliers.csv

Fully offline. See docs/universal_engine_architecture.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import pandas as pd  # noqa: E402

from dmie.engine.assistant import explain_market  # noqa: E402
from dmie.engine.ingestion import read_table  # noqa: E402
from dmie.engine.pipeline import run_engine, save_suppliers  # noqa: E402


def _print_result(r) -> None:
    s = r.summary
    print(f"\n=== {r.market_name} (run {r.run_id}) ===")
    print(f"adapter: {s['ingestion']['adapter']}  mapping: {json.dumps(s['ingestion']['detection']['mapping'], ensure_ascii=False)}")
    print(f"quality: {s['quality']['dataset_confidence']}/100, usable {s['quality']['usable_for_market']}/{s['quality']['records']}")
    print(f"relevance: {s['relevance']}")
    print(f"discovery: {s['discovery']['families']} families, {s['discovery']['segments']} segments")
    print(f"dedup: {s['dedup']['listings']} listings -> {s['dedup']['products']} products")
    print()
    print(explain_market(s, r.segments))
    cols = ["segment_label", "products", "listings", "monthly_revenue", "opportunity_score", "coverage"]
    print("\nTop segments by opportunity:")
    print(r.segments[cols].head(8).to_string(index=False))


def _latest_raw_files() -> list[tuple[str, Path]]:
    out = []
    for d in sorted((PROJECT_ROOT / "data" / "raw").iterdir()):
        if d.is_dir():
            files = sorted(d.glob("*.xlsx"), key=lambda p: p.stat().st_mtime)
            canonical = d / f"{d.name}_sellersprite.xlsx"  # the folder's primary export, if named conventionally
            if canonical.exists():
                out.append((d.name, canonical))
            elif files:
                out.append((d.name, files[-1]))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="file path (.xlsx/.csv/.json) or http(s) URL")
    ap.add_argument("--market", help="market name (defaults to the file stem)")
    ap.add_argument("--domain", default=None, help="relevance domain in config/engine/relevance_domains.yaml")
    ap.add_argument("--reviews", help="optional reviews file (columns: id/asin, text, rating)")
    ap.add_argument("--suppliers", help="optional suppliers file to include in this run")
    ap.add_argument("--snapshot-date", help="date this export represents (YYYY-MM-DD); builds the forecast history")
    ap.add_argument("--all-raw", action="store_true", help="run every data/raw/<market>/ folder")
    ap.add_argument("--import-suppliers", help="import a supplier file into the global supplier table and exit")
    ap.add_argument("--no-persist", action="store_true", help="do not write to DuckDB")
    args = ap.parse_args(argv)

    if args.import_suppliers:
        n = save_suppliers(read_table(args.import_suppliers))
        print(f"supplier table now holds {n} suppliers")
        return 0

    jobs: list[tuple[str, str]] = []
    if args.all_raw:
        jobs = [(name, str(p)) for name, p in _latest_raw_files()]
    elif args.source:
        jobs = [(args.market or Path(args.source).stem, args.source)]
    else:
        ap.error("give a source file/URL or --all-raw")

    reviews = read_table(args.reviews) if args.reviews else None
    suppliers = read_table(args.suppliers) if args.suppliers else None
    for market, src in jobs:
        r = run_engine(src, market, domain=args.domain, reviews=reviews, suppliers=suppliers,
                       snapshot_date=args.snapshot_date, persist=not args.no_persist)
        _print_result(r)
    return 0


if __name__ == "__main__":
    pd.set_option("display.width", 200)
    raise SystemExit(main())
