"""Phase 1 labelling workflow: reproducible stratified samples, per-check human labels, CSV
round trip for offline labelling. Human relevance corrections also feed the relevance feedback
store (applied on the next run and used to train the local model).

Design
------
* Product sample: strata = segment x sales tier (terciles of known monthly sales, plus
  "sales unknown"). Proportional allocation by largest remainder, fixed seed -- the same seed on
  the same data draws the same products, so before/after fixes are measured on one sample.
* Excluded-listing sample: listings the system excluded as not relevant (irrelevant / uncertain),
  needed to estimate relevance *recall* (relevant listings the system dropped).
* Each item stores a snapshot of what the system said when it was drawn; labels judge that
  snapshot, so a later re-run does not silently change what was measured.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from dip.storage import business as b
from dip.storage import lake

CHECKS = ("relevance", "entity", "segment", "best_listing", "attributes", "dental", "taxonomy")


def _j(v, default):
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v) if isinstance(v, str) and v else default
    except ValueError:
        return default


def _nn(v):
    """A JSON-safe scalar: pandas' missing values become None."""
    try:
        return None if v is None or pd.isna(v) else v
    except (TypeError, ValueError):
        return v


def _allocate(sizes: pd.Series, n: int) -> pd.Series:
    """Largest-remainder proportional allocation, capped by stratum size."""
    n = min(n, int(sizes.sum()))
    raw = sizes / sizes.sum() * n
    alloc = np.floor(raw).astype(int)
    rem = (raw - alloc).sort_values(ascending=False)
    for k in rem.index:
        if alloc.sum() >= n:
            break
        if alloc[k] < sizes[k]:
            alloc[k] += 1
    return alloc


def _tiers(products: pd.DataFrame) -> pd.Series:
    s = pd.to_numeric(products["monthly_sales"], errors="coerce")
    tier = pd.Series("sales unknown", index=products.index)
    known = s.notna()
    if known.sum() >= 3:
        tier[known] = pd.qcut(s[known].rank(method="first"), 3, labels=["low sales", "mid sales", "high sales"]).astype(str)
    elif known.any():
        tier[known] = "sales known"
    return tier


