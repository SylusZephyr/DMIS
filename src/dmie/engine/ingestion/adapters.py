"""Source adapters (Module 1).

Every adapter turns some source into the universal MarketRecord frame
(dmie.engine.records.RECORD_COLUMNS). SellerSprite is just one adapter
with a known mapping; everything else goes through schema_detector.

Raw files are only ever read, never modified (PRINCIPLES.md principle 3).
"""

from __future__ import annotations

import json
import math
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from dmie.cleaning.normalize import normalize_whitespace, parse_numeric
from dmie.engine.ingestion.schema_detector import DetectionResult, detect_schema
from dmie.engine.records import NUMERIC_FIELDS, RECORD_COLUMNS, TEXT_FIELDS, make_record_id

SUPPORTED_EXTENSIONS = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".json", ".jsonl"}

# SellerSprite's known export layout (see dmie.ingestion.schema.COLUMN_MAP,
# which remains the Phase-1 source of truth for the listings table).
SELLERSPRITE_MAPPING = {
    "id": "ASIN",
    "title": "商品标题",
    "brand": "品牌",
    "url": "商品详情页链接",
    "image": "商品主图",
    "price": "价格($)",
    "rating": "评分",
    "sales": "子体销量",
    "revenue": "子体销售额($)",
    "category": "小类目",
    "launch_date": "上架时间",
}
SELLERSPRITE_SIGNATURE = {"ASIN", "商品标题", "子体销量"}


@dataclass
class IngestionResult:
    frame: pd.DataFrame
    adapter: str
    detection: DetectionResult
    source: str
    raw_rows: int
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "adapter": self.adapter,
            "source": self.source,
            "raw_rows": self.raw_rows,
            "records": len(self.frame),
            "detection": self.detection.to_dict(),
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- readers

