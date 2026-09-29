"""Phase 1 accuracy metrics, computed from human labels (never estimated by a model).

Labels are stored as ground truth (which listings are dental; which listings belong together),
so a sample can be scored against the run it was drawn from (``against="snapshot"``) or against
the current processed data after a rule change (``against="current"``).

* relevance precision  -- labelled-relevant share of listings the system kept (listing level).
* relevance recall     -- estimated with the excluded-listing sample:
                          P*N_kept / (P*N_kept + F*N_excluded), F = relevant share of excluded sample.
* entity resolution    -- pairwise precision / recall over the labelled listings of each item
                          (true pair = both listings belong to the same real product).
* segment / best listing / attributes -- share judged correct.

Intervals: Wilson 95 % for simple shares; percentile bootstrap (items resampled, fixed seed)
for ratio estimators, since listings are clustered within sampled products.
Caveat reported with every "current" run: relevance labels are also written to the relevance
feedback store, so a re-run *overrides* those listings with the human answer. Such listings are
excluded from "current" relevance metrics; use a fresh holdout sample to measure a rule change.
"""

from __future__ import annotations

import math
from itertools import combinations

import numpy as np
import pandas as pd

from dip.pilot import labels as L
from dip.storage import business as b
from dip.storage import lake

Z = 1.959964


def wilson(k: int, n: int) -> dict:
    if n == 0:
        return {"value": None, "low": None, "high": None, "n": 0}
    p = k / n
    d = 1 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / d
    h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return {"value": p, "low": max(0.0, c - h), "high": min(1.0, c + h), "n": n}


def _boot(units: list, stat, reps: int = 1000, seed: int = 0) -> tuple[float | None, float | None]:
    if len(units) < 2:
        return None, None
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(reps):
        v = stat([units[i] for i in rng.integers(0, len(units), len(units))])
        if v is not None:
            vals.append(v)
    if not vals:
        return None, None
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def _ratio(num, den):
    return None if den == 0 else num / den


# ------------------------------------------------------------------ system view of an item
def _current_tables(market: str):
    lst = lake.read_curated("listings", market) if lake.has_curated("listings", market) else pd.DataFrame()
    rec = lake.read_curated("records", market) if lake.has_curated("records", market) else pd.DataFrame()
    prods = lake.read_curated("products", market) if lake.has_curated("products", market) else pd.DataFrame()
    return lst, rec, prods


def _system(item: dict, against: str, cur) -> dict:
    """What the system says about the item's listings: kept?, product group per listing, segment..."""
    sn = item["snapshot"] or {}
    if against == "snapshot":
        if item["kind"] == "excluded_listing":
            return {"kept": {sn.get("id"): False}, "human": set()}
        ids = [x["id"] for x in sn.get("listings", [])]
        return {"kept": {i: True for i in ids}, "group": {i: "this" for i in ids}, "segment_id": sn.get("segment_id"),
                "best_listing": sn.get("best_listing"), "attributes": sn.get("attributes"), "human": set()}
    lst, rec, prods = cur
    status = rec.drop_duplicates("id", keep="last").set_index("id")["relevance_status"].astype(str).to_dict() if len(rec) else {}
    kept_ids = set(lst["id"]) if len(lst) else set()
    group = lst.drop_duplicates("id", keep="last").set_index("id")["product_id"].to_dict() if len(lst) else {}
    ids = [sn.get("id")] if item["kind"] == "excluded_listing" else [x["id"] for x in sn.get("listings", [])]
    out = {"kept": {i: i in kept_ids for i in ids}, "group": group,
           "human": {i for i in ids if status.get(i, "").startswith("human")}}
    if item["kind"] == "product" and len(prods):
        pid = group.get(sn.get("best_listing"))
        row = prods[prods["product_id"] == pid]
        if len(row):
            r = row.iloc[0]
            out.update(segment_id=r["segment_id"], best_listing=r["best_listing"], attributes=L._j(r["attributes"], {}))
    return out


