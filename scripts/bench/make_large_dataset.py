"""Generate a large, realistic benchmark dataset from the real SellerSprite exports.

Every row is a real listing from data/raw/ with a new ASIN, perturbed
price/sales/rating and a monthly snapshot date, keeping SellerSprite's
original (Chinese) headers so the real adapter and detector are exercised.
A small share of rows is deliberately broken (missing price, bad ASIN,
negative sales, duplicates) so the ingestion report has something to catch.

    python scripts/bench/make_large_dataset.py --rows 1000000 --out data/bench/sellersprite_1m.csv

Benchmark data only -- never written to data/raw/ (which stays immutable).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def build(rows: int, months: int, seed: int = 7) -> pd.DataFrame:
    frames = [pd.read_excel(p) for p in sorted((ROOT / "data" / "raw").glob("*/*_sellersprite.xlsx"))]
    base = pd.concat(frames, ignore_index=True).dropna(subset=["商品标题"]).reset_index(drop=True)
    rng = np.random.default_rng(seed)
    listings = max(1, rows // months)
    idx = rng.integers(0, len(base), listings)
    lst = base.iloc[idx].reset_index(drop=True)
    alphabet = np.array(list("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"))
    lst["ASIN"] = ["B0" + "".join(r) for r in alphabet[rng.integers(0, len(alphabet), (listings, 8))]]
    price = pd.to_numeric(lst["价格($)"], errors="coerce")
    lst["价格($)"] = (price * rng.uniform(0.85, 1.15, listings)).round(2)
    sales = pd.to_numeric(lst["子体销量"], errors="coerce")
    growth = rng.normal(0.01, 0.04, listings)
    out = []
    for m in range(months):
        f = lst.copy()
        s = (sales * (1 + growth) ** m * rng.uniform(0.8, 1.2, listings)).round()
        f["子体销量"] = s
        f["子体销售额($)"] = (s * f["价格($)"]).round(2)
        f["快照日期"] = pd.Timestamp("2025-01-01") + pd.DateOffset(months=m)
        out.append(f)
    df = pd.concat(out, ignore_index=True).head(rows)
    n = len(df)
    bad = rng.random(n)
    df.loc[bad < 0.01, "价格($)"] = np.nan                                         # missing price
    df.loc[(bad >= 0.01) & (bad < 0.015), "ASIN"] = "INVALID"                     # bad ASIN
    df.loc[(bad >= 0.015) & (bad < 0.017), "子体销量"] = -5                         # impossible sales
    dups = df.sample(frac=0.005, random_state=seed)                                 # exact duplicates
    return pd.concat([df, dups], ignore_index=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--out", default=str(ROOT / "data" / "bench" / "sellersprite_1m.csv"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df = build(a.rows, a.months)
    df.to_csv(out, index=False)
    print(f"{len(df):,} rows -> {out} ({out.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
