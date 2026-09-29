"""Competitor intelligence: one profile per brand per market.

Position (Leader / Challenger / Follower / Niche) from share of the market,
price positioning against each segment's median, rating against the market,
breadth (segments covered), launch activity, customer complaints -- turned
into weaknesses and the opportunity each weakness leaves open. With more
than one snapshot, changes between the last two periods are tracked: share,
price, new listings, rating and review growth. Thresholds live in
config/platform/competitors.yaml; every statement cites its number.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dip.settings import PROJECT_ROOT

CONFIG = PROJECT_ROOT / "config" / "platform" / "competitors.yaml"


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load(Path(CONFIG).read_text(encoding="utf-8"))


def _share_basis(p: pd.DataFrame) -> tuple[pd.Series, str]:
    if p["monthly_revenue"].notna().any():
        return p["monthly_revenue"].fillna(0), "revenue"
    if p["monthly_sales"].notna().any():
        return p["monthly_sales"].fillna(0), "sales"
    return pd.Series(1.0, index=p.index), "product_count"


def _position(rank: int, share: float) -> str:
    c = config()["position"]
    if (rank == 1 and share >= c["leader_share"]) or share >= 1.5 * c["leader_share"]:
        return "Leader"
    if share >= c["challenger_share"]:
        return "Challenger"
    if share >= c["follower_share"]:
        return "Follower"
    return "Niche"


def _brand_complaints(pain: dict | None, product_ids: list[str]) -> list[dict]:
    if not pain:
        return []
    agg: dict[str, dict] = {}
    total = 0
    for pid in product_ids:
        rep = pain.get(f"product:{pid}")
        if rep is None or getattr(rep, "status", None) != "ok":
            continue
        total += rep.reviews
        for c in rep.complaints:
            a = agg.setdefault(c["aspect"], {"aspect": c["aspect"], "mentions": 0, "opportunity": c.get("opportunity")})
            a["mentions"] += c["mentions"]
    for a in agg.values():
        a["share_of_reviews"] = round(a["mentions"] / total, 4) if total else None
    return sorted(agg.values(), key=lambda a: -a["mentions"])


def profile(products: pd.DataFrame, listings: pd.DataFrame, segments: pd.DataFrame, as_of: pd.Timestamp,
            history: pd.DataFrame | None = None, pain: dict | None = None) -> pd.DataFrame:
    cfg = config()
    wcfg = cfg["weakness"]
    p = products.assign(brand=products["brand"].fillna("").astype(str).str.strip())
    p = p[p["brand"] != ""]
    if p.empty:
        return pd.DataFrame()
    basis_vals, basis = _share_basis(p)
    p = p.assign(_basis=basis_vals.to_numpy())
    seg_med = dict(zip(segments["segment_id"], segments["price_median"])) if "price_median" in segments else {}
    seg_lab = dict(zip(segments["segment_id"], segments["segment_label"])) if len(segments) else {}
    p = p.assign(_pidx=p["price"] / p["segment_id"].map(seg_med))
    market_rating = float(p["rating"].mean()) if p["rating"].notna().any() else None
    total = float(p["_basis"].sum()) or 1.0
    n_segments = int(p["segment_id"].nunique())
    lst = listings.assign(brand=listings["brand"].fillna("").astype(str).str.strip())
    ld = pd.to_datetime(lst["launch_date"], errors="coerce") if "launch_date" in lst else pd.Series(pd.NaT, index=lst.index)
    lst = lst.assign(_age_m=(as_of - ld).dt.days / 30.44)
    changes = brand_changes(history, dict(zip(listings["id"], listings["brand"]))) if history is not None else {}
    recent_by = (lst["_age_m"] <= wcfg["stale_months"]).groupby(lst["brand"], sort=False).sum().to_dict()
    known_by = lst["_age_m"].notna().groupby(lst["brand"], sort=False).sum().to_dict()
    rows = []
    for brand, g in p.groupby("brand", sort=False):
        share = float(g["_basis"].sum() / total)
        recent = int(recent_by.get(brand, 0))
        known_age = int(known_by.get(brand, 0))
        pidx = float(g["_pidx"].median()) if g["_pidx"].notna().any() else None
        rating = float(g["rating"].mean()) if g["rating"].notna().any() else None
        top_seg = g.groupby("segment_id")["_basis"].sum().sort_values(ascending=False)
        complaints = _brand_complaints(pain, g["product_id"].tolist())
        weak, opp, strong = [], [], []
        if pidx is not None and pidx >= wcfg["high_price_index"]:
            weak.append(f"High price: median {pidx - 1:+.0%} vs segment median")
            opp.append("Affordable alternative at or below the segment median price")
        elif pidx is not None and pidx <= wcfg["low_price_index"]:
            strong.append(f"Price leader: median {pidx - 1:+.0%} vs segment median")
            opp.append("Premium / better-specified alternative")
        if rating is not None and market_rating is not None:
            if rating <= market_rating - wcfg["rating_gap"]:
                weak.append(f"Lower rating: {rating:.1f} vs market {market_rating:.1f}")
                opp.append("Better-quality alternative")
            elif rating >= market_rating + wcfg["rating_gap"]:
                strong.append(f"Higher rating: {rating:.1f} vs market {market_rating:.1f}")
        if n_segments >= 3 and g["segment_id"].nunique() == 1:
            weak.append(f"Narrow range: 1 of {n_segments} segments")
            opp.append("Adjacent segments it does not cover")
        if known_age >= 1 and recent == 0:
            weak.append(f"No new listings in {wcfg['stale_months']} months")
            opp.append("Newer design in its segment")
        for c in complaints[:2]:
            if c["share_of_reviews"] and c["share_of_reviews"] >= wcfg["complaint_share"]:
                weak.append(f"Customers complain about {c['aspect']} ({c['share_of_reviews']:.0%} of its reviews)")
                if c.get("opportunity"):
                    opp.append(c["opportunity"])
        rows.append({
            "brand": brand, "products": int(len(g)), "listings": int(g["listing_count"].sum()) if "listing_count" in g else None,
            "monthly_revenue": float(g["monthly_revenue"].sum()) if g["monthly_revenue"].notna().any() else None,
            "monthly_sales": float(g["monthly_sales"].sum()) if g["monthly_sales"].notna().any() else None,
            "share": round(share, 4), "share_basis": basis, "median_price": float(g["price"].median()) if g["price"].notna().any() else None,
            "price_index": None if pidx is None else round(pidx, 3), "avg_rating": None if rating is None else round(rating, 2),
            "segments": int(g["segment_id"].nunique()),
            "top_segment": seg_lab.get(top_seg.index[0], top_seg.index[0]) if len(top_seg) else None,
            "new_listings_12m": recent,
            "weaknesses": json.dumps(weak), "strengths": json.dumps(strong), "opportunities": json.dumps(list(dict.fromkeys(opp))),
            "complaints": json.dumps(complaints[:5]), "changes": json.dumps(changes.get(brand, {})),
        })
    out = pd.DataFrame(rows).sort_values("share", ascending=False).reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    out["position"] = [_position(r, s) for r, s in zip(out["rank"], out["share"])]
    return out


def brand_changes(history: pd.DataFrame, brand_of_listing: dict) -> dict:
    """Per brand: change between the last two periods of the listing history."""
    if history is None or history.empty or history["period"].nunique() < 2:
        return {}
    c = config()["changes"]
    h = history.assign(brand=history["brand"].fillna(history["id"].map(brand_of_listing)).fillna("").astype(str).str.strip())
    h = h[h["brand"] != ""]
    per = sorted(h["period"].unique())
    prev, last = per[-2], per[-1]
    a, z = h[h["period"] == prev], h[h["period"] == last]
    rev = lambda d: d["revenue"].where(d["revenue"].notna(), d["price"] * d["sales"]).fillna(0)  # noqa: E731
    sa = rev(a).groupby(a["brand"]).sum()
    sz = rev(z).groupby(z["brand"]).sum()
    ta, tz = sa.sum() or 1.0, sz.sum() or 1.0
    out = {}
    for brand in set(sa.index) | set(sz.index):
        ch: dict = {"from": str(pd.Timestamp(prev).date()), "to": str(pd.Timestamp(last).date())}
        ds = float(sz.get(brand, 0) / tz - sa.get(brand, 0) / ta)
        if abs(ds) >= c["share_change"]:
            ch["share_change"] = round(ds, 4)
        ia, iz = set(a.loc[a["brand"] == brand, "id"]), set(z.loc[z["brand"] == brand, "id"])
        if iz - ia:
            ch["new_listings"] = sorted(iz - ia)[:20]
        if ia - iz:
            ch["dropped_listings"] = sorted(ia - iz)[:20]
        both = sorted(ia & iz)
        if both:
            pa = a.set_index("id").loc[both]
            pz = z.set_index("id").loc[both]
            pr = (pz["price"].astype(float) / pa["price"].astype(float) - 1).dropna()
            if len(pr) and abs(pr.median()) >= c["price_change"]:
                ch["price_change"] = round(float(pr.median()), 4)
            ra, rz = pa["rating"].astype(float).mean(), pz["rating"].astype(float).mean()
            if pd.notna(ra) and pd.notna(rz) and abs(rz - ra) >= c["rating_change"]:
                ch["rating_change"] = round(float(rz - ra), 2)
            va, vz = pd.to_numeric(pa["reviews"], errors="coerce").sum(), pd.to_numeric(pz["reviews"], errors="coerce").sum()
            if va > 0 and vz > va:
                ch["new_reviews"] = int(vz - va)
        if len(ch) > 2:
            out[brand] = ch
    return out