# ------------------------------------------------------------------ metrics
def evaluate(sample_id: str, against: str = "snapshot") -> dict:
    if against not in ("snapshot", "current"):
        raise ValueError("against must be snapshot or current")
    smp = L.sample_summary(sample_id)
    design = smp["design"] or {}
    its = L.items(sample_id)
    cur = _current_tables(smp["market_name"]) if against == "current" else None

    # ---- relevance
    rel_units, ex_units, human_excluded = [], [], 0
    for it in its:
        lab = it["labels"].get("relevance")
        if not lab:
            continue
        sysv = _system(it, against, cur)
        if it["kind"] == "excluded_listing":
            lid = (it["snapshot"] or {}).get("id")
            if lid in sysv["human"]:
                human_excluded += 1
                continue
            ex_units.append({"kept": sysv["kept"].get(lid, False), "relevant": bool(lab["value"].get("is_relevant"))})
        else:
            bad = set(lab["value"].get("irrelevant_listings") or [])
            pairs = []
            for x in it["snapshot"].get("listings", []):
                if x["id"] in sysv["human"]:
                    human_excluded += 1
                    continue
                pairs.append({"kept": sysv["kept"].get(x["id"], False), "relevant": x["id"] not in bad})
            rel_units.append(pairs)
    # listings from product items were kept in the snapshot; in "current" some may now be dropped
    all_l = [x for u in rel_units for x in u] + ex_units
    kept = [x for x in all_l if x["kept"]]
    dropped = [x for x in all_l if not x["kept"]]
    prec = wilson(sum(x["relevant"] for x in kept), len(kept))
    n_kept_pop = design.get("population_included_listings") or 0
    n_ex_pop = design.get("population_excluded") or 0

    def recall_stat(units):
        k = [x for u in units for x in (u if isinstance(u, list) else [u]) if x["kept"]]
        d = [x for u in units for x in (u if isinstance(u, list) else [u]) if not x["kept"]]
        if not k:
            return None
        p = sum(x["relevant"] for x in k) / len(k)
        f = (sum(x["relevant"] for x in d) / len(d)) if d else 0.0
        tp, fn = p * n_kept_pop, f * n_ex_pop
        return _ratio(tp, tp + fn)

    units = rel_units + ex_units
    rec_val = recall_stat(units) if (ex_units or n_ex_pop == 0) else None
    rec_ci = _boot(units, recall_stat) if rec_val is not None else (None, None)
    relevance = {"precision": prec, "recall": {"value": rec_val, "low": rec_ci[0], "high": rec_ci[1],
                                               "n_excluded_labelled": len(ex_units)},
                 "listings_labelled": len(all_l), "dropped_listings_labelled": len(dropped),
                 "human_override_excluded": human_excluded,
                 "note": None if ex_units or n_ex_pop == 0 else "recall needs labelled excluded listings"}

    # ---- entity resolution (pairwise)
    ent_units = []
    for it in its:
        lab = it["labels"].get("entity")
        if it["kind"] != "product" or not lab:
            continue
        sn = it["snapshot"]
        ids = [x["id"] for x in sn.get("listings", [])]
        wrong = set(lab["value"].get("wrong_listings") or [])
        missing = [m for m in (lab["value"].get("missing_listings") or []) if m not in ids]
        truth = (set(ids) - wrong) | set(missing)
        universe = ids + missing
        sysv = _system(it, against, cur)
        grp = sysv.get("group", {})
        tp = fp = fn = 0
        for a, c in combinations(universe, 2):
            same_truth = a in truth and c in truth
            ga, gc = grp.get(a), grp.get(c)
            same_sys = ga is not None and ga == gc
            tp += same_truth and same_sys
            fp += (not same_truth) and same_sys
            fn += same_truth and not same_sys
        ent_units.append({"tp": tp, "fp": fp, "fn": fn, "exact": fp == 0 and fn == 0})

    def pw(units, which):
        tp = sum(u["tp"] for u in units)
        return _ratio(tp, tp + sum(u[which] for u in units))

    entity = {"pairwise_precision": {"value": pw(ent_units, "fp"), **dict(zip(("low", "high"), _boot(ent_units, lambda u: pw(u, "fp"))))},
              "pairwise_recall": {"value": pw(ent_units, "fn"), **dict(zip(("low", "high"), _boot(ent_units, lambda u: pw(u, "fn"))))},
              "products_exactly_right": wilson(sum(u["exact"] for u in ent_units), len(ent_units)),
              "products_labelled": len(ent_units),
              "pairs": {k: sum(u[k] for u in ent_units) for k in ("tp", "fp", "fn")}}

    # ---- segment / best listing / attributes
    simple, unverified = {}, {}
    for check in ("segment", "best_listing", "attributes"):
        k = n = unv = 0
        for it in its:
            lab = it["labels"].get(check)
            if it["kind"] != "product" or not lab:
                continue
            if against == "current":
                sysv = _system(it, against, cur)
                key = {"segment": "segment_id", "best_listing": "best_listing", "attributes": "attributes"}[check]
                if sysv.get(key) != (it["snapshot"] or {}).get(key):
                    unv += 1                      # the system's answer changed -- the old label no longer applies
                    continue
            n += 1
            k += bool(lab["correct"])
        simple[check] = wilson(k, n)
        unverified[check] = unv

    # ---- knowledge layer: dental (the band's verdict; a review band is neither right nor wrong) and taxonomy node
    for check in ("dental", "taxonomy"):
        labs = [it["labels"].get(check) for it in its if it["kind"] == "product" and it["labels"].get(check)]
        decided = [lab for lab in labs if lab["correct"] is not None]
        simple[check] = {**wilson(sum(bool(lab["correct"]) for lab in decided), len(decided)),
                         "labelled": len(labs), "review_band": len(labs) - len(decided)}
    errors = [{"item": it["id"], "check": c, "title": (it["snapshot"] or {}).get("title"), "value": lab["value"], "notes": lab["notes"]}
              for it in its for c, lab in it["labels"].items() if lab["correct"] is False]
    return {"sample": {k: smp[k] for k in ("id", "market_name", "name", "seed", "items", "labelled_items", "created_at")},
            "design": design, "against": against, "relevance": relevance, "entity": entity,
            "segment": simple["segment"], "best_listing": simple["best_listing"], "attributes": simple["attributes"],
            "dental": simple["dental"], "taxonomy": simple["taxonomy"],
            "unverified_after_change": unverified if against == "current" else None, "errors": errors}


