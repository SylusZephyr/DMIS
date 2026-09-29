"""How current each market's data is, and how far it is from the history the models need.

Per market, from the dataset registry (every processed upload):

* ``latest``: the latest snapshot date (the upload date when a file has none), and its age in days;
* ``stale``: the age exceeds ``freshness.stale_after_days`` (config/platform/operations.yaml); None (unknown)
  when no upload carries a snapshot date -- an upload date says nothing about the data's age;
* ``snapshots``: distinct snapshot periods; ``growth`` needs ``forecast.min_periods`` of them (a trend is only
  fitted from that many), ``seasonality`` needs ``freshness.seasonality_periods``;
* ``next``: what to do -- drop the next export into data/inbox/<market>/ (named with its date, e.g.
  ``2026-10-01.xlsx``) or upload it on Data Operations.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from dip.operations import inbox_dir, ops_config
from dip.storage import business as b


def _day(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _rel(p) -> str:
    from dip.settings import PROJECT_ROOT

    try:
        return str(p.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(p)


def report(visible: list[str] | None = None, today: date | None = None) -> list[dict]:
    from dip.metrics import config as metrics_config

    cfg = ops_config().get("freshness") or {}
    stale_after = int(cfg.get("stale_after_days", 35))
    season = int(cfg.get("seasonality_periods", 24))
    growth = int(metrics_config()["forecast"]["min_periods"])
    today = today or datetime.now(timezone.utc).date()
    with b.session() as s:
        names = [m.name for m in s.query(b.Market).all() if visible is None or m.name in visible]
        ds = [(d.market_name, d.snapshot_date, d.created_at) for d in s.query(b.Dataset).all() if d.market_name in names]
    out = []
    for m in sorted(names):
        rows = [(snap, created) for mk, snap, created in ds if mk == m]
        periods = sorted({(_day(snap) or _day(created)) for snap, created in rows} - {None})
        latest = periods[-1] if periods else None
        age = (today - latest).days if latest else None
        n = len(periods)
        dated = sum(1 for snap, _ in rows if snap)
        # an upload without a snapshot date says nothing about how old its data is
        stale = None if not dated else (age is None or age > stale_after)
        out.append({"market": m, "latest": latest.isoformat() if latest else None, "age_days": age, "stale": stale,
                    "snapshots": n, "dated": dated,
                    "growth": {"needed": growth, "have": n, "ready": n >= growth},
                    "seasonality": {"needed": season, "have": n, "ready": n >= season},
                    "inbox": _rel(inbox_dir() / m),
                    "next": ("drop the next export into the inbox (name it with its date) or upload it on Data Operations"
                             if stale is not False or n < growth else None)})
    return out
