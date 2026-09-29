"""Polars ingestion path for large files (millions of rows).

Same contract and same results as the pandas path in ``ingestion/__init__``
(which wraps the v1 adapters), built from vectorised Polars expressions:

* every source column is read as text (CSV is scanned lazily; the raw zone
  is streamed straight to Parquet without materialising the file)
* column mapping reuses the v1 schema detector on a sample (or the pinned
  SellerSprite mapping), so both paths agree on what each column means
* whitespace / Excel-artifact cleanup, numeric and date parsing, revenue
  derivation, hard-rejection reasons and duplicate detection are Polars
  expressions -- no per-row Python loops
* ``record_id`` uses the exact v1 formula (sha1 of source|id|timestamp), so
  ids are identical whichever path ingested a file
* unmapped columns are kept as one JSON ``attributes`` string per row
  instead of a Python dict per row (the dicts dominated memory at scale)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import polars as pl

from dmie.engine.config import section
from dmie.engine.ingestion.adapters import SELLERSPRITE_MAPPING, SELLERSPRITE_SIGNATURE
from dmie.engine.ingestion.schema_detector import DetectionResult, detect_schema
from dmie.engine.records import NUMERIC_FIELDS, RECORD_COLUMNS, TEXT_FIELDS

FAST_EXTENSIONS = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".json", ".jsonl"}
SAMPLE_ROWS = 5000
ASIN_PATTERN = r"^(B0[0-9A-Z]{8}|[0-9]{9}[0-9X])$"
DATE_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%m/%d/%Y", "%d.%m.%Y", "%Y-%m"]


def scan(path: str | Path) -> pl.LazyFrame:
    """Every column as text. CSV/TSV/JSONL are lazy scans; Excel/JSON load once."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".csv", ".tsv", ".txt"):
        return pl.scan_csv(path, separator="\t" if ext == ".tsv" else ",", infer_schema=False,
                           encoding="utf8-lossy", truncate_ragged_lines=True)
    if ext in (".xlsx", ".xlsm", ".xls"):
        sheets = pl.read_excel(path, sheet_id=0, engine="calamine", infer_schema_length=0)
        df = max(sheets.values(), key=len) if isinstance(sheets, dict) else sheets
        return df.lazy()
    if ext == ".jsonl":
        return pl.scan_ndjson(path, infer_schema_length=10000).select(pl.all().cast(pl.Utf8))
    if ext == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            payload = next((payload[k] for k in ("data", "results", "items", "products", "records")
                            if isinstance(payload.get(k), list)), [payload])
        return pl.DataFrame(pd.json_normalize(payload).astype("string")).lazy()
    raise ValueError(f"unsupported file type '{ext}'")


def _is_sellersprite(columns: list[str]) -> bool:
    return SELLERSPRITE_SIGNATURE <= set(columns)


def detect(lf: pl.LazyFrame, overrides: dict | None = None) -> tuple[DetectionResult, str]:
    sample = lf.head(SAMPLE_ROWS).collect().to_pandas()
    sample = sample.dropna(how="all")
    if not overrides and _is_sellersprite(list(sample.columns)):
        pinned = {f: c for f, c in SELLERSPRITE_MAPPING.items() if c in sample.columns}
        return detect_schema(sample, overrides=pinned), "sellersprite"
    return detect_schema(sample, overrides=overrides), "auto_detect"


def _clean_text(c: str) -> pl.Expr:
    e = (pl.col(c).cast(pl.Utf8)
         .str.replace_all(r"_x000[0-9A-Fa-f]_", " ")
         .str.replace_all(r"\s+", " ")
         .str.strip_chars())
    return pl.when(e.str.len_chars() > 0).then(e).otherwise(None)


def _parse_number(c: str) -> pl.Expr:
    s = pl.col(c).cast(pl.Utf8).str.strip_chars().str.replace_all(r"[,$]", "")
    return s.cast(pl.Float64, strict=False)


def _parse_date(c: str) -> pl.Expr:
    s = pl.col(c).cast(pl.Utf8).str.strip_chars()
    numeric_looking = s.str.contains(r"^-?\d+(\.\d+)?$")
    parsed = pl.coalesce([s.str.strptime(pl.Datetime("us"), f, strict=False) for f in DATE_FORMATS])
    return pl.when(numeric_looking).then(None).otherwise(parsed)


