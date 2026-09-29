"""Metrics engine v3: every market metric from the interval-censored demand model.

Input: the market's listings (accepted, relevant, grouped into products and segments).
Output (``MetricsOutput``):
* listing, product and segment columns (units / revenue estimate, interval, floor, ceiling,
  unit economics, opportunity components)
* a long-form ``metrics`` table: one row per (scope, scope_id, metric) with value, low, high, n,
  unit, formula id (docs/METHODOLOGY.md), basis and caveat
* a market summary

All intervals come from joint simulation over the demand model (parameter + within-interval
uncertainty). Constants live in config/platform/metrics.yaml. Nothing is category-specific.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dip.metrics import brands as brands_mod
from dip.metrics import config
from dip.metrics.demand import DemandResult, crossvalidate, estimate
from dip.metrics.integrity import validation_issues
from dip.metrics.observation import observe


def _num(v) -> float | None:
    try:
        f = float(str(v).replace(",", "").replace("$", "").strip())
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _attr(attrs, fields: list[str]) -> float | None:
    if isinstance(attrs, str):
        import json
        try:
            attrs = json.loads(attrs)
        except ValueError:
            attrs = {}
    for f in fields:
        v = _num((attrs or {}).get(f))
        if v is not None:
            return v
    return None


def _ci(draws: np.ndarray, level: float) -> tuple[float, float]:
    a = (1 - level) / 2
    lo, hi = np.nanpercentile(draws, [100 * a, 100 * (1 - a)], axis=0)
    return lo, hi


def _clip01(x):
    return float(np.clip(x, 0.0, 1.0))


def transform(value: float | None, spec: dict) -> float | None:
    """Absolute component transform from config: linear (or log10) between x0 -> 0 and x1 -> 1."""
    if value is None or not np.isfinite(value):
        return None
    v = np.log10(max(value, 1e-9)) if spec.get("scale") == "log10" else value
    x0, x1 = spec["x0"], spec["x1"]
    y0, y1 = spec.get("y0", 0.0), spec.get("y1", 1.0)
    return y0 + (y1 - y0) * _clip01((v - x0) / (x1 - x0))


def geo_index(parts: dict[str, float | None], weights: dict[str, float],
              missing: float | None = None) -> tuple[float | None, float]:
    """Weighted geometric mean of components (0..1 -> 0..100) and the weight coverage (share of the weight
    carried by measured components).

    ``missing=None``: missing components are left out (the index is then over fewer components).
    ``missing=x``: a missing component counts as ``x`` (e.g. neutral 0.5), so indices with different coverage
    are on the same scale -- a segment is neither rewarded nor punished for evidence the source lacks.
    Nothing is imputed when no component is measured."""
    measured = {k: v for k, v in parts.items() if v is not None}
    if not measured:
        return None, 0.0
    coverage = round(float(sum(weights[k] for k in measured) / sum(weights.values())), 3)
    ok = measured if missing is None else {k: (missing if v is None else v) for k, v in parts.items()}
    w = np.array([weights[k] for k in ok])
    v = np.array([max(ok[k], 0.01) for k in ok])                # floor keeps one zero from erasing everything
    idx = float(np.exp(np.sum(w * np.log(v)) / w.sum()) * 100)
    return round(idx, 1), coverage


@dataclass
class MetricsOutput:
    listings: pd.DataFrame
    products: pd.DataFrame
    segments: pd.DataFrame
    metrics: pd.DataFrame
    summary: dict
    demand: DemandResult | None = field(default=None, repr=False)
    brands: pd.DataFrame | None = None          # competitor table (brands.table)
    cohorts: pd.DataFrame | None = None         # launch cohorts (brands.cohorts)
    momentum: pd.DataFrame | None = None        # launch momentum tests (brands.momentum)
    segment_brands: pd.DataFrame | None = None  # top brands per segment with share intervals
    draws: JointDraws | None = field(default=None, repr=False)   # product-level joint draws (any set's interval)

    def interval_for(self, scope: str, scope_id: str, product_ids) -> dict | None:
        """Revenue and units (estimate, low, high) for a set of products -- the one interval the platform shows.
        The market and segments return their stored metric rows; any other set (taxonomy node, filter) sums the
        same joint draws the market interval came from, so every scope's interval is consistent with it."""
        if scope in ("category", "market", "segment"):
            sid = "" if scope != "segment" else str(scope_id)
            sc = "segment" if scope == "segment" else "market"
            m = self.metrics
            out = {}
            for key, metric in (("revenue", "revenue_month"), ("units", "units_month")):
                r = m[(m["scope"] == sc) & (m["scope_id"].astype(str) == sid) & (m["metric"] == metric)]
                if len(r):
                    out[key] = (float(r["value"].iat[0]), _nf(r["low"].iat[0]), _nf(r["high"].iat[0]))
            if "revenue" in out:
                return out
        return None if self.draws is None else self.draws.interval(product_ids)


