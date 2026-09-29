"""The Polars ingestion path must produce exactly what the v1 (pandas)
adapters produce -- same record ids, values, rejection reasons -- and handle
generated large-file data correctly."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
REAL = sorted((ROOT / "data" / "raw").glob("*/*_sellersprite.xlsx"))
COLS = ["record_id", "id", "title", "brand", "price", "sales", "revenue", "rating", "category", "image", "url",
        "timestamp", "launch_date"]


@pytest.fixture()
def lake_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DIP_DATA_DIR", str(tmp_path / "platform"))
    monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "lake"))
    from dip import settings
    settings.get_settings.cache_clear()
    yield tmp_path
    settings.get_settings.cache_clear()


def _both(src, monkeypatch):
    from dip.pipeline import ingestion
    monkeypatch.setenv("DIP_INGEST_ENGINE", "pandas")
    a = ingestion.ingest_dataset(src, "m", "a")
    monkeypatch.setenv("DIP_INGEST_ENGINE", "polars")
    b = ingestion.ingest_dataset(src, "m", "b")
    return a, b


def _assert_same(a, b):
    fa, fb = a.frame.reset_index(drop=True), b.frame.reset_index(drop=True)
    assert len(fa) == len(fb)
    for c in COLS:
        x, y = fa[c], fb[c]
        both_missing = x.isna().to_numpy() & y.isna().to_numpy()
        assert ((x.to_numpy() == y.to_numpy()) | both_missing).all(), f"column {c} differs"
    assert fa["rejection_reasons"].map(sorted).tolist() == fb["rejection_reasons"].map(sorted).tolist()
    assert a.report["rejection_reasons"] == b.report["rejection_reasons"]
    assert b.report["engine"] == "polars"


@pytest.mark.parametrize("path", REAL, ids=[p.parent.name for p in REAL])
def test_polars_matches_v1_on_real_exports(path, lake_env, monkeypatch):
    _assert_same(*_both(path, monkeypatch))


def test_polars_matches_v1_on_generated_csv(lake_env, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts" / "bench"))
    from make_large_dataset import build

    src = lake_env / "gen.csv"
    build(20_000, 6).to_csv(src, index=False)
    a, b = _both(src, monkeypatch)
    _assert_same(a, b)
    reasons = b.report["rejection_reasons"]
    assert {"missing price", "invalid ASIN", "impossible sales", "duplicate"} <= set(reasons)
    assert b.report["accepted"] + b.report["rejected"] == b.report["raw_rows"]
    assert b.frame["timestamp"].notna().all()  # 快照日期 detected as the snapshot timestamp
    raw = list((lake_env / "lake" / "raw").rglob("data.parquet"))
    assert raw and pd.read_parquet(raw[-1]).shape[0] == len(pd.read_csv(src, dtype=str))
