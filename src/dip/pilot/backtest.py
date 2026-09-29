"""Phase 3 backtests: were past predictions right?

1. Forecasts   -- rolling origin: fit on periods 1..t, predict t+1, compare with the actual.
                  WAPE, median APE, direction accuracy (the platform's growth labels).
2. Change alerts -- a market move >= operations.yaml ``market_move`` would raise market.size_change;
                  "real" = the next period stays on the same side of the pre-move level.
3. Launch estimates (pseudo-launch) -- products launched shortly before the snapshot are treated as
                  launches: the simulator estimates their monthly units from the *other* products
                  (the product itself removed), and the estimate is compared with observed sales.
                  Caveats (reported): segment aggregates still include the product; only products
                  still listed are seen (survivorship); the simulator models a new entrant ramp.

Series come from dated records (a timestamp column) or from the platform's per-upload history.
Fewer than ``min_periods + 1`` periods -> "insufficient history" (nothing is extrapolated).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import yaml

from dip.settings import PROJECT_ROOT
from dip.storage import business as b
from dip.storage import lake


def _history_series(market: str) -> dict[str, pd.Series]:
    from dmie.engine.forecast import build_series

    rec = lake.read_curated("records", market)
    if len(rec) and rec["timestamp"].notna().any():
        keep = rec["accepted"].fillna(False).astype(bool) & rec["is_relevant"].fillna(False).astype(bool)
        r = rec[keep].copy()
        if "revenue" not in r or r["revenue"].isna().all():
            r["revenue"] = pd.to_numeric(r["price"], errors="coerce") * pd.to_numeric(r["sales"], errors="coerce")
        return build_series(r, "revenue", "segment_id")
    from dip.pipeline.runner import _analytics_con
    from dmie.engine import store as core_store

    con = _analytics_con()
    try:
        h = core_store.read_history(con, market)
    finally:
        con.close()
    if h.empty:
        return {}
    h["period"] = pd.to_datetime(h["period"])
    return {str(k): g.set_index("period")["revenue"].astype(float) for k, g in h.groupby("subject")}


def forecast_backtest(market: str) -> dict:
    from dip.pipeline.forecasting import label
    from dmie.engine.forecast import forecast_series
    from dmie.engine.config import section

    min_p = section("forecast").get("min_periods", 3)
    series = _history_series(market)
    rows = []
    for subject, s in series.items():
        s = s.dropna().sort_index()
        for i in range(min_p, len(s)):
            fc = forecast_series(s.iloc[:i], subject, "revenue")
            if fc.status != "ok" or not fc.forecast:
                continue
            pred, actual, last = float(fc.forecast[0][1]), float(s.iloc[i]), float(s.iloc[i - 1])
            rows.append({"subject": subject, "period": str(s.index[i])[:10], "predicted": pred, "actual": actual,
                         "last": last,
                         "label_pred": label(pred / last - 1) if last > 0 else None,
                         "label_actual": label(actual / last - 1) if last > 0 else None})
    periods = max((len(s.dropna()) for s in series.values()), default=0)
    if not rows:
        return {"status": "insufficient_history", "periods": periods, "needed": min_p + 1,
                "note": f"{periods} period(s) available; forecast backtests need at least {min_p + 1}"}
    df = pd.DataFrame(rows)
    err = (df["predicted"] - df["actual"]).abs()
    ape = err / df["actual"].where(df["actual"] > 0)
    lab = df.dropna(subset=["label_pred", "label_actual"])
    from dip.pilot.metrics import wilson

    return {"status": "ok", "periods": periods, "points": int(len(df)),
            "wape": float(err.sum() / df["actual"].abs().sum()) if df["actual"].abs().sum() else None,
            "median_ape": float(ape.median()) if ape.notna().any() else None,
            "market_wape": _wape(df[df["subject"] == "__market__"]),
            "direction_accuracy": wilson(int((lab["label_pred"] == lab["label_actual"]).sum()), len(lab)),
            "points_detail": df.round(2).to_dict("records")[:200]}


def _wape(df: pd.DataFrame):
    if df.empty or not df["actual"].abs().sum():
        return None
    return float((df["predicted"] - df["actual"]).abs().sum() / df["actual"].abs().sum())


def alert_backtest(market: str) -> dict:
    from dip.pilot.metrics import wilson

    thr = yaml.safe_load((PROJECT_ROOT / "config" / "platform" / "operations.yaml").read_text(encoding="utf-8"))
    move = float((thr.get("changes") or {}).get("market_move", 0.10))
    s = _history_series(market).get("__market__")
    if s is None or len(s.dropna()) < 3:
        return {"status": "insufficient_history", "periods": 0 if s is None else int(len(s.dropna())), "needed": 3}
    s = s.dropna().sort_index()
    moves = []
    for i in range(1, len(s) - 1):
        prev, cur, nxt = float(s.iloc[i - 1]), float(s.iloc[i]), float(s.iloc[i + 1])
        if prev <= 0:
            continue
        ch = cur / prev - 1
        if abs(ch) >= move:
            real = (nxt - prev) * np.sign(ch) > 0
            moves.append({"period": str(s.index[i])[:10], "change": round(ch, 4), "next_vs_before": round(nxt / prev - 1, 4),
                          "persisted": bool(real)})
    return {"status": "ok", "threshold": move, "alerts": len(moves),
            "persisted": wilson(sum(m["persisted"] for m in moves), len(moves)), "detail": moves}


def launch_backtest(market: str, window_days: int = 180, min_age_days: int = 30, limit: int = 60) -> dict:
    from dip.pilot.metrics import wilson
    from dmie.engine.simulation import SimulationInput, simulate

    products = lake.read_curated("products", market)
    listings = lake.read_curated("listings", market)
    if products.empty or "launch_date" not in listings:
        return {"status": "no_data"}
    launch = pd.to_datetime(listings["launch_date"], errors="coerce").groupby(listings["product_id"]).min()
    with b.session() as s:
        m = s.get(b.Market, market)
        ds = s.get(b.Dataset, m.dataset_id) if m and m.dataset_id else None
        snap = ds.snapshot_date if ds else None
    if snap:
        as_of, basis = pd.Timestamp(snap), "declared snapshot date"
    elif launch.notna().any():
        as_of, basis = launch.max(), "latest launch date in the export (no snapshot date declared)"
    else:
        return {"status": "no_launch_dates", "note": "the source has no usable launch dates"}
    p = products.assign(launched=products["product_id"].map(launch))
    p["units"] = pd.to_numeric(p["monthly_sales"], errors="coerce")
    new = p[(p["launched"] > as_of - pd.Timedelta(days=window_days)) & (p["launched"] <= as_of - pd.Timedelta(days=min_age_days))]
    eligible = new[new["units"].notna() & (new["units"] > 0) & pd.to_numeric(new["price"], errors="coerce").notna()]
    rows = []
    for _, r in eligible.sort_values("launched").head(limit).iterrows():
        rest = products[products["product_id"] != r["product_id"]]
        sim = simulate(rest, SimulationInput(str(r["title"]), float(r["price"]), 0.0, "", r["segment_id"], 0.15, 0.0)).to_dict()
        q = sim.get("units_p10_p50_p90")
        if not q:
            continue
        rows.append({"product_id": r["product_id"], "title": str(r["title"])[:80], "launched": str(r["launched"])[:10],
                     "actual": float(r["units"]), "p10": float(q[0]), "p50": float(q[1]), "p90": float(q[2])})
    out = {"as_of": str(as_of)[:10], "as_of_basis": basis, "window_days": window_days, "min_age_days": min_age_days,
           "launched_in_window": int(len(new)), "with_observed_sales": int(len(eligible)), "evaluated": len(rows),
           "caveats": ["segment aggregates still include the evaluated product", "only products still listed are observed",
                       "simulator applies a new-entrant ramp (mean 40 % of comparable demand)"]}
    if not rows:
        return {"status": "insufficient_data", **out}
    df = pd.DataFrame(rows)
    inside = ((df["actual"] >= df["p10"]) & (df["actual"] <= df["p90"])).sum()
    ratio = np.log(df["actual"] / df["p50"].clip(lower=1e-9))
    return {"status": "ok", **out,
            "interval_p10_p90_coverage": wilson(int(inside), len(df)),
            "wape_p50": float((df["p50"] - df["actual"]).abs().sum() / df["actual"].sum()),
            "median_actual_over_p50": float(np.exp(ratio.median())),
            "detail": df.round(1).to_dict("records")}


def run(market: str) -> dict:
    return {"market": market, "forecasts": forecast_backtest(market), "alerts": alert_backtest(market),
            "launch": launch_backtest(market)}


def _pct(m):
    if isinstance(m, dict):
        return "—" if m.get("value") is None else f"{m['value']:.0%} ({m['low']:.0%}–{m['high']:.0%}, n={m['n']})"
    return "—" if m is None else f"{m:.0%}"


def _ratio_fmt(v):
    return "—" if v is None else f"{v:.2f}×"


def to_markdown(results: list[dict]) -> str:
    L = ["# Backtest report (Phase 3)", "", "Generated by `python scripts/pilot.py backtest`. Method: `src/dip/pilot/backtest.py`.", "",
         "## Forecasts and change alerts", "",
         "| Market | Periods | Forecast points | WAPE | Market WAPE | Direction accuracy | Alerts (≥ threshold) | Alerts that persisted |",
         "|---|---|---|---|---|---|---|---|"]
    for r in results:
        f, a = r["forecasts"], r["alerts"]
        if f["status"] != "ok":
            L.append(f"| {r['market']} | {f.get('periods', 0)} | — | insufficient history (need ≥ {f.get('needed')}) | — | — | "
                     f"{'—' if a['status'] != 'ok' else a['alerts']} | {'—' if a['status'] != 'ok' else _pct(a['persisted'])} |")
        else:
            L.append(f"| {r['market']} | {f['periods']} | {f['points']} | {_pct(f['wape'])} | {_pct(f['market_wape'])} | "
                     f"{_pct(f['direction_accuracy'])} | {a.get('alerts', '—')} | {_pct(a.get('persisted')) if a['status'] == 'ok' else '—'} |")
    L += ["", "## Launch estimates (pseudo-launches)", "",
          "| Market | As of (basis) | Launched in window | With observed sales | Evaluated | Actual within p10–p90 | WAPE of p50 | Median actual / p50 |",
          "|---|---|---|---|---|---|---|---|"]
    for r in results:
        x = r["launch"]
        if x.get("status") in ("no_data", "no_launch_dates"):
            L.append(f"| {r['market']} | {x.get('note', 'no data')} | | | | | | |")
            continue
        L.append(f"| {r['market']} | {x['as_of']} ({x['as_of_basis']}) | {x['launched_in_window']} | {x['with_observed_sales']} | "
                 f"{x['evaluated']} | {_pct(x.get('interval_p10_p90_coverage'))} | {_pct(x.get('wape_p50'))} | "
                 f"{_ratio_fmt(x.get('median_actual_over_p50'))} |")
    L += ["", "Caveats for launch estimates: " + "; ".join(results[0]["launch"].get("caveats", [])) + "." if results else "", ""]
    return "\n".join(L)
