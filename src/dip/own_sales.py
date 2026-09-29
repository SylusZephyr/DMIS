"""Own sales: your Seller Central Business Reports -- the ground truth everything estimated is checked against.

    import_report("BusinessReport-2026-08.csv", "2026-08-01", "2026-08-31")
    estimate_check()        # how far the market estimates (SellerSprite, sales badges) sat from your real sales

* **Import**: headers matched with the synonyms in ``config/platform/own_sales.yaml`` (English or Chinese Seller
  Central). A report covers one period, given at import (the report file itself does not say which). Re-importing the
  same ASIN and period replaces it.
* **Monthly units**: every period is expressed per month (``units / days x days_per_month``) before comparisons.
* **Project outcomes** (src/dip/projects.py) use these exact numbers for the tracked listings when they exist,
  instead of the market estimates.
* **Estimate check**: for each report row, the market observation of the same ASIN nearest to the period's midpoint
  (within ``estimate_check.max_days_apart``). For a market whose sales column is a "bought in past month" badge the
  estimate is an interval [badge, next rung); the check says whether your actual fell inside it. Otherwise the ratio
  actual / estimate is reported. Nothing is adjusted automatically: the numbers are for review.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dip.settings import PROJECT_ROOT
from dip.storage import business as b
from dip.tabular import find_header, map_headers, num

FIELDS_NUM = ("units", "revenue", "orders", "sessions", "page_views")
FIELDS_RATE = ("conversion", "buy_box")


def config() -> dict:
    return yaml.safe_load((PROJECT_ROOT / "config" / "platform" / "own_sales.yaml").read_text(encoding="utf-8"))


def _day(v) -> date:
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError as exc:
        raise ValueError(f"'{v}' is not a date (YYYY-MM-DD)") from exc


def read(source: str | Path | pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    from dmie.engine.ingestion.adapters import read_table

    cfg = config()
    raw = source.copy() if isinstance(source, pd.DataFrame) else read_table(Path(source))
    raw = find_header(raw, cfg)
    mapping, scores = map_headers(list(raw.columns), cfg)
    missing = [f for f in cfg["required"] if f not in mapping]
    if missing:
        raise ValueError(f"no column found for {', '.join(missing)} (headers: {', '.join(map(str, raw.columns[:30]))}); "
                         "add a synonym in config/platform/own_sales.yaml")
    out = pd.DataFrame({"asin": raw[mapping["asin"]].astype(str).str.strip().str.upper()})
    for f in ("parent_asin", "sku", "title"):
        if f in mapping:
            out[f] = raw[mapping[f]].where(raw[mapping[f]].notna(), None).map(lambda v: None if v is None else str(v).strip())
    for f in FIELDS_NUM:
        if f in mapping:
            out[f] = num(raw[mapping[f]])
    for f in FIELDS_RATE:
        if f in mapping:
            out[f] = num(raw[mapping[f]], rate=True)
    notes = []
    bad = ~out["asin"].str.fullmatch(r"[A-Z0-9]{10}")
    if bad.any():
        notes.append(f"{int(bad.sum())} rows without an ASIN (totals or empty lines)")
    out = out[~bad]
    nounits = out["units"].isna()
    if nounits.any():
        notes.append(f"{int(nounits.sum())} rows without units ordered")
    out = out[~nounits]
    if out["asin"].duplicated().any():
        notes.append(f"{int(out['asin'].duplicated().sum())} repeated ASIN rows were summed")
        agg = {f: "sum" for f in FIELDS_NUM if f in out}
        agg.update({f: "first" for f in out.columns if f not in agg and f != "asin"})
        out = out.groupby("asin", as_index=False).agg(agg)
    return out.reset_index(drop=True), {"mapping": mapping, "header_scores": scores, "notes": notes,
                                        "unmapped": [str(c) for c in raw.columns if c not in mapping.values()]}


def import_report(source: str | Path | pd.DataFrame, period_start: str, period_end: str, source_name: str | None = None,
                  by: str | None = None) -> dict:
    p0, p1 = _day(period_start), _day(period_end)
    if p1 < p0:
        raise ValueError("the period ends before it starts")
    rows, meta = read(source)
    name = source_name or (Path(source).name if not isinstance(source, pd.DataFrame) else "frame")
    replaced = 0
    with b.session() as s:
        for r in rows.replace({np.nan: None}).to_dict("records"):
            old = s.query(b.OwnSale).filter_by(asin=r["asin"], period_start=p0.isoformat(), period_end=p1.isoformat()).first()
            if old is not None:
                s.delete(old)
                s.flush()
                replaced += 1
            s.add(b.OwnSale(**{k: v for k, v in r.items() if hasattr(b.OwnSale, k)}, period_start=p0.isoformat(),
                            period_end=p1.isoformat(), source_name=name, imported_by=by))
    from dip import audit
    audit.record("own_sales.import", by, "own_sales", None, {"rows": len(rows), "period": [p0.isoformat(), p1.isoformat()],
                                                             "source": name})
    return {**meta, "rows": int(len(rows)), "replaced": replaced, "period": [p0.isoformat(), p1.isoformat()],
            "units": float(rows["units"].sum()), "revenue": float(rows["revenue"].sum()) if "revenue" in rows else None}


def _frame(asins: list[str] | None = None) -> pd.DataFrame:
    with b.session() as s:
        q = s.query(b.OwnSale)
        if asins:
            q = q.filter(b.OwnSale.asin.in_([a.upper() for a in asins]))
        df = pd.DataFrame([b.row_dict(r) for r in q.all()])
    if df.empty:
        return df
    days = (pd.to_datetime(df["period_end"]) - pd.to_datetime(df["period_start"])).dt.days + 1
    per_month = float(config()["days_per_month"]) / days
    return df.assign(days=days, units_month=(df["units"] * per_month).round(2),
                     revenue_month=(df["revenue"] * per_month).round(2),
                     midpoint=pd.to_datetime(df["period_start"]) + pd.to_timedelta(days / 2, unit="D"))


def monthly(asins: list[str]) -> pd.DataFrame:
    """Your monthly units / revenue per report period for the given listings (summed across them), by period end."""
    df = _frame(asins)
    if df.empty:
        return pd.DataFrame(columns=["period", "sales", "revenue", "listings"])
    g = df.groupby("period_end")
    return pd.DataFrame({"sales": g["units_month"].sum(), "revenue": g["revenue_month"].sum(min_count=1),
                         "listings": g["asin"].nunique()}).reset_index().rename(columns={"period_end": "period"})


def rows(limit: int = 500) -> list[dict]:
    df = _frame()
    if df.empty:
        return []
    df = df.sort_values(["period_end", "units"], ascending=[False, False]).head(limit)
    return df.drop(columns=["midpoint"]).replace({np.nan: None}).to_dict("records")


# ---------------------------------------------------------------- how good were the market estimates?
def _market_observations(asins: set[str]) -> pd.DataFrame:
    from dip.storage import lake

    with b.session() as s:
        markets = [(m.name, ((((m.summary or {}).get("metrics_v3") or {}).get("sales_observation")) or {}).get("kind"))
                   for m in s.query(b.Market).all()]
    ids = ", ".join("'" + a.replace("'", "") + "'" for a in sorted(asins))
    frames = []
    for name, kind in markets:
        if not lake.has_curated("observation_history", name):
            continue
        h = lake.read_curated("observation_history", name, columns=["entity_id", "value", "observed_at", "source"],
                              where=f"entity_type = 'listing' AND metric = 'sales' AND upper(entity_id) IN ({ids})")
        if len(h):
            frames.append(h.assign(market=name, kind=kind or "estimate"))
    if not frames:
        return pd.DataFrame(columns=["entity_id", "value", "observed_at", "source", "market", "kind"])
    out = pd.concat(frames, ignore_index=True)
    return out.assign(value=pd.to_numeric(out["value"], errors="coerce"), observed_at=pd.to_datetime(out["observed_at"]))


def estimate_check() -> dict:
    df = _frame()
    if df.empty:
        return {"rows": [], "compared": 0, "not_observed": 0}
    obs = _market_observations(set(df["asin"]))
    max_days = int(config()["estimate_check"]["max_days_apart"])
    from dip.metrics.observation import observe
    out = []
    for r in df.itertuples():
        o = obs[obs["entity_id"].str.upper() == r.asin]
        row = {"asin": r.asin, "title": r.title, "period_start": r.period_start, "period_end": r.period_end,
               "actual_units_month": float(r.units_month)}
        if o.empty:
            out.append({**row, "status": "not_observed"})
            continue
        gap = (o["observed_at"] - r.midpoint).abs().dt.days
        o = o.assign(gap=gap)[gap <= max_days].dropna(subset=["value"])
        if o.empty:
            out.append({**row, "status": "no_observation_near_period"})
            continue
        best = o.sort_values("gap").iloc[0]
        est = float(best["value"])
        row.update({"status": "compared", "market": best["market"], "observed_at": str(best["observed_at"].date()),
                    "days_apart": int(best["gap"]), "estimate": est, "estimate_kind": best["kind"],
                    "ratio_actual_to_estimate": round(r.units_month / est, 3) if est else None})
        if best["kind"] == "badge":
            ob = observe(pd.Series([est]), "badge")
            lo, hi = float(ob.lower[0]), float(ob.upper[0])
            row.update({"estimate_interval": [lo, hi if np.isfinite(hi) else None],
                        "within_interval": bool(lo <= r.units_month < hi)})
        out.append(row)
    cmp_ = [x for x in out if x["status"] == "compared" and x.get("ratio_actual_to_estimate") is not None]
    badge = [x for x in cmp_ if "within_interval" in x]
    ratios = pd.Series([x["ratio_actual_to_estimate"] for x in cmp_], dtype=float)
    return {"rows": out, "compared": len(cmp_), "not_observed": sum(1 for x in out if x["status"] != "compared"),
            "median_ratio": float(ratios.median()) if len(ratios) else None,
            "ratio_p25_p75": [float(ratios.quantile(0.25)), float(ratios.quantile(0.75))] if len(ratios) >= 4 else None,
            "badge_compared": len(badge), "within_interval_share": round(sum(x["within_interval"] for x in badge) / len(badge), 3) if badge else None,
            "note": "ratio > 1: you sold more than the market data estimated. Nothing is adjusted automatically."}