def _sha20(keys: pl.Series) -> pl.Series:
    return pl.Series([hashlib.sha1(k.encode("utf-8")).hexdigest()[:20] for k in keys.to_list()], dtype=pl.Utf8)


def project(lf: pl.LazyFrame, mapping: dict[str, str], source: str) -> tuple[pl.LazyFrame, list[str]]:
    """Universal MarketRecord columns + rejection reasons, as one lazy plan."""
    notes: list[str] = []
    cols = lf.collect_schema().names()
    exprs = []
    for f in TEXT_FIELDS:
        exprs.append(_clean_text(mapping[f]).alias(f) if f in mapping else pl.lit(None, pl.Utf8).alias(f))
    for f in NUMERIC_FIELDS:
        exprs.append(_parse_number(mapping[f]).alias(f) if f in mapping else pl.lit(None, pl.Float64).alias(f))
    for f in ("timestamp", "launch_date"):
        exprs.append(_parse_date(mapping[f]).alias(f) if f in mapping else pl.lit(None, pl.Datetime("us")).alias(f))
    extra = [c for c in cols if c not in set(mapping.values())]
    attrs = pl.struct([pl.col(c) for c in extra]).struct.json_encode() if extra else pl.lit("{}")
    # like the v1 adapters: fully empty rows are dropped before rows are numbered
    lf = lf.filter(~pl.all_horizontal([pl.col(c).is_null() | (pl.col(c).cast(pl.Utf8).str.strip_chars() == "") for c in cols]))
    out = lf.with_row_index("_row").select([pl.col("_row"), *exprs, attrs.alias("attributes")])

    derived = pl.col("revenue").is_null() & pl.col("price").is_not_null() & pl.col("sales").is_not_null()
    out = out.with_columns(
        pl.when(derived).then(pl.col("price") * pl.col("sales")).otherwise(pl.col("revenue")).alias("revenue"),
        derived.alias("_revenue_derived"),
        pl.lit(source).alias("source"),
    )
    return out, notes


def add_record_ids(frame: pl.DataFrame, source: str) -> pl.DataFrame:
    """record_id identical to dmie.engine.records.make_record_id; hashed once, eagerly."""
    ts_txt = pl.col("timestamp").dt.strftime("%Y-%m-%d %H:%M:%S").fill_null("")
    native = (pl.when(pl.col("id").is_null() | (pl.col("id") == ""))
              .then(pl.lit("row") + pl.col("_row").cast(pl.Utf8)).otherwise(pl.col("id")))
    keys = frame.select(pl.concat_str([pl.lit(source), native, ts_txt], separator="|")).to_series()
    return frame.with_columns(_sha20(keys).alias("record_id")).with_columns(
        pl.coalesce([pl.col("id"), pl.col("record_id")]).alias("id"))


def rejection_exprs(amazon_like: bool) -> pl.Expr:
    title = pl.col("title")
    checks = [
        (title.is_null() | (title.str.len_chars() < 3), "missing title"),
        (pl.col("price").is_null(), "missing price"),
        (pl.col("price").is_not_null() & (pl.col("price") <= 0), "invalid price"),
        (pl.col("sales").is_not_null() & (pl.col("sales") < 0), "impossible sales"),
    ]
    if amazon_like:
        checks.append((~pl.col("id").str.to_uppercase().str.strip_chars().str.contains(ASIN_PATTERN), "invalid ASIN"))
    ts = pl.col("timestamp").dt.strftime("%Y-%m-%d %H:%M:%S").fill_null("")
    checks.append((pl.col("id").is_not_null() & ~pl.struct(pl.col("id"), ts).is_first_distinct(), "duplicate"))
    return pl.concat_list([pl.when(c.fill_null(False)).then(pl.lit(r)).otherwise(pl.lit(None, pl.Utf8)) for c, r in checks]).list.drop_nulls()


