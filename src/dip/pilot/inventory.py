"""Phase 0 data inventory: every raw file, what was processed from it, coverage of the fields the
metrics depend on, and anomalies that need a human decision. Read-only over data/raw."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from dip.settings import PROJECT_ROOT
from dip.storage import business as b
from dip.storage import lake

RAW_SUFFIXES = {".xlsx", ".xls", ".csv", ".json"}
COVERAGE_FIELDS = ("price", "sales", "revenue", "rating", "reviews", "brand", "launch_date", "review_text")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def raw_files(raw_dir: Path | None = None) -> pd.DataFrame:
    raw_dir = Path(raw_dir or PROJECT_ROOT / "data" / "raw")
    rows = [{"market_folder": p.parent.name if p.parent != raw_dir else None, "file": str(p.relative_to(raw_dir)),
             "bytes": p.stat().st_size, "sha256": _sha(p)}
            for p in sorted(raw_dir.rglob("*")) if p.is_file() and p.suffix.lower() in RAW_SUFFIXES]
    df = pd.DataFrame(rows, columns=["market_folder", "file", "bytes", "sha256"])
    first = df.groupby("sha256")["file"].transform("first")
    df["byte_identical_to"] = first.where(first != df["file"]).fillna("")
    return df


def coverage(market: str) -> dict:
    if not lake.has_curated("records", market):
        return {}
    r = lake.read_curated("records", market)
    acc = r[r["accepted"].fillna(False).astype(bool)] if "accepted" in r else r
    n = len(acc)
    out = {"records": n}
    for f in COVERAGE_FIELDS:
        if f in acc:
            col = acc[f]
            out[f] = float((col.notna() & (col.astype(str).str.strip() != "")).mean()) if n else 0.0
        else:
            out[f] = None
    out["relevance"] = acc["relevance_status"].value_counts().to_dict() if "relevance_status" in acc else {}
    out["excluded"] = r["excluded_reason"].dropna().value_counts().to_dict() if "excluded_reason" in r else {}
    return out


def inventory() -> dict:
    files = raw_files()
    with b.session() as s:
        ds = [b.row_dict(d) for d in s.query(b.Dataset).order_by(b.Dataset.market_name, b.Dataset.created_at).all()]
        markets = {m.name: dict(m.summary or {}) for m in s.query(b.Market).all()}
    processed = {Path(d["source_name"]).name for d in ds}
    files["processed"] = files["file"].map(lambda f: Path(f).name in processed)
    per_market = []
    for name, su in sorted(markets.items()):
        mine = [d for d in ds if d["market_name"] == name]
        per_market.append({"market": name, "snapshots": len(mine),
                           "snapshot_dates": [d["snapshot_date"] for d in mine],
                           "history_periods": su.get("history_periods") or [],
                           "datasets": [{k: d[k] for k in ("source_name", "raw_rows", "accepted_rows", "rejected_rows",
                                                               "duplicate_of")} | {
                               "rejection_reasons": (d["report"] or {}).get("rejection_reasons") or {},
                               "unmapped_columns": (d["report"] or {}).get("unmapped_columns") or [],
                               "warnings": (d["report"] or {}).get("warnings") or [],
                               "column_mapping": (d["report"] or {}).get("column_mapping") or {}} for d in mine],
                           "coverage": coverage(name),
                           "products": (su.get("discovery") or {}).get("models"),
                           })
    return {"files": files, "markets": per_market, "issues": issues(files, per_market)}


def issues(files: pd.DataFrame, markets: list[dict]) -> list[str]:
    """Anomalies that need a human decision -- stated, never silently fixed."""
    out = []
    for _, f in files[files["byte_identical_to"] != ""].iterrows():
        out.append(f"`{f['file']}` is byte-identical to `{f['byte_identical_to']}` -- same data under two market names?")
    for _, f in files[~files["processed"] & files["market_folder"].notna()].iterrows():
        out.append(f"`{f['file']}` was not processed (bootstrap takes one export per market folder; "
                   f"use `dmis.py process-dir` for successive snapshots).")
    for m in markets:
        c = m["coverage"]
        if m["snapshots"] < 2:
            out.append(f"{m['market']}: {m['snapshots']} snapshot -- trends, growth, change alerts and backtests need >= 2 (ideally 3-6 monthly).")
        if not any(m["snapshot_dates"]):
            out.append(f"{m['market']}: no snapshot date recorded -- history ordering falls back to upload order.")
        if c.get("sales") is not None and c["sales"] < 0.5:
            out.append(f"{m['market']}: monthly sales known for {c['sales']:.0%} of records -- market size is a lower bound.")
        if not c.get("reviews"):
            out.append(f"{m['market']}: no review counts in the source.")
        if not c.get("review_text"):
            out.append(f"{m['market']}: no review text -- customer-pain analysis unavailable.")
        rel = c.get("relevance") or {}
        if rel and set(rel) == {"relevant"}:
            out.append(f"{m['market']}: every record classified relevant -- the export may be pre-filtered; relevance precision must be checked by labelling.")
        for d in m["datasets"]:
            for w in d["warnings"]:
                out.append(f"{m['market']} / {d['source_name']}: {w}")
    return out


def _pct(v):
    return "—" if v is None else f"{v:.0%}"


def to_markdown(inv: dict) -> str:
    L = ["# Data inventory (Phase 0)", "",
         "Generated by `python scripts/pilot.py inventory` from `data/raw/` (read-only) and the processed platform data.", "",
         "## Raw files", "", "| File | Bytes | Processed | Byte-identical to |", "|---|---|---|---|"]
    for _, f in inv["files"].iterrows():
        L.append(f"| `{f['file']}` | {f['bytes']:,} | {'yes' if f['processed'] else 'no'} | {f['byte_identical_to'] or ''} |")
    L += ["", "## Markets", "",
          "| Market | Snapshots | Raw rows | Accepted | Rejected (reasons) | Price | Sales | Rating | Review count | Review text | Launch date |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in inv["markets"]:
        c = m["coverage"]
        raw = sum(d["raw_rows"] for d in m["datasets"])
        acc = sum(d["accepted_rows"] for d in m["datasets"])
        rej = sum(d["rejected_rows"] for d in m["datasets"])
        reasons = {}
        for d in m["datasets"]:
            for k, v in d["rejection_reasons"].items():
                reasons[k] = reasons.get(k, 0) + v
        rs = ", ".join(f"{k} {v}" for k, v in reasons.items())
        L.append(f"| {m['market']} | {m['snapshots']} | {raw:,} | {acc:,} | {rej}{f' ({rs})' if rs else ''} | "
                 + " | ".join(_pct(c.get(k)) for k in ("price", "sales", "rating", "reviews", "review_text", "launch_date")) + " |")
    L += ["", "Coverage = share of accepted records where the field is present.", ""]
    for m in inv["markets"]:
        L += [f"### {m['market']}", ""]
        for d in m["datasets"]:
            L.append(f"- `{d['source_name']}`: {d['raw_rows']} raw, {d['accepted_rows']} accepted, {d['rejected_rows']} rejected.")
            if d["unmapped_columns"]:
                L.append(f"  - Not mapped to a standard field (kept in attributes; config-listed ones such as unit cost "
                         f"and product manager are still used): {', '.join(d['unmapped_columns'])}")
            if d["warnings"]:
                L.append(f"  - Warnings: {'; '.join(d['warnings'])}")
        rel = m["coverage"].get("relevance") or {}
        if rel:
            L.append(f"- Relevance: {', '.join(f'{k} {v}' for k, v in rel.items())}")
        ex = m["coverage"].get("excluded") or {}
        if ex:
            L.append(f"- Excluded from market metrics (with reason): {', '.join(f'{k} {v}' for k, v in ex.items())}")
        L.append("")
    L += ["## Issues needing a decision", ""] + [f"{i}. {x}" for i, x in enumerate(inv["issues"], 1)] + [""]
    return "\n".join(L)
