"""Product IDs that survive re-uploads.

Resolution names a product by the hash of its member listing IDs, so a product whose listing set
changes between snapshots (a new seller listing joins, one is delisted) would get a new ID --
detaching labels, comments, project links and history keyed on it. After resolution, each new
group therefore inherits the previous run's product ID when it holds the majority of that previous
product's still-present listings. Each previous ID is used at most once (on a split, the larger
part keeps it); groups with no such predecessor keep their hash ID. Grouping itself is unchanged.
"""

from __future__ import annotations

from collections import Counter

import pandas as pd


def stabilize(frame: pd.DataFrame, products: pd.DataFrame, previous: pd.DataFrame | None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """``previous``: listing id -> product_id of the last run (columns id, product_id)."""
    if previous is None or previous.empty or "product_id" not in frame:
        return frame, products, {"status": "no previous run"}
    prev = previous.dropna(subset=["id", "product_id"]).drop_duplicates("id", keep="last").set_index("id")["product_id"]
    cur = frame.dropna(subset=["product_id"]).drop_duplicates("id", keep="last").set_index("id")["product_id"]
    common = cur.index.intersection(prev.index)
    present_prev = prev.loc[common]
    prev_size = present_prev.value_counts()
    groups = cur.groupby(cur).groups                                  # new pid -> listing ids
    order = sorted(groups, key=lambda g: (-len(groups[g]), g))
    used, remap = set(), {}
    for g in order:
        members = [i for i in groups[g] if i in present_prev.index]
        if not members:
            continue
        counts = Counter(present_prev.loc[members])
        best_n = max(counts.values())
        cand = min(p for p, n in counts.items() if n == best_n)
        if cand not in used and best_n * 2 > prev_size[cand]:
            used.add(cand)
            if cand != g:
                remap[g] = cand
    taken = set(remap.values())
    clash = {g for g in groups if g in taken and g not in remap}       # a hash id equal to an inherited id
    for g in clash:                                                   # (cannot happen with distinct member sets,
        remap[g] = g + "x"                                            #  guarded anyway)
    if remap:
        frame = frame.assign(product_id=frame["product_id"].replace(remap))
        products = products.assign(product_id=products["product_id"].replace(remap))
    final = frame.dropna(subset=["product_id"]).drop_duplicates("id", keep="last").set_index("id")["product_id"]
    both = final.index.intersection(prev.index)
    same = int((final.loc[both] == prev.loc[both]).sum())
    split = sum(1 for p, ids in present_prev.groupby(present_prev).groups.items() if final.loc[list(ids)].nunique() > 1)
    merged = sum(1 for g, ids in final.loc[both].groupby(final.loc[both]).groups.items() if prev.loc[list(ids)].nunique() > 1)
    stats = {"status": "ok", "listings_in_both": int(len(both)), "listings_same_product": same,
             "share_same_product": round(same / len(both), 4) if len(both) else None,
             "products_carried": len(used), "products_new": int(products["product_id"].nunique() - len(used)),
             "previous_products_split": int(split), "products_merged": int(merged),
             "ids_changed_by_membership": len(remap)}
    return frame, products, stats