# ------------------------------------------------------------------ report
def _fmt(m: dict | None) -> str:
    if not m or m.get("value") is None:
        return "not measured"
    ci = f" ({m['low']:.0%}–{m['high']:.0%})" if m.get("low") is not None else ""
    n = f", n={m['n']}" if m.get("n") else ""
    return f"{m['value']:.0%}{ci}{n}"


def report(market: str | None = None) -> str:
    with b.session() as s:
        q = s.query(b.LabelSample).order_by(b.LabelSample.market_name, b.LabelSample.created_at)
        if market:
            q = q.filter_by(market_name=market)
        samples = [x.id for x in q.all()]
    out = ["# Accuracy report (Phase 1)", "",
           "Generated by `python scripts/pilot.py accuracy`. Every number comes from human labels on a "
           "stratified random sample (method: `src/dip/pilot/metrics.py`). 95 % intervals: Wilson for shares, "
           "bootstrap for ratio estimators.", ""]
    if not samples:
        out += ["**No labelling samples exist yet — no accuracy number can be stated.**", "",
                "Draw one per market (`python scripts/pilot.py sample MARKET --n 50`), label it on the "
                "Labelling page or offline (`export-sample` / `import-labels`), then regenerate this report.", ""]
        return "\n".join(out)
    out += ["| Market | Sample | Labelled | Relevance precision | Relevance recall | ER pairwise precision | ER pairwise recall | "
            "Products exactly grouped | Segment correct | Best listing correct | Attributes correct |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    evals = []
    for sid in samples:
        for against in ("snapshot", "current"):
            e = evaluate(sid, against)
            evals.append(e)
            sm = e["sample"]
            out.append(f"| {sm['market_name']} | {sm['name']} ({against}) | {sm['labelled_items']}/{sm['items']} | "
                       f"{_fmt(e['relevance']['precision'])} | {_fmt(e['relevance']['recall'])} | "
                       f"{_fmt(e['entity']['pairwise_precision'])} | {_fmt(e['entity']['pairwise_recall'])} | "
                       f"{_fmt(e['entity']['products_exactly_right'])} | {_fmt(e['segment'])} | "
                       f"{_fmt(e['best_listing'])} | {_fmt(e['attributes'])} |")
    out += ["", "*snapshot* = the run the sample was drawn from (before fixes). *current* = the data as processed now "
            "(after fixes). Relevance labels are applied as overrides on re-runs, so overridden listings are left out of "
            "*current* relevance; measure rule changes on a fresh holdout sample.", "", "## Errors found", ""]
    for e in evals:
        if e["against"] != "snapshot" or not e["errors"]:
            continue
        out.append(f"### {e['sample']['market_name']} — {e['sample']['name']}")
        out += [f"- **{x['check']}** · {x['title']} · {x['value'] or ''} {('— ' + x['notes']) if x['notes'] else ''}" for x in e["errors"][:40]]
        out.append("")
    return "\n".join(out)
