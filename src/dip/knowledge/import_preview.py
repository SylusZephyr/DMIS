"""Import preview (spec 64-66): inspect an uploaded file before it is processed.

    upload -> file inspection -> column detection -> mapping with confidence -> preview + validation
           -> the person confirms or corrects the mapping -> import (POST /datasets with ``mapping``)

The mapping comes from the same detector the pipeline uses (``fast.detect``), so the preview shows exactly
what the import would do. Each field gets its confidence, a level (confident / confirm / uncertain) and the
alternative columns the detector considered. Nothing is written anywhere by a preview.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from dmie.engine.records import UNIVERSAL_FIELDS

ASIN = re.compile(r"^(B0[0-9A-Z]{8}|[0-9]{9}[0-9X])$")
URL = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.I)
SAMPLE_ROWS = 5000


def _level(conf: float) -> str:
    return "confident" if conf >= 0.85 else "confirm" if conf >= 0.6 else "uncertain"


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(r"[,$¥€£\s]", "", regex=True).replace({"": None, "nan": None, "None": None}),
                         errors="coerce")


def quality(df: pd.DataFrame, mapping: dict[str, str]) -> dict[str, dict]:
    """Per mapped field: missing share, invalid count and a few invalid examples (spec 66)."""
    out: dict[str, dict] = {}
    for field, col in mapping.items():
        if col not in df:
            continue
        s = df[col]
        blank = s.isna() | (s.astype(str).str.strip() == "")
        info: dict = {"column": col, "missing_share": round(float(blank.mean()), 3), "invalid": 0, "examples": []}
        present = s[~blank].astype(str)
        bad = pd.Series(False, index=present.index)
        if field in ("price", "sales", "revenue", "reviews"):
            v = _num(present)
            bad = v.isna() | (v < 0) | ((v == 0) & (field == "price"))
            info["rule"] = "a non-negative number" + (" above 0" if field == "price" else "")
        elif field == "rating":
            v = _num(present)
            bad = v.isna() | (v < 0) | (v > 5)
            info["rule"] = "a number between 0 and 5"
        elif field == "url":
            bad = ~present.str.match(URL)
            info["rule"] = "an http(s) URL"
        elif field == "id":
            dup = present.duplicated(keep=False)
            info["duplicates"] = int(dup.sum())
            info["asin_like_share"] = round(float(present.str.match(ASIN).mean()), 3) if len(present) else None
            info["rule"] = "an identifier (duplicates reported; repeated ids across snapshots are expected)"
        elif field in ("timestamp", "launch_date"):
            bad = pd.to_datetime(present, errors="coerce").isna()
            info["rule"] = "a date"
        info["invalid"] = int(bad.sum())
        info["examples"] = present[bad].head(3).tolist()
        out[field] = info
    return out


def preview(path: str | Path, overrides: dict | None = None, sample_rows: int = 8) -> dict:
    from dip.pipeline.ingestion import fast

    lf = fast.scan(path)
    det, adapter = fast.detect(lf, overrides)
    df = lf.head(SAMPLE_ROWS).collect().to_pandas().dropna(how="all")
    cols = [{"name": c, "non_null": round(float(df[c].notna().mean()), 3) if len(df) else 0.0,
             "examples": df[c].dropna().astype(str).head(3).str.slice(0, 80).tolist()} for c in df.columns]
    fields = []
    for f in UNIVERSAL_FIELDS:
        col = det.mapping.get(f)
        conf = float(det.confidence.get(f, 0.0)) if col else 0.0
        cands = [{"column": c, "score": round(float(s), 3)} for c, s in (det.candidates.get(f) or []) if c != col][:2]
        fields.append({"field": f, "column": col, "confidence": round(conf, 3) if col else None,
                       "level": _level(conf) if col else "unmapped", "alternatives": cands,
                       "pinned": bool(overrides and f in overrides)})
    mapped = {f: c for f, c in det.mapping.items() if c in df}
    sample = df.head(sample_rows)[[c for c in mapped.values()]].rename(columns={c: f for f, c in mapped.items()})
    return {"file": Path(path).name, "adapter": adapter, "rows_sampled": int(len(df)), "columns": cols, "fields": fields,
            "unmapped_columns": det.unmapped_columns, "warnings": det.warnings, "quality": quality(df, mapped),
            "sample": sample.astype(object).where(sample.notna(), None).to_dict("records"),
            "needs_confirmation": [x["field"] for x in fields if x["level"] in ("confirm", "uncertain")]}
