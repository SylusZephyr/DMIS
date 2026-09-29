"""Data Quality Engine (Module 2).

Replaces manual checking with deterministic statistics:

* missing-value detection (per field, weighted by importance)
* duplicate detection (same native id, or same title+brand+price)
* rule checks (non-positive price, negative/impossible sales, rating
  outside range, revenue inconsistent with price*sales)
* outlier detection: IQR, robust z-score (median/MAD) and Isolation Forest

Each record gets ``data_confidence`` in 0..100 and a list of
``quality_issues``. Nothing is dropped: low-confidence records are
flagged (``usable_for_market`` = False) and stay visible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from dmie.engine.config import section

# Share of confidence lost when a field is missing.
FIELD_WEIGHTS = {"title": 25, "price": 15, "sales": 15, "brand": 5, "revenue": 5, "rating": 3, "image": 2}
ISSUE_PENALTIES = {
    "duplicate_record": 15,
    "content_duplicate": 5,
    "invalid_price": 30,
    "impossible_sales": 30,
    "invalid_rating": 10,
    "revenue_inconsistent": 10,
    "outlier_iqr": 5,
    "outlier_zscore": 8,
    "outlier_isolation_forest": 8,
}


@dataclass
class QualityReport:
    frame: pd.DataFrame                 # input + data_confidence, quality_issues, usable_for_market
    missing: dict[str, float]           # field -> share missing
    issue_counts: dict[str, int]
    dataset_confidence: float
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "records": int(len(self.frame)),
            "dataset_confidence": round(self.dataset_confidence, 1),
            "usable_for_market": int(self.frame["usable_for_market"].sum()) if len(self.frame) else 0,
            "missing_share": {k: round(v, 3) for k, v in self.missing.items()},
            "issue_counts": self.issue_counts,
            "notes": self.notes,
        }


def iqr_outliers(values: pd.Series, k: float) -> pd.Series:
    v = values.dropna()
    if len(v) < 8:
        return pd.Series(False, index=values.index)
    lv = np.log1p(v.clip(lower=0))  # market quantities are heavy-tailed; test in log space
    q1, q3 = lv.quantile([0.25, 0.75])
    iqr = q3 - q1
    if iqr == 0:
        return pd.Series(False, index=values.index)
    out = (lv < q1 - k * iqr) | (lv > q3 + k * iqr)
    return out.reindex(values.index, fill_value=False)


def robust_zscore_outliers(values: pd.Series, threshold: float) -> pd.Series:
    v = values.dropna()
    if len(v) < 8:
        return pd.Series(False, index=values.index)
    lv = np.log1p(v.clip(lower=0))
    med = lv.median()
    mad = (lv - med).abs().median()
    if mad == 0:
        return pd.Series(False, index=values.index)
    z = 0.6745 * (lv - med) / mad
    return (z.abs() > threshold).reindex(values.index, fill_value=False)


def isolation_forest_outliers(frame: pd.DataFrame, cols: list[str], contamination: float, seed: int = 0) -> pd.Series:
    data = frame[cols].copy()
    ok = data.notna().all(axis=1)
    result = pd.Series(False, index=frame.index)
    if ok.sum() < 20:
        return result
    X = np.log1p(data[ok].clip(lower=0).to_numpy(dtype=float))
    model = IsolationForest(contamination=contamination, random_state=seed, n_estimators=200)
    result.loc[ok] = model.fit_predict(X) == -1
    return result


def assess_quality(frame: pd.DataFrame) -> QualityReport:
    cfg = section("quality")
    df = frame.copy()
    n = len(df)
    issues: list[list[str]] = [[] for _ in range(n)]
    notes: list[str] = []

    def flag(mask: pd.Series, name: str) -> None:
        for i in np.flatnonzero(mask.to_numpy()):
            issues[i].append(name)

    missing = {f: float(df[f].isna().mean()) if n else 0.0 for f in FIELD_WEIGHTS}

    # duplicates: exact native-id repeats within a snapshot are the same row twice (excluded). Title+brand+price
    # repeats are excluded only when the row has no native id of its own; distinct ASINs with identical content
    # are separate listings (often variation children, each with its own sales) -- a listing is not a product, so
    # they stay, flagged, and product resolution groups them into one product.
    ts = df["timestamp"].astype(str)
    dup_id = df.assign(_ts=ts).duplicated(subset=["id", "_ts"], keep="first")
    dup_content = df.assign(_ts=ts, _t=df["title"].str.lower()).duplicated(subset=["_t", "brand", "price", "_ts"], keep="first") & df["title"].notna()
    no_native_id = (df["id"].isna() | (df["id"].astype(str) == df["record_id"].astype(str))) if "record_id" in df else df["id"].isna()
    flag(dup_id | (dup_content & no_native_id), "duplicate_record")
    flag(dup_content & ~dup_id & ~no_native_id, "content_duplicate")

    lo, hi = cfg.get("rating_range", [0, 5])
    flag(df["price"].notna() & ((df["price"] <= 0) | (df["price"] > cfg.get("max_price", 1e5))), "invalid_price")
    flag(df["sales"].notna() & ((df["sales"] < 0) | (df["sales"] > cfg.get("max_monthly_sales", 1e6))), "impossible_sales")
    flag(df["rating"].notna() & ((df["rating"] < lo) | (df["rating"] > hi)), "invalid_rating")

    if "_revenue_derived" in df:  # large-file ingestion supplies this as a column
        derived = df["_revenue_derived"].fillna(False).astype(bool)
    else:
        derived = df["attributes"].map(lambda a: bool(a.get("_revenue_derived")) if isinstance(a, dict) else False)
    ok = df["price"].notna() & df["sales"].notna() & df["revenue"].notna() & (df["revenue"] > 0) & ~derived
    rel = ((df["price"] * df["sales"] - df["revenue"]).abs() / df["revenue"]).where(ok)
    flag(rel > cfg.get("revenue_consistency_tolerance", 0.35), "revenue_inconsistent")

    k = cfg.get("iqr_multiplier", 3.0)
    z = cfg.get("zscore_threshold", 3.5)
    for col in ("price", "sales"):
        flag(iqr_outliers(df[col], k), "outlier_iqr")
        flag(robust_zscore_outliers(df[col], z), "outlier_zscore")
    flag(isolation_forest_outliers(df, ["price", "sales"], cfg.get("isolation_contamination", 0.05)), "outlier_isolation_forest")

    conf = np.full(n, 100.0)
    for f, w in FIELD_WEIGHTS.items():
        conf -= df[f].isna().to_numpy() * w
    for i, rec_issues in enumerate(issues):
        uniq = list(dict.fromkeys(rec_issues))
        issues[i] = uniq
        conf[i] -= sum(ISSUE_PENALTIES.get(x, 5) for x in uniq)
    conf = np.clip(conf, 0, 100)

    df["data_confidence"] = conf.round(1)
    df["quality_issues"] = issues
    min_conf = cfg.get("min_confidence_for_market", 40)
    df["usable_for_market"] = (
        (df["data_confidence"] >= min_conf)
        & df["title"].notna()
        & ~df["quality_issues"].map(lambda x: any(i in x for i in ("duplicate_record", "invalid_price", "impossible_sales")))
    )
    issue_counts: dict[str, int] = {}
    for rec_issues in issues:
        for x in rec_issues:
            issue_counts[x] = issue_counts.get(x, 0) + 1
    if n and df["sales"].isna().all():
        notes.append("no sales column -- demand metrics unavailable")
    dataset_conf = float(df["data_confidence"].mean()) if n else 0.0
    return QualityReport(df, missing, issue_counts, dataset_conf, notes)
