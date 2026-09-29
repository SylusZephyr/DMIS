"""Employee Ownership System (Module 13).

Links people to the markets they are responsible for, from two real
sources only:

1. the ownership CSV (config: ownership.ownership_csv) -- employee ->
   raw category labels (e.g. 倪政 -> 医师椅);
2. owner columns inside uploaded datasets (e.g. SellerSprite's 产品经理),
   kept in each record's ``attributes``.

An engine market is linked to an employee when a dataset names them, or
when one of the market's category labels matches a label they own.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd

from dmie.engine.config import PROJECT_ROOT, section


def _attr_values(records: pd.DataFrame, fields: list[str]) -> Counter:
    c: Counter = Counter()
    for a in records.get("attributes", pd.Series(dtype=object)):
        if not isinstance(a, dict):
            continue
        for f in fields:
            v = a.get(f)
            if isinstance(v, str) and v.strip():
                c[v.strip()] += 1
    return c


def market_owner_hints(records: pd.DataFrame) -> dict:
    """Owner names and human category labels found in a dataset."""
    cfg = section("ownership")
    owners = _attr_values(records, cfg.get("owner_fields", []))
    labels = _attr_values(records, cfg.get("category_label_fields", []))
    return {"owners": dict(owners.most_common()), "category_labels": dict(labels.most_common(20))}


def load_ownership_table(path: str | Path | None = None) -> pd.DataFrame:
    cfg = section("ownership")
    path = Path(path or PROJECT_ROOT / cfg.get("ownership_csv", "data/raw/employee_category_ownership.csv"))
    cols = ["employee", "category_label", "listing_count", "source"]
    if not path.exists():
        return pd.DataFrame(columns=cols)
    raw = pd.read_csv(path, encoding="utf-8-sig")
    out = pd.DataFrame({
        "employee": raw["product_manager"].astype(str).str.strip(),
        "category_label": raw["raw_category_label"].astype(str).str.strip(),
        "listing_count": pd.to_numeric(raw.get("listing_count"), errors="coerce"),
        "source": path.name,
    })
    return out[out["employee"] != ""].reset_index(drop=True)


def link_markets(ownership: pd.DataFrame, markets: pd.DataFrame) -> pd.DataFrame:
    """markets: [market_name, owners(dict), category_labels(dict)] -> employee/market links."""
    rows = []
    for _, m in markets.iterrows():
        owners = m.get("owners") or {}
        labels = set((m.get("category_labels") or {}).keys()) | {m["market_name"]}
        for emp, n in owners.items():
            rows.append({"employee": emp, "market_name": m["market_name"], "basis": f"named in dataset ({n} records)"})
        if len(ownership):
            hit = ownership[ownership["category_label"].isin(labels)]
            for _, h in hit.iterrows():
                rows.append({"employee": h["employee"], "market_name": m["market_name"],
                             "basis": f"owns category '{h['category_label']}'"})
    df = pd.DataFrame(rows, columns=["employee", "market_name", "basis"])
    return df.drop_duplicates(["employee", "market_name"]).reset_index(drop=True)