def draw_sample(market: str, n: int = 50, n_excluded: int = 20, seed: int = 7, name: str | None = None,
                created_by: str | None = None) -> dict:
    if not lake.has_curated("products", market):
        raise KeyError(f"market '{market}' has no processed products")
    products = lake.read_curated("products", market).sort_values("product_id").reset_index(drop=True)
    listings = lake.read_curated("listings", market)
    segs = lake.read_curated("segments", market).set_index("segment_id")["segment_label"].to_dict()
    products["stratum"] = products["segment_id"].astype(str) + " | " + _tiers(products)
    sizes = products.groupby("stratum").size()
    alloc = _allocate(sizes, n)
    rng = np.random.default_rng(seed)
    picked = []
    for st, k in alloc.items():
        if k:
            pool = products[products["stratum"] == st]
            picked.append(pool.iloc[np.sort(rng.choice(len(pool), size=int(k), replace=False))])
    chosen = pd.concat(picked) if picked else products.iloc[0:0]
    chosen = chosen.iloc[rng.permutation(len(chosen))]          # label order independent of strata

    rec = lake.read_curated("records", market)
    # the relevance decisions that dropped listings: accepted records judged irrelevant / uncertain
    # (rows rejected for data quality never reached a relevance decision)
    acc = rec["accepted"].fillna(False).astype(bool)
    excl = rec[acc & rec["relevance_status"].isin(["irrelevant", "uncertain"])].sort_values("record_id")
    ex_sizes = excl.groupby("relevance_status").size() if len(excl) else pd.Series(dtype=int)
    ex_alloc = _allocate(ex_sizes, n_excluded) if len(excl) else ex_sizes
    ex_picked = []
    for st, k in ex_alloc.items():
        if k:
            pool = excl[excl["relevance_status"] == st]
            ex_picked.append(pool.iloc[np.sort(rng.choice(len(pool), size=int(k), replace=False))])
    ex_chosen = pd.concat(ex_picked) if ex_picked else excl.iloc[0:0]

    with b.session() as s:
        m = s.get(b.Market, market)
        design = {"n_products": int(len(chosen)), "n_excluded": int(len(ex_chosen)), "population_products": int(len(products)),
                  "population_excluded": int(len(excl)),
                  "population_included_listings": int(len(listings)),
                  "strata": {k: {"size": int(sizes[k]), "drawn": int(alloc[k])} for k in sizes.index},
                  "excluded_strata": {k: {"size": int(ex_sizes[k]), "drawn": int(ex_alloc[k])} for k in ex_sizes.index},
                  "method": "stratified (segment x sales tier), proportional largest-remainder allocation"}
        smp = b.LabelSample(market_name=market, name=name or f"{market} seed {seed}", seed=seed, design=design,
                            dataset_id=m.dataset_id if m else None, created_by=created_by)
        s.add(smp)
        s.flush()
        pos = 0
        for _, p in chosen.iterrows():
            ids = _j(p["listing_ids"], [])
            ls = listings[listings["id"].isin(ids)] if "id" in listings else listings.iloc[0:0]
            snap = {"title": p["title"], "brand": p.get("brand"), "segment_id": p["segment_id"],
                    "segment_label": segs.get(p["segment_id"]), "model_label": p.get("model_label"),
                    "price_tier": p.get("price_tier"), "attributes": _j(p.get("attributes"), {}),
                    # knowledge layer (judged by the dental / attributes / taxonomy checks)
                    "dental_confidence": None if pd.isna(p.get("dental_confidence")) else float(p["dental_confidence"]),
                    "dental_band": _nn(p.get("dental_band")), "kn_attributes": _j(p.get("kn_attributes"), {}),
                    "configuration_key": _nn(p.get("configuration_key")), "taxonomy_node": _nn(p.get("taxonomy_node")),
                    "best_listing": p.get("best_listing"), "monthly_sales": None if pd.isna(p.get("monthly_sales")) else float(p["monthly_sales"]),
                    "listings": [{"record_id": r["record_id"], "id": r["id"], "title": r["title"],
                                  "price": None if pd.isna(r["price"]) else float(r["price"]),
                                  "sales": None if pd.isna(r["sales"]) else float(r["sales"]),
                                  "is_best_listing": bool(r["is_best_listing"]), "relevance_status": r["relevance_status"]}
                                 for _, r in ls.iterrows()]}
            s.add(b.LabelItem(sample_id=smp.id, kind="product", ref_id=p["product_id"], stratum=p["stratum"], position=pos, snapshot=snap))
            pos += 1
        for _, r in ex_chosen.iterrows():
            snap = {"id": r["id"], "title": r["title"], "relevance_status": r["relevance_status"],
                    "relevance_score": None if pd.isna(r.get("relevance_score")) else float(r["relevance_score"]),
                    "relevance_explanation": r.get("relevance_explanation"), "excluded_reason": r["excluded_reason"]}
            s.add(b.LabelItem(sample_id=smp.id, kind="excluded_listing", ref_id=r["record_id"],
                              stratum=f"excluded | {r['relevance_status']}", position=pos, snapshot=snap))
            pos += 1
        sid = smp.id
    return sample_summary(sid)


def sample_summary(sample_id: str) -> dict:
    with b.session() as s:
        smp = s.get(b.LabelSample, sample_id)
        if smp is None:
            raise KeyError(sample_id)
        items = s.query(b.LabelItem).filter_by(sample_id=sample_id).all()
        labelled = {i for (i,) in s.query(b.Label.item_id).filter_by(sample_id=sample_id).distinct()}
        out = b.row_dict(smp)
    out["items"] = len(items)
    out["labelled_items"] = len(labelled)
    return out


def list_samples(market: str | None = None) -> list[dict]:
    with b.session() as s:
        q = s.query(b.LabelSample).order_by(b.LabelSample.created_at.desc())
        if market:
            q = q.filter_by(market_name=market)
        ids = [x.id for x in q.all()]
    return [sample_summary(i) for i in ids]


