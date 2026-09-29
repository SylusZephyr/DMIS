"""Country resolution for the Global Command Center (real reference data only)."""

from __future__ import annotations

import csv
from functools import lru_cache

from dip.settings import PROJECT_ROOT


@lru_cache(maxsize=1)
def _table() -> dict[str, dict]:
    idx: dict[str, dict] = {}
    with open(PROJECT_ROOT / "config" / "platform" / "country_centroids.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rec = {"country": r["country"], "iso2": r["iso2"], "lat": float(r["lat"]), "lon": float(r["lon"])}
            for key in [r["country"], r["iso2"], *r["aliases"].split("|")]:
                idx[key.strip().lower()] = rec
    return idx


def resolve(name: str | None) -> dict | None:
    if not name:
        return None
    return _table().get(str(name).strip().lower())
