"""Lake batches (src/dip/storage/lake.py ``batch``): a processing run's curated writes are all-or-nothing."""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture()
def lake(tmp_path, monkeypatch):
    monkeypatch.setenv("DIP_LAKE_DIR", str(tmp_path / "lake"))
    from dip import settings
    settings.get_settings.cache_clear()
    from dip.storage import lake as lk
    yield lk
    settings.get_settings.cache_clear()


def _v(lk, table):
    return int(lk.read_curated(table, "m")["v"].iat[0])


def test_a_failed_batch_keeps_every_previous_table(lake):
    lake.write_curated("a", "m", pd.DataFrame({"v": [1]}))
    lake.write_curated("b", "m", pd.DataFrame({"v": [1]}))
    with pytest.raises(RuntimeError):
        with lake.batch():
            lake.write_curated("a", "m", pd.DataFrame({"v": [2]}))
            assert _v(lake, "a") == 1                      # readers inside the batch still see the old version
            raise RuntimeError("stage failed before table b was written")
    assert _v(lake, "a") == 1 and _v(lake, "b") == 1       # nothing half-updated
    leftovers = [p for p in (lake._root() / "curated").rglob("*") if p.name.endswith(".tmp")]
    assert leftovers == []                                  # staged files cleaned up


def test_a_successful_batch_commits_all_tables_and_the_last_write_wins(lake):
    with lake.batch():
        lake.write_curated("a", "m", pd.DataFrame({"v": [2]}))
        lake.write_curated("a", "m", pd.DataFrame({"v": [3]}))
        lake.write_curated("b", "m", pd.DataFrame({"v": [2]}))
        with lake.batch():                                  # nested: joins the outer batch
            lake.write_curated("c", "m", pd.DataFrame({"v": [2]}))
        assert not lake.has_curated("c", "m")
    assert (_v(lake, "a"), _v(lake, "b"), _v(lake, "c")) == (3, 2, 2)
    assert [p for p in (lake._root() / "curated").rglob("*") if p.name.endswith(".tmp")] == []
