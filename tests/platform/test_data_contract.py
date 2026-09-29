"""Data contract at ingestion (engine phase E1): one 'as of' date per upload with its basis, and date columns that
cannot be read are reported instead of silently blanked."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "engine"))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DIP_DATA_DIR", str(tmp_path / "platform"))
    monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "lake"))
    monkeypatch.setenv("DIP_AUTH", "off")
    monkeypatch.setenv("DIP_FORECAST_WORKERS", "1")
    for var in ("DIP_POSTGRES_URL", "DIP_NEO4J_URI", "DIP_QDRANT_URL", "DIP_JOB_MODE"):
        monkeypatch.delenv(var, raising=False)
    from dip import settings
    from dip.storage import graph, vectors
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()
    yield tmp_path
    settings.get_settings.cache_clear()
    graph.get_graph_store.cache_clear()
    vectors.get_vector_store.cache_clear()


def _export(tmp: Path, name: str) -> Path:
    from test_engine_smoke import synthetic_dataset

    df = synthetic_dataset(months=1).drop(columns=["Snapshot"])        # no date column: the upload itself is the snapshot
    p = tmp / name
    df.to_csv(p, index=False)
    return p


def _market(name: str) -> dict:
    from dip.storage import business as b

    with b.session() as s:
        return dict(s.get(b.Market, name).summary)


def test_undated_upload_is_as_of_the_import_date_everywhere(env):
    from dip.pipeline import runner
    from dip.storage import lake

    runner.process_dataset(_export(env, "export.csv"), "micromotor", source_name="export.csv")
    s = _market("micromotor")
    today = str(date.today())
    assert s["snapshot"] == {"date": today, "basis": "import date (no snapshot date declared)"}
    v3 = s["metrics_v3"]
    assert v3["as_of"] == today and v3["as_of_basis"] == "import date (no snapshot date declared)"
    obs = lake.read_curated("observation_history", "micromotor")
    assert set(obs["observed_at"].astype(str)) == {today}                 # the same day on every stored observation
    rh = lake.read_curated("revenue_history", "micromotor")
    assert {str(pd.Timestamp(p).date()) for p in rh["period"]} == {today}
    from dip.metrics.integrity import check_market
    snap = next(c for c in check_market("micromotor")["checks"] if c["id"] == "snapshot_date_declared")
    assert snap["status"] == "warn"                                         # an import date is a fallback: flagged


def test_date_in_the_file_name_is_the_snapshot_date(env):
    from dip.pipeline import runner

    runner.process_dataset(_export(env, "micromotor_2025-03.csv"), "micromotor", source_name="micromotor_2025-03.csv")
    s = _market("micromotor")
    assert s["snapshot"] == {"date": "2025-03-01", "basis": "snapshot date"}
    assert s["metrics_v3"]["as_of"] == "2025-03-01"


def test_unreadable_dates_are_reported_not_silently_blanked():
    from dmie.engine.ingestion.adapters import to_market_frame

    raw = pd.DataFrame({"asin": ["B000000001", "B000000002", "B000000003"], "title": ["a", "b", "c"],
                        "launch": ["2024-05-01", "0.49", None], "age": [0.49, 0.71, 0.88]})
    out, notes = to_market_frame(raw, {"id": "asin", "title": "title", "launch_date": "launch"}, "t")
    assert out["launch_date"].notna().sum() == 1
    assert any("1 of 2 values are not dates" in n for n in notes)
    out2, notes2 = to_market_frame(raw, {"id": "asin", "title": "title", "launch_date": "age"}, "t")
    assert out2["launch_date"].isna().all()
    assert any("none of its 3 values is a date" in n and "0.49" in n for n in notes2)