def latest_labels(sample_id: str) -> dict[tuple[str, str], dict]:
    """(item_id, check) -> the most recent label."""
    with b.session() as s:
        rows = [b.row_dict(x) for x in s.query(b.Label).filter_by(sample_id=sample_id).order_by(b.Label.at).all()]
    return {(r["item_id"], r["check"]): r for r in rows}


def items(sample_id: str) -> list[dict]:
    lab = latest_labels(sample_id)
    with b.session() as s:
        rows = [b.row_dict(x) for x in s.query(b.LabelItem).filter_by(sample_id=sample_id).order_by(b.LabelItem.position).all()]
    for r in rows:
        r["labels"] = {c: lab[(r["id"], c)] for c in CHECKS if (r["id"], c) in lab}
    return rows


def _entity_correct(value: dict) -> bool:
    return not (value.get("wrong_listings") or value.get("missing_listings"))


def save_label(item_id: str, check: str, value: dict | None, correct: bool | None = None, notes: str | None = None,
               labeller: str | None = None) -> dict:
    """Store one judgement. ``value`` shapes:
    relevance (product)          {"irrelevant_listings": [asin, ...]}           correct = none irrelevant
    relevance (excluded_listing) {"is_relevant": bool}                          correct = not is_relevant
    entity                       {"wrong_listings": [asin], "missing_listings": [asin]}
    dental                       {"is_dental": bool}   correct = the product's Dental Confidence band agrees
                                 (a "review" band is neither right nor wrong: correct is None)
    segment / best_listing / attributes / taxonomy: correct = bool, value optional {"suggested": ...}
    """
    if check not in CHECKS:
        raise ValueError(f"check must be one of {CHECKS}")
    value = value or {}
    with b.session() as s:
        it = s.get(b.LabelItem, item_id)
        if it is None:
            raise KeyError(item_id)
        if it.kind == "excluded_listing" and check != "relevance":
            raise ValueError("excluded listings are labelled for relevance only")
        if check == "relevance":
            if it.kind == "excluded_listing":
                if "is_relevant" not in value:
                    raise ValueError("value.is_relevant is required")
                correct = not bool(value["is_relevant"])
            else:
                known = {x["id"] for x in (it.snapshot or {}).get("listings", [])}
                bad = set(value.get("irrelevant_listings") or [])
                if not bad <= known:
                    raise ValueError(f"unknown listings: {sorted(bad - known)}")
                correct = not bad
        elif check == "dental":
            if "is_dental" not in value:
                raise ValueError("value.is_dental is required")
            bnd = (it.snapshot or {}).get("dental_band")
            pred = True if bnd in ("strong", "probable", "verified_dental") else False if bnd in (
                "probably_non_dental", "non_dental", "verified_non_dental") else None
            correct = None if pred is None else pred == bool(value["is_dental"])
        elif check == "entity":
            known = {x["id"] for x in (it.snapshot or {}).get("listings", [])}
            wrong = set(value.get("wrong_listings") or [])
            if not wrong <= known:
                raise ValueError(f"unknown listings: {sorted(wrong - known)}")
            correct = _entity_correct(value)
        elif correct is None:
            raise ValueError("correct (true/false) is required")
        row = b.Label(item_id=item_id, sample_id=it.sample_id, check=check, value=value, correct=correct,
                      notes=notes, labeller=labeller)
        s.add(row)
        s.flush()
        out = b.row_dict(row)
        smp = s.get(b.LabelSample, it.sample_id)
        market, snap, kind, ref = smp.market_name, dict(it.snapshot or {}), it.kind, it.ref_id
    _feed_relevance(market, kind, ref, snap, check, value, labeller)
    return out


def _feed_relevance(market, kind, ref, snap, check, value, labeller) -> None:
    """Relevance labels are corrections too: store them in the relevance feedback used by the pipeline."""
    if check != "relevance":
        return
    from dip.settings import get_settings
    from dmie.engine import store as core_store

    pairs = []
    if kind == "excluded_listing":
        pairs.append((ref, snap.get("title") or "", bool(value.get("is_relevant"))))
    else:
        bad = set(value.get("irrelevant_listings") or [])
        pairs += [(x["record_id"], x.get("title") or "", x["id"] not in bad) for x in snap.get("listings", [])]
    con = core_store.connect(get_settings().analytics_path)
    try:
        for rid, text, is_dental in pairs:
            core_store.add_feedback(con, "dental", rid, text.lower(), is_dental, market, f"pilot-labelling:{labeller or 'unknown'}")
    finally:
        con.close()


