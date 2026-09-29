"""Keyword intelligence: a keyword export (SellerSprite 关键词挖掘 / 反查关键词, or any tool with the same kind of
columns) turned into keyword demand, competition and gaps for a market and each of its sub-categories.

    import_keywords("denture_base", "kw.xlsx")     # map headers, parse, assign to sub-categories, score, store
    rows("denture_base", gaps=True)                # under-served keywords: demand that few listings target

* **Headers** are mapped with the synonyms in ``config/platform/keywords.yaml`` (the same matching as listing
  imports); the mapping and its scores are returned so it can be checked.
* **Sub-category** of a keyword: the market's own listings whose title contains every content word of the keyword;
  the keyword goes to the sub-category holding most of their sales, with that share as its confidence. Keywords that
  match too few listings stay unassigned -- they are kept and shown, never dropped.
* **Score** 0-100: demand against competition, each component a percentile rank inside the market's keyword list;
  a component whose column is missing is left out and the coverage says so.
* **Gap**: at least median searches, and few first-page listings naming the keyword in their title.

Everything here is deterministic (no model). Nothing is silently discarded: blank and duplicate keyword rows are
counted in the import notes.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dip.settings import PROJECT_ROOT
from dip.storage import lake
from dip.tabular import find_header as _find_header
from dip.tabular import map_headers as _map_headers
from dip.tabular import num as _num

TABLE = "keywords"
RATES = ("purchase_rate", "click_share", "conversion_share")
LOWER_IS_BETTER = {"products", "title_density", "ppc_bid", "click_share"}
_WORD = re.compile(r"[a-z0-9]+")


def config() -> dict:
    return yaml.safe_load((PROJECT_ROOT / "config" / "platform" / "keywords.yaml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- reading
def read(source: str | Path | pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """A keyword export as a clean frame (one row per keyword) plus what was mapped and what was merged."""
    from dmie.engine.ingestion.adapters import read_table

    cfg = config()
    raw = source.copy() if isinstance(source, pd.DataFrame) else read_table(Path(source))
    raw = _find_header(raw, cfg)
    mapping, scores = _map_headers(list(raw.columns), cfg)
    missing = [f for f in cfg["required"] if f not in mapping]
    if missing:
        raise ValueError(f"no column found for {', '.join(missing)} (headers: {', '.join(map(str, raw.columns[:30]))}); "
                         "add a synonym in config/platform/keywords.yaml")
    out = pd.DataFrame({"keyword": raw[mapping["keyword"]].astype(str).str.strip()})
    for f, col in mapping.items():
        if f in ("keyword", "translation"):
            if f == "translation":
                out[f] = raw[col].astype(str).where(raw[col].notna(), None)
            continue
        out[f] = _num(raw[col], rate=f in RATES)
    notes = []
    blank = out["keyword"].isin(["", "nan", "None"]) | out["keyword"].isna()
    if blank.any():
        notes.append(f"{int(blank.sum())} rows without a keyword (not keywords: summary or empty lines)")
    out = out[~blank].copy()
    out["keyword_key"] = out["keyword"].str.lower().str.replace(r"\s+", " ", regex=True)
    dup = out.duplicated("keyword_key", keep=False)
    if dup.any():
        n = int(out["keyword_key"][dup].nunique())
        notes.append(f"{n} keywords appeared more than once; kept the row with the most searches")
        out = out.sort_values("searches", ascending=False, na_position="last").drop_duplicates("keyword_key")
    return out.reset_index(drop=True), {"mapping": mapping, "header_scores": scores, "notes": notes,
                                        "unmapped": [str(c) for c in raw.columns if c not in mapping.values()]}


# ---------------------------------------------------------------- sub-category of each keyword
def _content_words(text: str, stop: set[str]) -> set[str]:
    words = set()
    for w in _WORD.findall(str(text).lower()):
        if w in stop or len(w) < 2:
            continue
        words.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return words


def assign(market: str, kw: pd.DataFrame) -> pd.DataFrame:
    cfg = config()["matching"]
    stop = set(cfg["stopwords"])
    out = kw.assign(segment_id=None, segment_label=None, matched_listings=0, assign_share=np.nan)
    if not lake.has_curated("listings", market):
        return out
    li = lake.read_curated("listings", market, columns=["id", "title", "segment_id", "segment_label", "sales"])
    li = li[li["segment_id"].notna()]
    if li.empty:
        return out
    titles = [_content_words(t, stop) for t in li["title"].fillna("")]
    seg = li["segment_id"].astype(str).tolist()
    lab = dict(zip(li["segment_id"].astype(str), li["segment_label"]))
    w = pd.to_numeric(li["sales"], errors="coerce").fillna(0).clip(lower=0).to_numpy() + 1.0   # every listing counts a little
    found: list[tuple[str | None, str | None, int, float]] = []
    for k in out["keyword"]:
        words = _content_words(k, stop)
        hit = [i for i, t in enumerate(titles) if words and words <= t]
        if len(hit) < int(cfg["min_listings"]):
            found.append((None, None, len(hit), np.nan))
            continue
        by: dict[str, float] = {}
        for i in hit:
            by[seg[i]] = by.get(seg[i], 0.0) + w[i]
        best = max(by, key=lambda s: by[s])
        found.append((best, lab.get(best), len(hit), by[best] / sum(by.values())))
    sid, slab, n_m, share = zip(*found) if found else ((), (), (), ())
    return out.assign(segment_id=list(sid), segment_label=list(slab), matched_listings=list(n_m),
                      assign_share=np.round(np.array(share, dtype=float), 3))


# ---------------------------------------------------------------- scoring
def score(kw: pd.DataFrame) -> pd.DataFrame:
    cfg = config()["score"]
    weights = {k: float(v) for k, v in cfg["weights"].items()}
    comps = {}
    for f in weights:
        if f not in kw or kw[f].notna().sum() < 2:
            continue
        r = kw[f].rank(pct=True)
        comps[f] = (1.0 - r + 1.0 / len(kw)) if f in LOWER_IS_BETTER else r
    total_w = sum(weights[f] for f in comps) or 1.0
    s = sum(comps[f].fillna(0.5) * weights[f] for f in comps) / total_w if comps else pd.Series(np.nan, index=kw.index)
    out = kw.assign(score=(100 * s).round(1), coverage=round(total_w / sum(weights.values()), 2) if comps else 0.0)
    out["components"] = [json.dumps({f: round(float(comps[f].iat[i]), 3) for f in comps if pd.notna(comps[f].iat[i])})
                         for i in range(len(out))]
    g = cfg["gap"]
    med = out["searches"].quantile(float(g["min_searches_pct"])) if out["searches"].notna().any() else np.inf
    if "title_density" in out:
        out["is_gap"] = (out["searches"] >= med) & (out["title_density"] <= float(g["max_title_density"]))
    else:
        out["is_gap"] = False
    return out


# ---------------------------------------------------------------- import + read
def import_keywords(market: str, source: str | Path | pd.DataFrame, source_name: str | None = None, by: str | None = None) -> dict:
    market = lake.validate_market_name(market)
    kw, meta = read(source)
    kw = score(assign(market, kw))
    kw["source_name"] = source_name or (Path(source).name if not isinstance(source, pd.DataFrame) else "frame")
    kw["imported_at"] = datetime.now(timezone.utc).isoformat()
    with lake.market_lock(market):
        lake.write_curated(TABLE, market, kw.drop(columns=["keyword_key"]))
    summ = summary(market, kw)
    from dip import audit
    audit.record("keywords.import", by, "keywords", market, {"keywords": len(kw), "source": kw["source_name"].iat[0] if len(kw) else None})
    return {**meta, **summ}


def _frame(market: str) -> pd.DataFrame:
    return lake.read_curated(TABLE, market) if lake.has_curated(TABLE, market) else pd.DataFrame()


def summary(market: str, kw: pd.DataFrame | None = None) -> dict:
    kw = _frame(market) if kw is None else kw
    if kw.empty:
        return {"market": market, "keywords": 0, "segments": []}
    segs = []
    for (sid, lab), g in kw[kw["segment_id"].notna()].groupby(["segment_id", "segment_label"], dropna=False):
        top = g.sort_values("searches", ascending=False).head(5)
        segs.append({"segment_id": sid, "segment_label": lab, "keywords": int(len(g)),
                     "searches": float(g["searches"].sum()), "purchases": float(g["purchases"].sum()) if "purchases" in g else None,
                     "purchase_rate": float((g["purchase_rate"] * g["searches"]).sum() / g["searches"].sum())
                     if "purchase_rate" in g and g["searches"].sum() > 0 and g["purchase_rate"].notna().any() else None,
                     "ppc_bid_median": float(g["ppc_bid"].median()) if "ppc_bid" in g and g["ppc_bid"].notna().any() else None,
                     "gaps": int(g["is_gap"].sum()), "top_keywords": top["keyword"].tolist()})
    segs.sort(key=lambda r: -r["searches"])
    un = kw[kw["segment_id"].isna()]
    return {"market": market, "keywords": int(len(kw)), "assigned": int(kw["segment_id"].notna().sum()),
            "unassigned": int(len(un)), "gaps": int(kw["is_gap"].sum()), "searches": float(kw["searches"].sum()),
            "coverage": float(kw["coverage"].iat[0]) if len(kw) else None,
            "columns": [c for c in ("searches", "purchases", "purchase_rate", "spr", "title_density", "products", "supply_demand",
                                    "ppc_bid", "click_share", "conversion_share", "aba_rank") if c in kw and kw[c].notna().any()],
            "source_name": kw["source_name"].iat[0] if "source_name" in kw and len(kw) else None,
            "imported_at": kw["imported_at"].iat[0] if "imported_at" in kw and len(kw) else None, "segments": segs}


SORTABLE = {"score", "searches", "purchases", "purchase_rate", "products", "title_density", "ppc_bid", "spr", "supply_demand", "aba_rank"}


def rows(market: str, segment: str | None = None, gaps: bool = False, q: str | None = None, sort: str = "score",
         desc: bool = True, limit: int = 100, offset: int = 0) -> dict:
    kw = _frame(market)
    if kw.empty:
        return {"total": 0, "rows": []}
    if segment == "unassigned":
        kw = kw[kw["segment_id"].isna()]
    elif segment:
        kw = kw[kw["segment_id"] == segment]
    if gaps:
        kw = kw[kw["is_gap"]]
    if q:
        kw = kw[kw["keyword"].str.contains(q, case=False, regex=False)]
    col = sort if sort in SORTABLE and sort in kw else "score"
    kw = kw.sort_values(col, ascending=not desc, na_position="last")
    page = kw.iloc[offset:offset + limit].copy()
    page["components"] = [json.loads(c) if isinstance(c, str) else c for c in page["components"]]
    return {"total": int(len(kw)), "rows": page.replace({np.nan: None}).to_dict("records")}
