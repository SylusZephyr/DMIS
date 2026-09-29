"""Listing history across uploads.

Each upload of a market is kept in the lake's std zone. Stitching them by
native listing id (ASIN) gives a per-listing time series -- the evidence
behind historical consistency, trends, price movement, new launches and
competitor changes. Within one file, a timestamp column (e.g. a monthly
snapshot date) provides periods too. Nothing is interpolated: a period
exists only where a dataset or timestamp says so.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dip.storage import business as b
from dip.storage import lake

# the same records the market metrics use: accepted, relevant and usable
MARKET_FLAGS = ("accepted", "is_relevant", "usable_for_market")
COLUMNS = ["id", "title", "brand", "price", "sales", "revenue", "rating", "reviews", "launch_date", "timestamp"]


def dataset_period(d: b.Dataset) -> pd.Timestamp:
    """The date a dataset describes: its declared snapshot date, else its upload time."""
    if d.snapshot_date:
        return pd.Timestamp(d.snapshot_date)
    ts = pd.Timestamp(d.created_at)
    return (ts.tz_convert(None) if ts.tzinfo else ts).normalize()


def prior_datasets(market: str, exclude: str | None = None, exclude_hash: str | None = None) -> list[b.Dataset]:
    """Earlier uploads of the same market, oldest first; exact re-uploads (of each other or of
    the current file) are skipped."""
    with b.session() as s:
        rows = (s.query(b.Dataset).filter(b.Dataset.market_name == market)
                .order_by(b.Dataset.created_at.asc()).all())
    seen, out = {exclude_hash} if exclude_hash else set(), []
    for d in rows:
        if d.id == exclude or d.content_hash in seen:
            continue
        seen.add(d.content_hash)
        out.append(d)
    return out


def listing_history(market: str, current: pd.DataFrame, current_period: pd.Timestamp,
                    current_dataset: str | None = None, current_hash: str | None = None) -> pd.DataFrame:
    """Relevant listing records of every upload of ``market`` with a ``period`` column.

    ``current`` is this run's record frame (all snapshots); earlier uploads are read
    from the lake. Rows whose own timestamp is set use it as the period."""
    cur_cols = [c for c in COLUMNS if c in current]
    keep = np.ones(len(current), dtype=bool)
    for flag in MARKET_FLAGS:
        if flag in current:
            keep &= current[flag].fillna(False).to_numpy(dtype=bool)
    cur = current.loc[keep, cur_cols].copy()
    cur["dataset_id"] = current_dataset
    cur["period"] = pd.to_datetime(cur["timestamp"]).fillna(current_period) if "timestamp" in cur else current_period
    frames = [cur]
    prior = [d for d in prior_datasets(market, exclude=current_dataset, exclude_hash=current_hash) if d.content_hash]
    if prior:
        old = lake.read_std([d.id for d in prior], COLUMNS, where=" AND ".join(f"coalesce({f}, false)" for f in MARKET_FLAGS))
        if len(old):
            per = {d.id: dataset_period(d) for d in prior}
            old["period"] = pd.to_datetime(old["timestamp"]).fillna(old["dataset_id"].map(per))
            frames.append(old)
    out = pd.concat([f for f in frames if len(f)], ignore_index=True) if any(len(f) for f in frames) else cur
    out["period"] = pd.to_datetime(out["period"]).dt.normalize()
    # one row per listing and period (a re-upload of the same period keeps the newest dataset)
    return out.drop_duplicates(["id", "period"], keep="first").reset_index(drop=True)


def periods(history: pd.DataFrame) -> list[pd.Timestamp]:
    return sorted(history["period"].dropna().unique()) if len(history) else []
