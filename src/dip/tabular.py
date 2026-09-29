"""Reading exported tables whose headers vary by tool and language (keyword exports, Seller Central reports, ...).

A module's config gives ``columns`` (field -> header synonyms), ``min_header_score`` and ``required``; headers are
matched like listing imports (src/dmie/engine/ingestion/schema_detector.py: exact, token-contained, CJK substring).
"""

from __future__ import annotations

import re

import pandas as pd

_BRACKETS = re.compile(r"[()（）\[\]【】]")


def _unbracket(text) -> str:
    """Bracketed words are part of a header's meaning here ("(Child) ASIN" vs "(Parent) ASIN"), so keep them as words
    instead of letting header normalisation drop them as a unit annotation."""
    return _BRACKETS.sub(" ", str(text))


def map_headers(columns: list, cfg: dict) -> tuple[dict[str, str], dict[str, float]]:
    from dmie.engine.ingestion.schema_detector import header_score, normalize_header

    syn = {f: [normalize_header(_unbracket(s)) for s in v] for f, v in cfg["columns"].items()}
    cands = sorted(((header_score(_unbracket(c), s), f, str(c)) for f, s in syn.items() for c in columns), reverse=True)
    mapping: dict[str, str] = {}
    scores: dict[str, float] = {}
    used: set[str] = set()
    for sc, f, c in cands:
        if sc < float(cfg["min_header_score"]) or f in mapping or c in used:
            continue
        mapping[f], scores[f] = c, round(sc, 2)
        used.add(c)
    return mapping, scores


def find_header(raw: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Some exports put a title or filter line above the header row: use the first row that maps the required fields."""
    mapping, _ = map_headers(list(raw.columns), cfg)
    if all(f in mapping for f in cfg["required"]):
        return raw
    for i in range(min(8, len(raw))):
        cols = [str(x) for x in raw.iloc[i].tolist()]
        m, _ = map_headers(cols, cfg)
        if all(f in m for f in cfg["required"]):
            out = raw.iloc[i + 1:].copy()
            out.columns = cols
            return out.reset_index(drop=True)
    return raw


def num(s: pd.Series, rate: bool = False) -> pd.Series:
    txt = s.astype(str).str.strip()
    pct = txt.str.endswith("%")
    v = pd.to_numeric(txt.str.replace(r"[,$¥€£%\s]", "", regex=True).replace({"": None, "-": None, "--": None, "nan": None,
                                                                             "None": None}), errors="coerce")
    if rate:
        v = v.where(~pct, v / 100.0)
        v = v.where(~((~pct) & (v > 1)), v / 100.0)      # 12.5 in a rate column means 12.5 %
    return v