def read_table(path: str | Path, sheet: str | int | None = None) -> pd.DataFrame:
    """Read csv / tsv / xlsx / json / jsonl into a raw DataFrame."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"unsupported file type '{ext}' (supported: {sorted(SUPPORTED_EXTENSIONS)})")
    if ext in (".xlsx", ".xlsm", ".xls"):
        if sheet is not None:
            return pd.read_excel(path, sheet_name=sheet)
        sheets = pd.read_excel(path, sheet_name=None)
        # the largest sheet is the data sheet; summary sheets are small
        return max(sheets.values(), key=len)
    if ext in (".json", ".jsonl"):
        text = path.read_text(encoding="utf-8-sig")
        return json_to_frame(text, lines=ext == ".jsonl")
    sep = "\t" if ext == ".tsv" else None
    for enc in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            return pd.read_csv(path, sep=sep, engine="python", encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"could not decode {path}")


def json_to_frame(payload: Any, lines: bool = False, record_path: str | None = None) -> pd.DataFrame:
    if isinstance(payload, (str, bytes)):
        if lines:
            payload = [json.loads(line) for line in str(payload).splitlines() if line.strip()]
        else:
            payload = json.loads(payload)
    if record_path:
        for key in record_path.split("."):
            payload = payload[key]
    if isinstance(payload, dict):
        # common API envelopes: {"data": [...]}, {"results": [...]}, {"items": [...]}
        for key in ("data", "results", "items", "products", "records"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            payload = [payload]
    return pd.json_normalize(payload)


# --------------------------------------------------------------------------- conversion

def _clean_value(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _parse_dates(series: pd.Series) -> pd.Series:
    """Parse dates without ever reading plain numbers as epoch offsets
    (SellerSprite's 上架时间 is sometimes a listing age in years, e.g. 0.49)."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    if pd.api.types.is_numeric_dtype(series):
        return pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    text = series.astype("string")
    looks_numeric = text.str.fullmatch(r"\s*-?\d+(\.\d+)?\s*").fillna(False)
    parsed = pd.to_datetime(text.where(~looks_numeric), errors="coerce", format="mixed")
    return parsed.astype("datetime64[ns]")


def to_market_frame(raw: pd.DataFrame, mapping: dict[str, str], source: str) -> tuple[pd.DataFrame, list[str]]:
    """Project a raw frame onto the universal schema using ``mapping``."""
    notes: list[str] = []
    out = pd.DataFrame(index=raw.index)
    for f in TEXT_FIELDS:
        col = mapping.get(f)
        out[f] = raw[col].map(normalize_whitespace) if col else None
    for f in NUMERIC_FIELDS:
        col = mapping.get(f)
        out[f] = raw[col].map(parse_numeric).astype("float64") if col else np.nan
    for f in ("timestamp", "launch_date"):
        col = mapping.get(f)
        out[f] = _parse_dates(raw[col]) if col else pd.NaT
        if not col:
            continue
        given = int(raw[col].notna().sum())
        unread = given - int(out[f].notna().sum())
        if given and out[f].isna().all():
            sample = ", ".join(map(str, raw[col].dropna().astype(str).head(3)))
            notes.append(f"'{col}' mapped to {f} but none of its {given} values is a date (e.g. {sample}) -- "
                         "kept as attribute only; nothing that depends on it is computed")
            mapping = {k: v for k, v in mapping.items() if k != f}
        elif unread:
            notes.append(f"'{col}': {unread} of {given} values are not dates and were left empty")

    derived = out["revenue"].isna() & out["price"].notna() & out["sales"].notna()
    if derived.any():
        out.loc[derived, "revenue"] = out.loc[derived, "price"] * out.loc[derived, "sales"]
        notes.append(f"revenue derived as price*sales for {int(derived.sum())} records")

    used = set(mapping.values())
    extra_cols = [c for c in raw.columns if c not in used]
    attrs = []
    for idx, row in raw[extra_cols].iterrows() if extra_cols else ((i, None) for i in raw.index):
        a = {} if row is None else {str(k): _clean_value(v) for k, v in row.items() if _clean_value(v) is not None}
        if derived.loc[idx]:
            a["_revenue_derived"] = True
        attrs.append(a)
    out["attributes"] = attrs
    out["source"] = source
    out["record_id"] = [
        make_record_id(source, rid, i, None if pd.isna(ts) else ts)
        for i, (rid, ts) in enumerate(zip(out["id"], out["timestamp"]))
    ]
    missing_id = out["id"].isna()
    out.loc[missing_id, "id"] = out.loc[missing_id, "record_id"]
    return out[RECORD_COLUMNS].reset_index(drop=True), notes


# --------------------------------------------------------------------------- adapters

class SellerSpriteAdapter:
    name = "sellersprite"

    @staticmethod
    def matches(raw: pd.DataFrame) -> bool:
        return SELLERSPRITE_SIGNATURE <= set(map(str, raw.columns))

    def convert(self, raw: pd.DataFrame, source: str) -> IngestionResult:
        mapping = {f: c for f, c in SELLERSPRITE_MAPPING.items() if c in raw.columns}
        detection = detect_schema(raw, overrides=mapping)
        # SellerSprite's known columns are pinned; the detector may still add
        # e.g. a timestamp or seller column that some exports include.
        frame, notes = to_market_frame(raw, detection.mapping, source)
        return IngestionResult(frame, self.name, detection, source, len(raw), notes)


class GenericTableAdapter:
    name = "auto_detect"

    def convert(self, raw: pd.DataFrame, source: str, overrides: dict | None = None) -> IngestionResult:
        detection = detect_schema(raw, overrides=overrides)
        frame, notes = to_market_frame(raw, detection.mapping, source)
        return IngestionResult(frame, self.name, detection, source, len(raw), notes)


class ApiAdapter:
    """JSON API source: pass a URL (GET, JSON body) or an already-decoded payload."""

    name = "api_json"

    def fetch(self, url: str, record_path: str | None = None, headers: dict | None = None, timeout: int = 30) -> pd.DataFrame:
        req = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user-supplied data source
            return json_to_frame(resp.read().decode("utf-8"), record_path=record_path)

    def convert(self, payload: Any, source: str, record_path: str | None = None, overrides: dict | None = None) -> IngestionResult:
        raw = payload if isinstance(payload, pd.DataFrame) else json_to_frame(payload, record_path=record_path)
        result = GenericTableAdapter().convert(raw, source, overrides)
        result.adapter = self.name
        return result


def ingest_frame(raw: pd.DataFrame, source: str, overrides: dict | None = None) -> IngestionResult:
    raw = raw.dropna(how="all")
    raw.columns = [str(c) for c in raw.columns]
    if not overrides and SellerSpriteAdapter.matches(raw):
        return SellerSpriteAdapter().convert(raw, source)
    return GenericTableAdapter().convert(raw, source, overrides)


def ingest(source: str | Path | pd.DataFrame | list | dict, name: str | None = None,
           overrides: dict | None = None, sheet: str | int | None = None) -> IngestionResult:
    """Universal entry point: a file path, an http(s) URL, a DataFrame, or a JSON payload."""
    if isinstance(source, pd.DataFrame):
        return ingest_frame(source.copy(), name or "dataframe", overrides)
    if isinstance(source, (list, dict)):
        return ApiAdapter().convert(source, name or "api_payload", overrides=overrides)
    text = str(source)
    if text.startswith(("http://", "https://")):
        adapter = ApiAdapter()
        result = adapter.convert(adapter.fetch(text), name or text, overrides=overrides)
        return result
    path = Path(text)
    return ingest_frame(read_table(path, sheet=sheet), name or path.name, overrides)