def owner_hints(lf: pl.LazyFrame, top: int = 20) -> dict:
    """Owner / category-label counts straight from the raw columns (ownership config)."""
    cfg = section("ownership")
    names = set(lf.collect_schema().names())
    out = {"owners": {}, "category_labels": {}}
    for key, fields in (("owners", cfg.get("owner_fields", [])), ("category_labels", cfg.get("category_label_fields", []))):
        for f in fields:
            if f in names:
                vc = (lf.select(pl.col(f).cast(pl.Utf8).str.strip_chars().alias("v")).drop_nulls().filter(pl.col("v") != "")
                      .group_by("v").len().sort("len", descending=True).head(top).collect())
                for v, n in vc.iter_rows():
                    out[key][v] = out[key].get(v, 0) + int(n)
    return out


def ingest_file(path: str | Path, source: str, raw_sink: Path | None = None, overrides: dict | None = None):
    """Return (pandas frame, report parts) for a file. The raw zone is streamed to ``raw_sink``."""
    lf = scan(path)
    if raw_sink is not None:
        raw_sink.parent.mkdir(parents=True, exist_ok=True)
        lf.sink_parquet(raw_sink)  # raw zone: exactly as received (text), streamed
    detection, adapter = detect(lf, overrides)
    mapping = dict(detection.mapping)
    notes: list[str] = []
    # a date column whose values never parse stays an attribute (e.g. SellerSprite's numeric listing age)
    for f in ("timestamp", "launch_date"):
        if f in mapping:
            ok = lf.head(SAMPLE_ROWS).select(_parse_date(mapping[f]).is_not_null().any()).collect().item()
            if not ok:
                notes.append(f"'{mapping[f]}' mapped to {f} but no values parse as dates -- kept as attribute only")
                mapping.pop(f)
    projected, _ = project(lf, mapping, source)
    frame = add_record_ids(projected.collect(engine="streaming"), source)
    ids = frame["id"].head(SAMPLE_ROWS)
    amazon_like = adapter == "sellersprite" or (len(ids) > 0 and ids.str.starts_with("B0").mean() > 0.5)
    frame = frame.with_columns(rejection_exprs(amazon_like).alias("rejection_reasons"))
    derived = int(frame["_revenue_derived"].sum())
    if derived:
        notes.append(f"revenue derived as price*sales for {derived} records")
    reasons = (frame.select(pl.col("rejection_reasons").explode(empty_as_null=True).alias("r")).drop_nulls()
               .group_by("r").len().sort("len", descending=True))
    rejected = int((frame["rejection_reasons"].list.len() > 0).sum())
    report = {
        "raw_rows": frame.height, "accepted": frame.height - rejected, "rejected": rejected,
        "rejection_reasons": {r: int(n) for r, n in reasons.iter_rows()},
        "adapter": adapter, "column_mapping": mapping,
        "mapping_confidence": {k: round(v, 3) for k, v in detection.confidence.items() if k in mapping},
        "unmapped_columns": [c for c in lf.collect_schema().names() if c not in set(mapping.values())],
        "warnings": list(detection.warnings) + notes, "engine": "polars",
    }
    return frame, report, adapter, owner_hints(lf)


def to_pandas_frame(frame: pl.DataFrame) -> pd.DataFrame:
    """Hand the Polars result to the (pandas) downstream stages in the v1 column layout."""
    reasons = frame["rejection_reasons"].to_list()          # native list-of-lists, no per-row numpy arrays
    pdf = frame.drop("_row", "rejection_reasons").to_pandas()
    pdf["rejection_reasons"] = pd.Series(reasons, index=pdf.index, dtype=object)
    pdf["accepted"] = pdf["rejection_reasons"].map(len) == 0
    for c in TEXT_FIELDS:  # missing text is None (as in the v1 adapters), never a float NaN
        pdf[c] = pdf[c].astype(object).where(pdf[c].notna(), None)
    for c in ("timestamp", "launch_date"):
        pdf[c] = pd.to_datetime(pdf[c]).astype("datetime64[ns]")
    for c in NUMERIC_FIELDS:
        pdf[c] = pdf[c].astype("float64")
    return pdf[[*RECORD_COLUMNS, "_revenue_derived", "rejection_reasons", "accepted"]]


__all__ = ["FAST_EXTENSIONS", "ingest_file", "to_pandas_frame", "scan"]