def _undecided(L: pd.DataFrame, dem: DemandResult, price: np.ndarray) -> dict | None:
    """How much of the market's revenue sits in sub-categories whose category scope a person has not decided yet
    (scope_status 'review'). They stay in every total -- dropping them before anyone decides would be a silent
    exclusion -- and this block says how much they carry, so the size is read with that uncertainty."""
    if "scope_status" not in L:
        return None
    und = (L["scope_status"].astype(str) == "review").to_numpy()
    p = np.nan_to_num(price)
    floor_all = float((dem.floor * p).sum())
    est_all = float((dem.units_est * p).sum())
    f, e = float((dem.floor * p)[und].sum()), float((dem.units_est * p)[und].sum())
    return {"listings": int(und.sum()), "floor": round(f, 2), "estimate": round(e, 2),
            "floor_share": round(f / floor_all, 4) if floor_all > 0 else None,
            "estimate_share": round(e / est_all, 4) if est_all > 0 else None,
            "decided_floor": round(floor_all - f, 2)}


def _nf(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


@dataclass
class JointDraws:
    """Joint simulation draws per product (float32, sims x products), kept after compute so any set of products
    gets an interval from the same simulation as the market (never an independence approximation)."""
    product_ids: np.ndarray
    revenue: np.ndarray
    units: np.ndarray
    level: float

    def interval(self, product_ids) -> dict | None:
        pos = {p: i for i, p in enumerate(self.product_ids)}
        ids = [str(p) for p in product_ids]
        if not ids or any(p not in pos for p in ids):        # a product the engine did not simulate: no partial sums
            return None
        idx = [pos[p] for p in ids]
        out = {}
        for key, arr in (("revenue", self.revenue), ("units", self.units)):
            d = arr[:, idx].astype(np.float64).sum(axis=1)
            lo, hi = _ci(d, self.level)
            out[key] = (float(d.mean()), float(lo), float(hi))
        return out


class _Rows:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, scope, scope_id, metric, value, low=None, high=None, n=None, unit="", formula="", basis="", caveat=""):
        def r(x):
            return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else (round(float(x), 6))
        self.rows.append({"scope": scope, "scope_id": str(scope_id), "metric": metric, "value": r(value), "low": r(low),
                          "high": r(high), "n": None if n is None else int(n), "unit": unit, "formula": formula,
                          "basis": basis, "caveat": caveat})


