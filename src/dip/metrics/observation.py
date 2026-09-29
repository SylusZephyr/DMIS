"""How was a sales figure observed? Turns a sales column into per-listing observation intervals.

* Badge data (values only on the marketplace badge ladder, none below the first rung):
  v -> [v, next rung);  missing -> [0, first rung)   ("no badge" = below the threshold)
* Exact data (anything else): v -> [v, v];  missing -> unknown (no information)

The decision is made from the data itself (share of values on the ladder), never from the file
name or category, and is reported so a reader can check it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dip.metrics import config


@dataclass
class Observation:
    kind: str                     # "badge" | "exact"
    lower: np.ndarray             # inclusive lower bound (units / month)
    upper: np.ndarray             # exclusive upper bound; == lower for exact; inf for unknown
    known: np.ndarray             # a value was present in the source
    evidence: dict

    @property
    def bounded(self) -> np.ndarray:
        """Observations that carry information (anything but fully unknown)."""
        return np.isfinite(self.upper)


def classify(sales: pd.Series) -> tuple[str, dict]:
    cfg = config()["sales_observation"]
    ladder = np.array(cfg["badge_ladder"], dtype=float)
    v = pd.to_numeric(sales, errors="coerce").dropna()
    v = v[v >= 0]
    ev = {"observed": int(len(v)), "missing": int(sales.isna().sum()), "ladder_first": float(ladder[0])}
    if len(v) < cfg["detect_min_observed"]:
        return "exact", {**ev, "reason": f"only {len(v)} observed values -- too few to detect a badge ladder"}
    on = np.isin(v.to_numpy(), ladder)
    share = float(on.mean())
    below = int((v < ladder[0]).sum())
    ev.update(on_ladder_share=round(share, 4), below_first_rung=below)
    if share >= cfg["detect_min_share"] and below == 0:
        return "badge", {**ev, "reason": f"{share:.1%} of observed values are badge rungs and none is below "
                                          f"{ladder[0]:.0f}: values are 'N+ bought in past month' badges"}
    return "exact", {**ev, "reason": f"only {share:.1%} of values are badge rungs -- treated as exact figures"}


def observe(sales: pd.Series, kind: str | None = None) -> Observation:
    cfg = config()["sales_observation"]
    ladder = np.array(cfg["badge_ladder"], dtype=float)
    detected, ev = classify(sales)
    kind = kind or detected
    x = pd.to_numeric(sales, errors="coerce").to_numpy(dtype=float)
    known = ~np.isnan(x)
    if kind == "badge":
        idx = np.searchsorted(ladder, np.where(known, x, 0), side="right") - 1
        idx = np.clip(idx, 0, len(ladder) - 1)
        lo = np.where(known, ladder[idx], 0.0)
        nxt = np.where(idx + 1 < len(ladder), ladder[np.minimum(idx + 1, len(ladder) - 1)], ladder[-1] * cfg["top_rung_multiplier"])
        up = np.where(known, nxt, ladder[0])
        # off-ladder values (rare, tolerated by detect_min_share) are kept as exact points
        off = known & ~np.isin(x, ladder)
        lo = np.where(off, x, lo)
        up = np.where(off, x, up)
    else:
        lo = np.where(known, x, 0.0)
        up = np.where(known, x, np.inf)
    return Observation(kind, lo, up, known, {**ev, "kind": kind})