# ------------------------------------------------------------------ offline labelling (CSV)
SHEET_COLUMNS = ["item_id", "kind", "stratum", "title", "segment_label", "best_listing", "listings", "dental_band", "taxonomy_node",
                 "relevance_irrelevant_listings", "excluded_is_relevant", "entity_wrong_listings",
                 "entity_missing_listings", "segment_correct", "best_listing_correct", "attributes_correct",
                 "dental_is_dental", "taxonomy_correct", "notes"]


def export_sheet(sample_id: str) -> pd.DataFrame:
    rows = []
    for it in items(sample_id):
        sn = it["snapshot"] or {}
        rows.append({"item_id": it["id"], "kind": it["kind"], "stratum": it["stratum"], "title": sn.get("title"),
                     "segment_label": sn.get("segment_label"), "best_listing": sn.get("best_listing"),
                     "dental_band": sn.get("dental_band"), "taxonomy_node": sn.get("taxonomy_node"),
                     "listings": " ; ".join(f"{x['id']}: {x['title']}" for x in sn.get("listings", [])) or sn.get("id")})
    return pd.DataFrame(rows).reindex(columns=SHEET_COLUMNS)


def _ids(v) -> list[str]:
    return [x.strip() for x in str(v).replace(",", " ").replace(";", " ").split() if x.strip()] if pd.notna(v) and str(v).strip() else []


def _yn(v) -> bool | None:
    if pd.isna(v) or str(v).strip() == "":
        return None
    t = str(v).strip().lower()
    if t in ("y", "yes", "1", "true", "correct", "ok"):
        return True
    if t in ("n", "no", "0", "false", "wrong"):
        return False
    raise ValueError(f"not a yes/no value: {v!r}")


def import_sheet(sample_id: str, df: pd.DataFrame, labeller: str) -> int:
    """Blank cells mean "not labelled"; for relevance/entity on products an explicit "none" means no errors."""
    valid = {it["id"]: it for it in items(sample_id)}
    n = 0
    for _, r in df.iterrows():
        it = valid.get(r["item_id"])
        if it is None:
            raise ValueError(f"item {r['item_id']} is not in sample {sample_id}")
        notes = None if pd.isna(r.get("notes")) else str(r["notes"])
        if it["kind"] == "excluded_listing":
            v = _yn(r.get("excluded_is_relevant"))
            if v is not None:
                save_label(it["id"], "relevance", {"is_relevant": v}, notes=notes, labeller=labeller)
                n += 1
            continue
        filled = lambda c: pd.notna(r.get(c)) and str(r.get(c)).strip() != ""  # noqa: E731
        none = lambda c: str(r.get(c)).strip().lower() == "none"  # noqa: E731
        if filled("relevance_irrelevant_listings"):
            save_label(it["id"], "relevance", {"irrelevant_listings": [] if none("relevance_irrelevant_listings")
                                               else _ids(r["relevance_irrelevant_listings"])}, notes=notes, labeller=labeller)
            n += 1
        if filled("entity_wrong_listings") or filled("entity_missing_listings"):
            val = {"wrong_listings": [] if not filled("entity_wrong_listings") or none("entity_wrong_listings") else _ids(r["entity_wrong_listings"]),
                   "missing_listings": [] if not filled("entity_missing_listings") or none("entity_missing_listings") else _ids(r["entity_missing_listings"])}
            save_label(it["id"], "entity", val, notes=notes, labeller=labeller)
            n += 1
        v = _yn(r.get("dental_is_dental"))
        if v is not None:
            save_label(it["id"], "dental", {"is_dental": v}, notes=notes, labeller=labeller)
            n += 1
        for check in ("segment", "best_listing", "attributes", "taxonomy"):
            v = _yn(r.get(f"{check}_correct"))
            if v is not None:
                save_label(it["id"], check, {}, correct=v, notes=notes, labeller=labeller)
                n += 1
    return n