def compute(listings: pd.DataFrame, products: pd.DataFrame, segments: pd.DataFrame, as_of=None,
            snapshots: int = 1, pain: dict | None = None, sales_kind: str | None = None,
            as_of_basis: str | None = None) -> MetricsOutput:
    """``sales_kind``: how the source observed sales ("badge" / "exact"), detected on the whole export --
    the observation process is a property of the source, not of the in-scope subset."""
    cfg = config()
    lvl = cfg["engine"]["interval"]
    L = listings.reset_index(drop=True).copy()
    P = products.copy()
    S = segments.copy()
    rows = _Rows()
    if L.empty:
        return MetricsOutput(L, P, S, pd.DataFrame(rows.rows), {"status": "no_listings"})
    if as_of is None:
        ld = pd.to_datetime(L.get("launch_date"), errors="coerce")
        as_of = ld.max() + pd.Timedelta(days=1) if ld.notna().any() else pd.Timestamp.today().normalize()
        as_of_basis = "latest launch date in the data (no snapshot date declared)"
    else:
        as_of_basis = as_of_basis or "snapshot date"
    as_of = pd.Timestamp(as_of)

    # ---------------------------------------------------------------- 1. demand
    obs = observe(L["sales"], sales_kind)
    dem = estimate(L, obs, as_of)
    price = pd.to_numeric(L["price"], errors="coerce").to_numpy(dtype=float)
    L["units_floor"] = dem.floor
    L["units_ceiling"] = dem.ceiling
    L["units_est"] = np.round(dem.units_est, 2)
    L["units_lo"] = np.round(dem.units_lo, 2)
    L["units_hi"] = np.round(dem.units_hi, 2)
    L["revenue_est"] = np.round(dem.units_est * np.nan_to_num(price), 2)
    L["sales_observation"] = np.where(obs.known, "badge" if obs.kind == "badge" else "exact",
                                      "below first badge" if obs.kind == "badge" else "unknown")
    cv = None
    if cfg["engine"].get("crossvalidate", True) and len(L) >= 30:
        sub = L
        if len(L) > cfg["engine"]["crossvalidate_max_rows"]:
            sub = L.sample(cfg["engine"]["crossvalidate_max_rows"], random_state=0)
        try:
            cv = crossvalidate(sub, observe(sub["sales"], obs.kind), as_of)
        except Exception as exc:  # validation must never break processing
            cv = {"error": str(exc)}

    model_issues = validation_issues(cv, int(obs.known.sum()))
    headline_basis = ("observed: sum of sales-badge floors (certain minimum)" if obs.kind == "badge"
                      else "observed: sum of reported sales (listings without sales count as 0)")

    # ---------------------------------------------------------------- 2. joint simulation of groups
    prod_codes, prod_ids = pd.factorize(L["product_id"].astype(str))
    seg_codes, seg_ids = pd.factorize(L["segment_id"].astype(str))
    brand = L["brand"].fillna("(no brand)").astype(str)
    br_codes, br_ids = pd.factorize(brand)
    pair_codes, pair_idx = pd.factorize(pd.Series(seg_codes).astype(str) + "\x1f" + pd.Series(br_codes).astype(str))
    pair_seg = np.array([int(k.split("\x1f")[0]) for k in pair_idx])
    pair_br = np.array([int(k.split("\x1f")[1]) for k in pair_idx])
    groups = {"market": np.zeros(len(L), dtype=int), "segment": seg_codes, "product": prod_codes, "brand": br_codes, "pair": pair_codes}
    sims = cfg["engine"]["simulations"]
    rev = dem.simulate(groups, weights=price, sims=sims)
    units = dem.simulate({"market": groups["market"], "segment": seg_codes, "product": prod_codes}, sims=sims)
    floor_rev = pd.Series(dem.floor * np.nan_to_num(price)).groupby(seg_codes).sum()

    # market size
    m_draw = rev["market"][:, 0]
    m_lo, m_hi = _ci(m_draw, lvl)
    floor_total = float((dem.floor * np.nan_to_num(price)).sum())
    ceil_total = float(np.nansum(np.where(np.isfinite(dem.ceiling), dem.ceiling, np.nan) * price)) if np.isfinite(dem.ceiling).all() else None
    caveat_size = ("badge data: listings without a badge sell < first rung; estimate from the interval-censored "
                   "demand model" if obs.kind == "badge" else "exact sales where present; missing sales modelled")
    rows.add("market", "", "revenue_month", float(m_draw.mean()), m_lo, m_hi, len(L), "USD/month", "M1.4",
             "estimate (joint simulation)", caveat_size)
    rows.add("market", "", "revenue_month_floor", floor_total, n=len(L), unit="USD/month", formula="M1.4",
             basis="sum of observation lower bounds (certain)")
    if ceil_total is not None:
        rows.add("market", "", "revenue_month_ceiling", ceil_total, n=len(L), unit="USD/month", formula="M1.4",
                 basis="sum of observation upper bounds")
    u_draw = units["market"][:, 0]
    ul, uh = _ci(u_draw, lvl)
    rows.add("market", "", "units_month", float(u_draw.mean()), ul, uh, len(L), "units/month", "M1.4", "estimate")
    units_floor = float(np.nansum(dem.floor))
    rows.add("market", "", "units_month_floor", units_floor, n=len(L), unit="units/month", formula="M1.4",
             basis="sum of observation lower bounds (certain)")

    # products
    p_rev = rev["product"]
    p_units = units["product"]
    plo, phi = _ci(p_rev, lvl)
    ulo, uhi = _ci(p_units, lvl)
    pdf = pd.DataFrame({"product_id": prod_ids, "revenue_est": p_rev.mean(0), "revenue_lo": plo, "revenue_hi": phi,
                        "units_est": p_units.mean(0), "units_lo": ulo, "units_hi": uhi})
    pfl = L.groupby("product_id").agg(units_floor=("units_floor", "sum"), units_ceiling=("units_ceiling", "sum"))
    pdf = pdf.merge(pfl, left_on="product_id", right_index=True, how="left")

    # ---------------------------------------------------------------- 3. unit economics (per listing)
    ue = cfg["unit_economics"]
    cost = L["attributes"].map(lambda a: _attr(a, ue["unit_cost_fields"])) if "attributes" in L else pd.Series(np.nan, index=L.index)
    fee_f = L["attributes"].map(lambda a: _attr(a, ue["fulfilment_fee_fields"])) if "attributes" in L else pd.Series(np.nan, index=L.index)
    cost = pd.to_numeric(cost, errors="coerce")
    fee_f = pd.to_numeric(fee_f, errors="coerce")
    valid_cost = cost.notna() & (cost > 0) & (cost < price)
    ratio = ((cost + fee_f.fillna(0)) / price)[valid_cost]
    derived = bool(len(ratio) >= 20 and ratio.std() / max(ratio.mean(), 1e-9) < ue["derived_cost_max_cv"])
    cost_note = None
    if derived:
        cost_note = (f"the cost field is computed from price ((cost + fulfilment fee) / price = {ratio.mean():.3f} "
                     f"± {ratio.std():.3f} for {len(ratio)} listings): it is a target cost ceiling, not an observed cost, "
                     "so it is not used for margins")
        L["cost_ceiling"] = cost.where(valid_cost)
        valid_cost = pd.Series(False, index=L.index)
    L["unit_cost"] = cost.where(valid_cost)
    L["unit_cost_issue"] = np.where(cost.isna(), "not in source",
                                    np.where(valid_cost, None, "price-derived cost ceiling -- not a cost" if derived
                                             else "invalid (<= 0 or >= price) -- quarantined"))
    L["fulfilment_fee"] = fee_f.where(fee_f >= 0)
    L["unit_margin"] = (price * (1 - ue["referral_fee"]) - L["fulfilment_fee"].fillna(0) - L["unit_cost"]).round(2)
    L["margin_rate"] = (L["unit_margin"] / price).round(4)

    # ---------------------------------------------------------------- 4. entrants
    ec = cfg["entry"]
    ld = pd.to_datetime(L.get("launch_date"), errors="coerce")
    age_days = (as_of - ld).dt.days
    L["age_days"] = age_days
    entrant = (age_days >= 0) & (age_days <= ec["entrant_window_days"])
    established = age_days > ec["entrant_window_days"]
    L["is_entrant"] = entrant.fillna(False)
    # listing-level unit draws for entrant success (re-simulated; memory is n x sims only for these rows)
    rng = np.random.default_rng(config()["demand_model"]["seed"] + 11)
    thetas = dem.fit.boot or [(dem.fit.beta, dem.fit.sigma, dem.fit.df)]
    from dip.metrics.demand import Dist, draw
    ent_idx = np.where(entrant.to_numpy())[0]
    est_idx = np.where(established.to_numpy())[0]
    ent_draws = np.zeros((sims, len(ent_idx)))
    est_draws = np.zeros((sims, len(est_idx)))
    if len(ent_idx) or len(est_idx):
        for i in range(sims):
            b, sg, df = thetas[i % len(thetas)]
            x = draw(dem.observation, dem.X @ b, sg, rng, Dist(df))
            ent_draws[i] = x[ent_idx]
            est_draws[i] = x[est_idx]

    # ---------------------------------------------------------------- 5. per-segment metrics
    q = cfg["quality"]
    seg_rev = rev["segment"]
    seg_units = units["segment"]
    pair_rev = rev["pair"]
    op = cfg["opportunity"]
    rows_seg = []
    ent_seg = seg_codes[ent_idx]
    est_seg = seg_codes[est_idx]
    # market-level entrant success (prior for shrinkage)
    if len(ent_idx) and len(est_idx):
        med_est_all = np.median(est_draws, axis=1, keepdims=True)
        mkt_rate_draw = (ent_draws >= med_est_all).mean(axis=1)
        mkt_rate = float(mkt_rate_draw.mean())
        lo_, hi_ = _ci(mkt_rate_draw, lvl)
        rows.add("market", "", "entrant_success_rate", mkt_rate, lo_, hi_, len(ent_idx), "share", "M3.2",
                 f"entrants (launched <= {ec['entrant_window_days']} days before {as_of.date()}, {as_of_basis}) reaching the median "
                 "estimated units of established listings")
    else:
        mkt_rate = None
    seg_brand_rows: list[dict] = []
    seg_tot_draw = seg_rev.sum(axis=1)
    for si, sid in enumerate(seg_ids):
        mask = seg_codes == si
        n = int(mask.sum())
        d = seg_rev[:, si]
        lo, hi = _ci(d, lvl)
        est = float(d.mean())
        rows.add("segment", sid, "revenue_month", est, lo, hi, n, "USD/month", "M1.4", "estimate")
        rows.add("segment", sid, "revenue_month_floor", float(floor_rev.get(si, 0.0)), n=n, unit="USD/month", formula="M1.4",
                 basis="certain lower bound")
        share_d = d / np.maximum(seg_tot_draw, 1e-9)       # per draw over all segments: shares sum to 1 exactly
        sl, sh = _ci(share_d, lvl)
        rows.add("segment", sid, "revenue_share", float(share_d.mean()), sl, sh, n, "share", "M1.4")
        # concentration per draw
        pr = pair_rev[:, pair_seg == si]
        tot = pr.sum(axis=1, keepdims=True)
        shares = pr / np.maximum(tot, 1e-9)
        hhi_d = (shares ** 2).sum(axis=1) * 10000
        hl, hh = _ci(hhi_d, lvl)
        hhi = float(hhi_d.mean())
        brands_here = pair_br[pair_seg == si]
        top_share_d = shares.max(axis=1)
        top_brand = br_ids[brands_here[np.argmax(pr.mean(0))]] if pr.shape[1] else None
        top3_d = np.sort(shares, axis=1)[:, -3:].sum(axis=1)
        rows.add("segment", sid, "hhi", hhi, hl, hh, n, "HHI 0-10,000", "M2.1", "brand shares of estimated revenue")
        rows.add("segment", sid, "top_brand_share", float(top_share_d.mean()), *_ci(top_share_d, lvl), n, "share", "M2.1",
                 basis=f"top brand: {top_brand}")
        rows.add("segment", sid, "top3_share", float(top3_d.mean()), *_ci(top3_d, lvl), n, "share", "M2.1")
        live = tot[:, 0] > 1e-9                       # draws where the segment sells nothing define no shares
        if pr.shape[1] and live.any():
            sv = shares[live]
            s_lo, s_hi = _ci(sv, lvl)
            for j in np.argsort(-sv.mean(0))[:5]:
                se = float(sv[:, j].mean())                # bounds clamped: exact data must not round outside
                seg_brand_rows.append({"segment_id": sid, "brand": br_ids[brands_here[j]], "share_est": se,
                                       "share_lo": min(float(s_lo[j]), se), "share_hi": max(float(s_hi[j]), se), "revenue_est": float(pr[:, j].mean()),
                                       "p_top": float((sv.argmax(axis=1) == j).mean()), "draws": int(live.sum())})
        rows.add("segment", sid, "brands", int(len(brands_here)), n=n, unit="brands", formula="M2.1")
        rows.add("segment", sid, "effective_competitors", 10000 / hhi if hhi else None, n=n, unit="brands", formula="M2.1",
                 basis="1 / HHI")
        # entrants
        e_here = ent_draws[:, ent_seg == si]
        s_here = est_draws[:, est_seg == si]
        n_ent = int(e_here.shape[1])
        rate = rate_lo = rate_hi = None
        if s_here.shape[1] >= 3 and n_ent:
            med = np.median(s_here, axis=1, keepdims=True)
            rd = (e_here >= med).mean(axis=1)
            k = ec["prior_strength"] if n_ent < ec["min_entrants"] and mkt_rate is not None else 0
            rd = (rd * n_ent + (mkt_rate or 0) * k) / (n_ent + k)                # empirical-Bayes shrinkage
            rate, (rate_lo, rate_hi) = float(rd.mean()), _ci(rd, lvl)
        elif mkt_rate is not None and n_ent == 0:
            rate = mkt_rate
        ent_rev_share = None
        if n_ent:
            erev = (e_here * price[ent_idx][ent_seg == si]).sum(axis=1) / np.maximum(d, 1e-9)
            ent_rev_share = float(erev.mean())
            rows.add("segment", sid, "entrant_revenue_share", ent_rev_share, *_ci(erev, lvl), n_ent, "share", "M3.1",
                     f"listings launched within {ec['entrant_window_days']} days")
        rows.add("segment", sid, "entrants", n_ent, n=n, unit="listings", formula="M3.1")
        rows.add("segment", sid, "entrant_success_rate", rate, rate_lo, rate_hi, n_ent, "share", "M3.2",
                 "segment entrants" + (" shrunk toward the market rate" if n_ent < ec["min_entrants"] else "")
                 if n_ent else "no entrants in the segment: market entrant rate used",
                 "" if n_ent >= ec["min_entrants"] else "few entrants -- wide uncertainty")
        # unit economics
        mg = L.loc[mask, "margin_rate"].dropna()
        um = L.loc[mask, "unit_margin"].dropna()
        margin_rate = float(mg.median()) if len(mg) else None
        rows.add("segment", sid, "margin_rate_median", margin_rate, n=len(mg), unit="share of price", formula="M4.1",
                 basis=f"price x (1 - {ue['referral_fee']:.0%}) - fulfilment fee - unit cost",
                 caveat="" if len(mg) else "no valid unit cost in the source")
        rows.add("segment", sid, "unit_margin_median", float(um.median()) if len(um) else None, n=len(um), unit="USD/unit", formula="M4.1")
        rows.add("segment", sid, "profitable_share", float((um > 0).mean()) if len(um) else None, n=len(um), unit="share", formula="M4.1")
        # quality gap
        rt = pd.to_numeric(L.loc[mask, "rating"], errors="coerce")
        w = dem.units_est[mask]
        okr = rt.notna().to_numpy()
        qgap = float(np.average((rt[okr] < q["threshold"]).to_numpy(), weights=w[okr] + 1e-9)) if okr.any() else None
        rows.add("segment", sid, "quality_gap_share", qgap, n=int(okr.sum()), unit="share of demand", formula="M5.1",
                 basis=f"estimated units sold by listings rated < {q['threshold']}",
                 caveat="rating-based proxy; review text not in source" if not (pain or {}).get(sid) else "")
        rev_l = dem.units_est[mask] * np.nan_to_num(price[mask])
        top = np.argsort(-rev_l)[: max(1, int(np.ceil(n * q["rating_bar_top_share"])))]
        top_r = rt.to_numpy()[top]
        bar = float(np.nanmedian(top_r)) if np.isfinite(top_r).any() else None
        rows.add("segment", sid, "rating_bar", bar, n=len(top), unit="stars", formula="M5.2",
                 basis=f"median rating of the top {q['rating_bar_top_share']:.0%} listings by estimated revenue")
        # saturation
        unb = float((~obs.known[mask]).mean()) if obs.kind == "badge" else None
        su = seg_units[:, si]
        lpk = n / max(float(su.mean()), 1e-9) * 1000
        rows.add("segment", sid, "unbadged_share", unb, n=n, unit="share of listings", formula="M6.1",
                 basis="listings selling below the first badge rung")
        rows.add("segment", sid, "listings_per_1k_units", lpk, n=n, unit="listings", formula="M6.1")
        # opportunity: expected entrant profit
        exp_units = exp_lo = exp_hi = profit = p_lo = p_hi = p_pos = None
        entrant_value_kind = None
        pool = e_here if n_ent >= 1 else None
        if pool is None or pool.shape[1] < ec["min_entrants"]:
            extra = ent_draws if ent_draws.shape[1] else None
            pool = extra if pool is None else (np.hstack([pool, extra]) if extra is not None else pool)
        basis_e = "segment and market entrants" if n_ent and n_ent < ec["min_entrants"] else "segment entrants"
        if pool is None or pool.shape[1] == 0:
            if s_here.shape[1]:
                pool = np.percentile(s_here, 25, axis=1, keepdims=True)
                basis_e = "no entrants in data: lower quartile of established listings"
        if pool is not None and pool.shape[1]:
            eu = pool.mean(axis=1)
            exp_units, (exp_lo, exp_hi) = float(eu.mean()), _ci(eu, lvl)
            rows.add("segment", sid, "entrant_units_expected", exp_units, exp_lo, exp_hi, pool.shape[1], "units/month", "M7.1", basis_e)
            rng2 = np.random.default_rng(len(rows.rows))
            seg_prices = price[mask][np.isfinite(price[mask])]
            if len(um):
                msamp = rng2.choice(um.to_numpy(), size=(sims, pool.shape[1]))
                val, target, what = pool * msamp, ue["target_monthly_profit"], "profit"
                basis_v = f"entrant units ({basis_e}) x unit margins of the segment's listings"
            elif len(seg_prices):
                psamp = rng2.choice(seg_prices, size=(sims, pool.shape[1]))
                val, target, what = pool * psamp, ue["target_monthly_revenue"], "revenue"
                basis_v = f"entrant units ({basis_e}) x prices of the segment's listings (no genuine unit costs in source)"
            else:
                val = None
            if val is not None:
                mean_v = val.mean(axis=1)
                profit, (p_lo, p_hi) = float(mean_v.mean()), _ci(mean_v, lvl)
                p_pos = float((val >= target).mean())
                rows.add("segment", sid, f"entrant_{what}_expected", profit, p_lo, p_hi, pool.shape[1], "USD/month", "M7.1", basis_v)
                rows.add("segment", sid, f"entrant_{what}_target_prob", p_pos, n=pool.shape[1], unit="probability", formula="M7.1",
                         basis=f"share of simulated entrants reaching {target:,} USD/month {what}")
                entrant_value_kind = what
        # index
        comps = {}
        vals = {"segment_revenue_est": est, "entrant_success_rate": rate, "margin_rate_median": margin_rate, "hhi": hhi,
                "quality_gap_share": qgap, "unbadged_share": unb}
        for name, spec in op["components"].items():
            comps[name] = transform(vals.get(spec["metric"]), spec)
        neutral = op.get("missing_component_score", 0.5)
        idx, coverage = geo_index(comps, {k: v["weight"] for k, v in op["components"].items()}, missing=neutral)
        for name, v in comps.items():
            rows.add("segment", sid, f"opp_{name}", v, n=n, unit="0-1", formula="M7.2",
                     basis=f"{op['components'][name]['metric']} -> 0..1 (x0={op['components'][name]['x0']}, x1={op['components'][name]['x1']})",
                     caveat="" if v is not None else f"not measurable from the source: counted as neutral {neutral} in the index")
        n_meas = sum(v is not None for v in comps.values())
        rows.add("segment", sid, "opportunity_index", idx, n=n, unit="0-100", formula="M7.2",
                 basis=f"weighted geometric mean of {len(comps)} components, {n_meas} measured (coverage {coverage:.0%})"
                 + (f"; unmeasured components counted as neutral {neutral}" if n_meas < len(comps) else ""))
        rows_seg.append({"segment_id": sid, "listings_v3": n, "revenue_est": round(est, 2), "revenue_lo": round(float(lo), 2),
                         "revenue_hi": round(float(hi), 2), "revenue_floor": round(float(floor_rev.get(si, 0.0)), 2),
                         "units_est": round(float(su.mean()), 2), "hhi_est": round(hhi, 1), "top_brand_est": top_brand,
                         "entrants": n_ent, "entrant_success_rate": rate, "entrant_revenue_share": ent_rev_share,
                         "margin_rate_median": margin_rate, "quality_gap_share": qgap, "rating_bar": bar,
                         "unbadged_share": unb, "listings_per_1k_units": round(lpk, 2),
                         "entrant_units_expected": exp_units, "entrant_value_kind": entrant_value_kind,
                         "entrant_value_expected": profit, "entrant_value_lo": p_lo, "entrant_value_hi": p_hi,
                         "entrant_target_prob": p_pos,
                         "opportunity_index": idx, "opportunity_coverage": coverage,
                         **{f"opp_{k}": (None if v is None else round(v, 3)) for k, v in comps.items()}})
    seg_v3 = pd.DataFrame(rows_seg)
    lv = op["levels"]

    def level(x):
        return None if x is None or pd.isna(x) else next(lbl for bound, lbl in lv if x < bound)

    if len(S):
        S = S.drop(columns=[c for c in seg_v3.columns if c != "segment_id" and c in S.columns], errors="ignore")
        S = S.merge(seg_v3, on="segment_id", how="left")
        if "opportunity_score" in S:
            S["opportunity_v2"] = S["opportunity_score"]
        S["opportunity_score"] = S["opportunity_index"]
        S["opportunity_level"] = S["opportunity_index"].map(level)

    # ---------------------------------------------------------------- 6. products
    bands = cfg["price_bands"]
    L["price_band"] = L.groupby("segment_id")["price"].transform(
        lambda s: pd.qcut(s.rank(method="first"), min(bands, max(1, s.notna().sum())), labels=False, duplicates="drop")
        if s.notna().sum() >= bands else pd.Series(0, index=s.index))
    band_dpl = L.groupby(["segment_id", "price_band"])["units_est"].mean()
    seg_dpl = L.groupby("segment_id")["units_est"].mean()
    lift = (band_dpl / band_dpl.index.get_level_values(0).map(seg_dpl).to_numpy())
    L["price_band_lift"] = [lift.get((s, b), np.nan) for s, b in zip(L["segment_id"], L["price_band"])]
    pw = op["product"]
    seg_idx = S.set_index("segment_id")["opportunity_index"] if len(S) else pd.Series(dtype=float)
    seg_bar = S.set_index("segment_id")["rating_bar"] if len(S) else pd.Series(dtype=float)
    prod_rows = L.groupby("product_id").agg(segment_id=("segment_id", "first"), rating=("rating", "mean"),
                                           band_lift=("price_band_lift", "mean"), unit_margin=("unit_margin", "median"),
                                           margin_rate=("margin_rate", "median"), unit_cost=("unit_cost", "median"),
                                           is_entrant=("is_entrant", "max"))
    out_idx = []
    for pid, r in prod_rows.iterrows():
        s_i = seg_idx.get(r["segment_id"])
        band = None if pd.isna(r["band_lift"]) else _clip01((np.log2(max(r["band_lift"], 1e-6)) + 1) / 2)
        room = None
        if pd.notna(r["rating"]) and pd.notna(seg_bar.get(r["segment_id"])):
            room = _clip01(seg_bar.get(r["segment_id"]) - r["rating"])
        parts = {"segment": None if s_i is None or pd.isna(s_i) else s_i / 100, "price_band": band, "quality_room": room}
        idx, cov = geo_index(parts, {"segment": pw["segment_weight"], "price_band": pw["price_band_weight"],
                                     "quality_room": pw["quality_room_weight"]}, missing=op.get("missing_component_score", 0.5))
        out_idx.append({"product_id": pid, "opportunity_index": idx, "opp_segment": parts["segment"],
                        "opp_price_band": band, "opp_quality_room": room, "unit_margin": r["unit_margin"],
                        "margin_rate": r["margin_rate"], "unit_cost": r["unit_cost"], "is_entrant": bool(r["is_entrant"])})
    pidx = pd.DataFrame(out_idx)
    pdf = pdf.merge(pidx, on="product_id", how="left")
    if len(P):
        P = P.drop(columns=[c for c in pdf.columns if c != "product_id" and c in P.columns], errors="ignore")
        P = P.merge(pdf, on="product_id", how="left")
        if "opportunity_score" in P:
            P["opportunity_v2"] = P["opportunity_score"]
        P["opportunity_score"] = P["opportunity_index"]

    # ---------------------------------------------------------------- 7. market summary + evidence grade
    auc = (cv or {}).get("auc_badged_vs_unbadged")
    badged_share = float(obs.known.mean())
    eg = cfg["evidence_grade"]
    auc = None if auc is None or not np.isfinite(auc) else float(auc)
    grade = "D"
    for g in ("A", "B", "C"):
        c = eg[g]
        if len(L) >= c["n"] and badged_share >= c.get("badged_share", 0) and (c.get("auc") is None or (auc or 0) >= c["auc"]):
            grade = g
            break
    grade_basis = f"{len(L)} listings, {badged_share:.0%} badged, hold-out AUC {'n/a' if auc is None else f'{auc:.2f}'}"
    grade_reason = f"{grade}: {grade_basis}"
    # a measured hold-out AUC below min_auc_any (the demand model failed validation) caps any grade at D
    if grade != "D" and eg.get("min_auc_any") is not None and auc is not None and auc < eg["min_auc_any"]:
        grade = "D"
        grade_reason = f"capped at D: hold-out AUC {auc:.2f} is below {eg['min_auc_any']:.2f}; {grade_basis}"
    hhi_m_d = None
    br = rev["brand"]
    mshares = br / np.maximum(br.sum(axis=1, keepdims=True), 1e-9)
    hhi_m_d = (mshares ** 2).sum(axis=1) * 10000
    rows.add("market", "", "hhi", float(hhi_m_d.mean()), *_ci(hhi_m_d, lvl), len(L), "HHI 0-10,000", "M2.1")
    brand_tab = pd.DataFrame({"brand": br_ids, "revenue_est": br.mean(0), "share_est": mshares.mean(0)})
    bl, bh = _ci(mshares, lvl)
    brand_tab["share_lo"], brand_tab["share_hi"] = bl, bh
    brand_tab = brand_tab.sort_values("revenue_est", ascending=False)
    for _, b in brand_tab.head(50).iterrows():
        rows.add("brand", b["brand"], "revenue_share", b["share_est"], b["share_lo"], b["share_hi"], None, "share", "M2.1")
    summary = {
        "status": "ok", "as_of": str(as_of.date()), "as_of_basis": as_of_basis, "snapshots": snapshots,
        "sales_observation": obs.evidence, "badged_share": round(badged_share, 4),
        "demand_model": {"family": dem.fit.dist.name, "sigma": round(dem.fit.sigma, 4), "n": dem.fit.n,
                         "n_bounded": dem.fit.n_bounded, "family_selection": dem.fit.family_selection,
                         "coefficients": dem.coefficients(), "price_association": dem.price_elasticity(),
                         "bias_correction": dem.fit.bias_correction, "crossvalidation": cv},
        # headline = what the data certainly shows; the modelled estimate is shown beside it, flagged by whether
        # the demand model passed hold-out validation (thresholds: integrity in metrics.yaml)
        "revenue_month": {"headline": round(floor_total, 2), "headline_basis": headline_basis,
                          "estimate": round(float(m_draw.mean()), 2), "low": round(float(m_lo), 2), "high": round(float(m_hi), 2),
                          "floor": round(floor_total, 2), "ceiling": None if ceil_total is None else round(ceil_total, 2),
                          "model_validated": not model_issues, "model_issues": model_issues,
                          "undecided": _undecided(L, dem, price)},
        "units_month": {"headline": round(units_floor, 1), "floor": round(units_floor, 1),
                        "estimate": round(float(u_draw.mean()), 1), "low": round(float(ul), 1), "high": round(float(uh), 1)},
        "hhi": round(float(hhi_m_d.mean()), 1), "entrant_success_rate": mkt_rate, "evidence_grade": grade,
        "evidence_grade_reason": grade_reason,
        "unit_cost": {"derived_field": derived, "note": cost_note,
                      "valid_listings": int(L["unit_cost"].notna().sum())},
        "top_brands": brand_tab.head(10).round(4).to_dict("records"),
    }
    btab, binfo = brands_mod.table(L, br, br_ids, lvl)
    summary["brands"] = binfo
    metrics = pd.DataFrame(rows.rows)
    keep = p_rev.size <= int(cfg["engine"].get("max_kept_draw_cells", 50_000_000))
    draws = JointDraws(np.asarray(prod_ids).astype(str), p_rev.astype(np.float32), p_units.astype(np.float32), lvl) if keep else None
    return MetricsOutput(L, P, S, metrics, summary, dem, btab, brands_mod.cohorts(L, as_of),
                         brands_mod.momentum(L, as_of, S), pd.DataFrame(seg_brand_rows), draws)
