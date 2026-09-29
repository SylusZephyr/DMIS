"""Dynamic taxonomy discovery (spec 9-10, 103-104, 161).

    canonical products -> attribute matrix -> candidate dimensions -> tested splits -> candidate taxonomy
                       -> human review (approve / reject / rename) -> approved taxonomy

Nothing is assumed before seeing the data. Every commercial attribute of the market's schema is a
candidate dimension; it becomes a taxonomy level only when

* coverage  -- enough products carry a value (``min_coverage``),
* size      -- at least two groups have ``min_products`` products,
* commerce  -- the groups differ in price: eta squared of log price >= ``min_eta2`` with a permutation-test
               p-value <= ``alpha`` (a dimension that does not move price does not define a market segment).

Numeric attributes (max RPM, torque) are cut at natural breaks: every split of the sorted values into
2..``max_bins`` groups of at least ``min_products`` is scored by eta squared of log price and the best is
kept; boundaries are the midpoint between neighbouring groups, rounded to two significant figures.

The tree is built greedily: the best dimension at the root, then the best remaining dimension inside
each child, down to ``max_depth``. Every node carries its evidence (products, listings, modeled revenue,
price median, eta squared, p-value) and a status: ``machine`` until a person approves, rejects or
renames it (``TaxonomyDecision``). Both taxonomies are retained (spec 10).
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from dip.knowledge import config
from dip.knowledge.attributes import schema_for

NODE_COLUMNS = ["node_key", "parent_key", "depth", "dimension", "value", "label", "products", "listings", "revenue_est",
                "revenue_lo", "revenue_hi", "price_median", "price_min", "price_max", "eta2", "p_value", "explanation",
                "product_ids", "status", "approved_label"]


def _cfg() -> dict:
    return config()["taxonomy_discovery"]


def eta2(logp: np.ndarray, groups: np.ndarray) -> float:
    """Share of log-price variance explained by the groups (vectorised)."""
    ok = ~np.isnan(logp)
    x, g = logp[ok], groups[ok]
    if len(x) < 3:
        return 0.0
    _, codes = np.unique(g, return_inverse=True)
    if codes.max() < 1:
        return 0.0
    total = float(((x - x.mean()) ** 2).sum())
    if total <= 0:
        return 0.0
    n = np.bincount(codes)
    means = np.bincount(codes, weights=x) / n
    return float((n * (means - x.mean()) ** 2).sum() / total)


PERMUTATION_MAX_N = 2000   # above this, the F-test p-value (same statistic, asymptotic) replaces permutations


def perm_p(logp: np.ndarray, groups: np.ndarray, n: int, rng: np.random.Generator) -> float:
    ok = ~np.isnan(logp)
    x, g = logp[ok], groups[ok]
    obs = eta2(x, g)
    if obs <= 0:
        return 1.0
    k = len(np.unique(g))
    if len(x) > PERMUTATION_MAX_N:
        from scipy.stats import f as f_dist
        dfb, dfw = k - 1, len(x) - k
        if dfw <= 0 or obs >= 1:
            return 0.0 if obs >= 1 else 1.0
        return float(f_dist.sf((obs / dfb) / ((1 - obs) / dfw), dfb, dfw))
    hits = sum(eta2(x, rng.permutation(g)) >= obs - 1e-12 for _ in range(n))
    return (hits + 1) / (n + 1)


def _round2(v: float) -> float:
    if v <= 0:
        return v
    mag = 10 ** (math.floor(math.log10(v)) - 1)
    return round(v / mag) * mag


def numeric_breaks(values: np.ndarray, logp: np.ndarray, min_n: int, max_bins: int) -> list[float] | None:
    """Best natural-break boundaries for one numeric attribute (None when no valid split)."""
    ok = ~np.isnan(values)
    v, lp = values[ok], logp[ok]
    order = np.argsort(v, kind="stable")
    v, lp = v[order], lp[order]
    uniq = np.unique(v)
    if len(uniq) < 2 or len(v) < 2 * min_n:
        return None
    # candidate cut positions: between distinct values
    cuts = [i for i in range(1, len(v)) if v[i] != v[i - 1]]
    best, best_e = None, 0.0

    def score(cs):
        g = np.zeros(len(v), dtype=int)
        for k, c in enumerate(cs, start=1):
            g[c:] = k
        sizes = np.bincount(g)
        if (sizes < min_n).any():
            return -1.0
        return eta2(lp, g)

    import itertools
    for k in range(1, max_bins):
        if len(cuts) > 60 and k > 2:          # keep the search bounded on long value lists
            break
        for cs in itertools.combinations(cuts, k):
            e = score(cs)
            if e > best_e:
                best, best_e = cs, e
    if best is None:
        return None
    return [_round2((v[c - 1] + v[c]) / 2) for c in best]


def _bin_labels(breaks: list[float], unit: str | None) -> list[str]:
    u = f" {unit}" if unit else ""
    edges = [None, *breaks, None]
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        if lo is None:
            out.append(f"< {hi:,.0f}{u}" if hi >= 10 else f"< {hi:g}{u}")
        elif hi is None:
            out.append(f"≥ {lo:,.0f}{u}" if lo >= 10 else f"≥ {lo:g}{u}")
        else:
            out.append(f"{lo:,.0f}–{hi:,.0f}{u}" if lo >= 10 else f"{lo:g}–{hi:g}{u}")
    return out


def _product_values(products: pd.DataFrame, dims: list[str]) -> pd.DataFrame:
    vals = []
    for s in products.get("kn_attributes", pd.Series([None] * len(products))):
        a = json.loads(s) if isinstance(s, str) else {}
        vals.append({d: ("+".join(map(str, a[d])) if isinstance(a.get(d), list) else a.get(d)) for d in dims})
    return pd.DataFrame(vals, index=products.index)


def evaluate_dimension(sub: pd.DataFrame, dim: str, spec: dict, rng: np.random.Generator) -> dict | None:
    c = _cfg()
    min_n = int(c["min_products"])
    values = sub[f"_d_{dim}"]
    coverage = float(values.notna().mean()) if len(values) else 0.0
    if coverage < float(c["min_coverage"]):
        return None
    logp = np.log(pd.to_numeric(sub["price_median"], errors="coerce").where(lambda x: x > 0).to_numpy(dtype=float))
    if spec.get("kind") == "number":
        num = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
        br = numeric_breaks(num, logp, min_n, int(c["max_bins"]))
        if not br:
            return None
        labels = _bin_labels(br, spec.get("unit_label"))
        grp = np.full(len(num), None, dtype=object)
        okv = ~np.isnan(num)
        idx = np.searchsorted(np.array(br), num[okv], side="right")
        grp[okv] = [labels[i] for i in idx]
        keys = pd.Series(grp, index=sub.index)
    else:
        keys = values.astype(object).where(values.notna(), None)
        counts = keys.value_counts()
        keep = counts[counts >= min_n].index
        keys = keys.where(keys.isin(keep), None)
    counts = keys.value_counts()
    if (counts >= min_n).sum() < 2:
        return None
    mask = keys.notna().to_numpy()
    g = keys[mask].astype(str).to_numpy()
    e = eta2(logp[mask], g)
    p = perm_p(logp[mask], g, int(c["permutations"]), rng)
    # rank: variance explained among the products the dimension classifies, times coverage squared -- a level
    # should classify most products, so a strong split of a small minority ranks below a good split of nearly all
    return {"dimension": dim, "coverage": coverage, "groups": {str(k): int(v) for k, v in counts.items()}, "eta2": e,
            "p_value": p, "rank_score": e * coverage ** 2, "keys": keys,
            "meaningful": e >= float(c["min_eta2"]) and p <= float(c["alpha"])}


def _price_line(sub: pd.DataFrame, keys: pd.Series) -> str:
    med = pd.to_numeric(sub["price_median"], errors="coerce").groupby(keys).median().dropna().sort_values()
    return ", ".join(f"{k} ${v:,.0f}" for k, v in med.items())


def discover(products: pd.DataFrame, market: str, decisions: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(nodes, dimension ranking at the root). ``products`` needs product_id, kn_attributes, price_median,
    listing_count and the modeled revenue columns."""
    c = _cfg()
    schema = schema_for(market)
    dims = [d for d, s in schema.items() if s.get("commercial")]
    units = {"rpm": "rpm", "torque": "N·cm", "watt": "W", "volt": "V", "count": "ct", "mass": "g", "length": "mm", "volume": "ml"}
    specs = {d: {**schema[d], "unit_label": units.get(schema[d].get("unit", ""))} for d in dims}
    P = products.copy()
    vals = _product_values(P, dims)
    for d in dims:
        P[f"_d_{d}"] = vals[d]
    rng = np.random.default_rng(int(c["seed"]))
    nodes: list[dict] = []
    ranking: list[dict] = []

    def add_node(sub, key, parent, depth, dim, value, e, p, expl):
        rev = pd.to_numeric(sub.get("revenue_est"), errors="coerce")
        price = pd.to_numeric(sub["price_median"], errors="coerce")
        path_label = " / ".join(part.split("=", 1)[1] for part in key.split("/"))     # "reline_kit / soft"
        nodes.append({"node_key": key, "parent_key": parent, "depth": depth, "dimension": dim, "value": value, "label": path_label,
                      "products": int(len(sub)), "listings": int(pd.to_numeric(sub.get("listing_count"), errors="coerce").sum()),
                      "revenue_est": float(rev.sum()) if rev.notna().any() else None,
                      "revenue_lo": float(pd.to_numeric(sub.get("revenue_lo"), errors="coerce").sum()) if "revenue_lo" in sub else None,
                      "revenue_hi": float(pd.to_numeric(sub.get("revenue_hi"), errors="coerce").sum()) if "revenue_hi" in sub else None,
                      "price_median": float(price.median()) if price.notna().any() else None,
                      "price_min": float(price.min()) if price.notna().any() else None,
                      "price_max": float(price.max()) if price.notna().any() else None,
                      "eta2": e, "p_value": p, "explanation": expl, "product_ids": json.dumps(sub["product_id"].astype(str).tolist())})

    def split(sub: pd.DataFrame, parent: str, depth: int, used: set[str]):
        if depth > int(c["max_depth"]) or len(sub) < 2 * int(c["min_products"]):
            return
        cands = [r for d in dims if d not in used and (r := evaluate_dimension(sub, d, specs[d], rng)) is not None]
        if depth == 1:
            ranking.extend({k: v for k, v in r.items() if k != "keys"} for r in cands)
        good = sorted([r for r in cands if r["meaningful"]], key=lambda r: (-r["rank_score"], r["p_value"], r["dimension"]))
        if not good:
            return
        # parsimony: among dimensions nearly as good as the best, the one with the fewest groups (a technology
        # before its RPM ranges, spec 103-104)
        tol = float(c.get("parsimony", 0.10))
        near = [r for r in good if r["rank_score"] >= (1 - tol) * good[0]["rank_score"]]
        best = min(near, key=lambda r: (sum(1 for n in r["groups"].values() if n >= int(c["min_products"])), -r["rank_score"], r["dimension"]))
        keys = best["keys"]
        line = _price_line(sub, keys)
        for value, n in best["groups"].items():
            if n < int(c["min_products"]):
                continue
            child = sub[keys == value]
            key = f"{parent}/{best['dimension']}={value}" if parent else f"{best['dimension']}={value}"
            expl = (f"{best['dimension']} (stated on {best['coverage']:.0%} of {len(sub)} products) explains {best['eta2']:.0%} "
                    f"of their log-price variance "
                    f"(permutation p={best['p_value']:.3f}); median price by group: {line}")
            add_node(child, key, parent or None, depth, best["dimension"], value, round(best["eta2"], 4), round(best["p_value"], 4), expl)
            split(child, key, depth + 1, used | {best["dimension"]})
        other = sub[keys.isna() | ~keys.isin([k for k, n in best["groups"].items() if n >= int(c["min_products"])])]
        if len(other):
            key = f"{parent}/{best['dimension']}=other" if parent else f"{best['dimension']}=other"
            add_node(other, key, parent or None, depth, best["dimension"], "other", None, None,
                     f"products without a {best['dimension']} value, or in groups smaller than {c['min_products']}")

    split(P, "", 1, set())
    out = pd.DataFrame(nodes, columns=[c_ for c_ in NODE_COLUMNS if c_ not in ("status", "approved_label")])
    out["status"], out["approved_label"] = "machine", None
    if decisions is not None and len(decisions) and len(out):
        latest = decisions.drop_duplicates("node_key", keep="last").set_index("node_key")
        st = out["node_key"].map(latest["decision"])
        out["status"] = st.fillna("machine")
        out["approved_label"] = out["node_key"].map(latest["label"]).where(st.isin(["renamed", "approved"]))
    rank = pd.DataFrame(ranking, columns=["dimension", "coverage", "groups", "eta2", "p_value", "rank_score", "meaningful"])
    if len(rank):
        rank["groups"] = rank["groups"].map(json.dumps)
        rank = rank.sort_values(["meaningful", "rank_score"], ascending=[False, False]).reset_index(drop=True)
    return out, rank


def load_decisions(market: str) -> pd.DataFrame:
    from dip.storage import business as b
    with b.session() as s:
        rows = s.query(b.TaxonomyDecision).filter(b.TaxonomyDecision.market_name == market).order_by(b.TaxonomyDecision.at).all()
        return pd.DataFrame([{"node_key": r.node_key, "decision": r.decision, "label": r.label} for r in rows],
                            columns=["node_key", "decision", "label"])


def assign(products: pd.DataFrame, nodes: pd.DataFrame) -> pd.Series:
    """The deepest taxonomy node of every product (None when the taxonomy has no node for it)."""
    out = pd.Series(None, index=products.index, dtype=object)
    if not len(nodes):
        return out
    where = {}
    for key, depth, ids in zip(nodes["node_key"], nodes["depth"], nodes["product_ids"]):
        for pid in json.loads(ids):
            if depth >= where.get(pid, (0, None))[0]:
                where[pid] = (depth, key)
    return products["product_id"].astype(str).map(lambda p: where.get(p, (0, None))[1])
