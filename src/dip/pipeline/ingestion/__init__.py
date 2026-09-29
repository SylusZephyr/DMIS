"""Stage 1 -- Universal ingestion with an ingestion report.

Wraps ``dmie.engine.ingestion`` (schema detection, SellerSprite adapter,
csv/xlsx/json/API) and adds: content hashing + duplicate-upload detection,
immutable raw-zone storage, and an accepted/rejected report with reasons.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from dmie.engine.ingestion import ingest as core_ingest
from dmie.engine.ingestion import read_table
from dip.storage import lake

ASIN_RE = re.compile(r"^(B0[0-9A-Z]{8}|[0-9]{9}[0-9X])$")


@dataclass
class IngestionOutcome:
    dataset_id: str
    frame: pd.DataFrame            # universal MarketRecord frame, every row (accepted + rejected)
    raw: pd.DataFrame
    adapter: str
    detection: dict
    content_hash: str
    report: dict = field(default_factory=dict)
    owner_hints: dict | None = None   # set by the Polars path (computed from raw columns)


def _looks_like_amazon(frame: pd.DataFrame, adapter: str) -> bool:
    ids = frame["id"].dropna().astype(str)
    return adapter == "sellersprite" or (len(ids) > 0 and ids.str.upper().str.match(r"^B0").mean() > 0.5)


def rejection_reasons(frame: pd.DataFrame, adapter: str) -> pd.Series:
    """Hard rejections (the record cannot enter the market). Soft quality issues
    are scored later by the cleaning stage, not rejected here."""
    reasons = pd.Series([[] for _ in range(len(frame))], index=frame.index, dtype=object)

    def add(mask: pd.Series, reason: str) -> None:
        for i in frame.index[mask.fillna(False)]:
            reasons.at[i] = reasons.at[i] + [reason]

    add(frame["title"].isna() | (frame["title"].astype(str).str.len() < 3), "missing title")
    add(frame["price"].isna(), "missing price")
    add(frame["price"].notna() & (frame["price"] <= 0), "invalid price")
    add(frame["sales"].notna() & (frame["sales"] < 0), "impossible sales")
    if _looks_like_amazon(frame, adapter):
        ids = frame["id"].astype(str).str.upper().str.strip()
        add(~ids.str.match(ASIN_RE), "invalid ASIN")
    ts = frame["timestamp"].astype(str)
    dup = frame.assign(_ts=ts).duplicated(subset=["id", "_ts"], keep="first") & frame["id"].notna()
    add(dup, "duplicate")
    return reasons


def build_report(frame: pd.DataFrame, reasons: pd.Series, detection: dict, adapter: str, notes: list[str]) -> dict:
    rejected = reasons.map(len) > 0
    counts: dict[str, int] = {}
    for rs in reasons[rejected]:
        for r in rs:
            counts[r] = counts.get(r, 0) + 1
    return {
        "raw_rows": int(len(frame)),
        "accepted": int((~rejected).sum()),
        "rejected": int(rejected.sum()),
        "rejection_reasons": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "adapter": adapter,
        "column_mapping": detection.get("mapping", {}),
        "mapping_confidence": detection.get("confidence", {}),
        "unmapped_columns": detection.get("unmapped_columns", []),
        "warnings": detection.get("warnings", []) + notes,
    }


def use_fast_path(source) -> bool:
    if os.environ.get("DIP_INGEST_ENGINE", "polars").lower() == "pandas":
        return False
    from dip.pipeline.ingestion.fast import FAST_EXTENSIONS
    return (isinstance(source, (str, Path)) and not str(source).startswith(("http://", "https://"))
            and Path(source).suffix.lower() in FAST_EXTENSIONS)


def max_rows() -> int:
    """Sanity limit on one dataset (env DIP_MAX_UPLOAD_ROWS, default 5,000,000)."""
    try:
        return int(float(os.environ.get("DIP_MAX_UPLOAD_ROWS", "") or 5_000_000))
    except ValueError:
        return 5_000_000


def _check_rows(n: int) -> None:
    if n > max_rows():
        raise ValueError(f"dataset has {n:,} rows; the limit is {max_rows():,} (DIP_MAX_UPLOAD_ROWS)")


def ingest_dataset(source, market: str, dataset_id: str, overrides: dict | None = None) -> IngestionOutcome:
    """Files go through the Polars path (scales to millions of rows); DataFrames and
    API payloads through the v1 pandas adapters. Both produce the same frame and report."""
    if use_fast_path(source):
        from dip.pipeline.ingestion import fast

        path = Path(source)
        # raw zone is written before we know the adapter; the kind is fixed up below
        tmp_sink = lake.raw_path("incoming", dataset_id)
        pl_frame, report, adapter, hints = fast.ingest_file(path, path.name, raw_sink=tmp_sink, overrides=overrides)
        lake.move_raw(tmp_sink, adapter, dataset_id)
        _check_rows(pl_frame.height)
        frame = fast.to_pandas_frame(pl_frame)
        return IngestionOutcome(dataset_id, frame, pd.DataFrame(), adapter,
                                {"mapping": report["column_mapping"], "confidence": report["mapping_confidence"],
                                 "unmapped_columns": report["unmapped_columns"], "warnings": report["warnings"]},
                                lake.content_hash(path), report, hints)
    if isinstance(source, (str, Path)) and not str(source).startswith(("http://", "https://")):
        raw = read_table(source)
        digest = lake.content_hash(source)
        result = core_ingest(raw.copy(), name=Path(source).name, overrides=overrides)
    else:
        result = core_ingest(source, name=market, overrides=overrides)
        raw = result.frame.copy() if not isinstance(source, pd.DataFrame) else source
        digest = lake.content_hash(pd.util.hash_pandas_object(raw.astype(str), index=False).values.tobytes())
    frame = result.frame
    _check_rows(len(frame))
    reasons = rejection_reasons(frame, result.adapter)
    frame = frame.assign(rejection_reasons=reasons, accepted=reasons.map(len) == 0)
    report = build_report(frame, reasons, result.detection.to_dict(), result.adapter, result.notes)
    lake.write_raw(raw, result.adapter, dataset_id)
    return IngestionOutcome(dataset_id, frame, raw, result.adapter, result.detection.to_dict(), digest, report)
