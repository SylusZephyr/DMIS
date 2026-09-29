"""Tenancy and commercial readiness.

* **Organizations** (tenants): markets, suppliers, datasets and users carry ``org_id``;
  NULL means the default organization. A signed-in user sees only their organization's
  markets and suppliers. While no second organization exists, nothing changes for anyone.
* **Plans** (config/platform/plans.yaml): per-plan limits (markets, users, records per month)
  and feature flags (projects, connectors, analyst_ai, reports, api), enforced by the API.
* **Usage metering**: records ingested, datasets processed, analyst questions and AI calls
  are recorded per organization and month (``usage`` table) -- the basis for billing later.
No payment provider is integrated; an admin assigns plans.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

import yaml
from sqlalchemy import func

from dip.settings import PROJECT_ROOT
from dip.storage import business as b

DEFAULT = b.DEFAULT_ORG


class LimitError(Exception):
    """A plan limit or feature flag blocks the action (HTTP 402)."""


@lru_cache(maxsize=1)
def plans_config() -> dict:
    return yaml.safe_load((PROJECT_ROOT / "config" / "platform" / "plans.yaml").read_text(encoding="utf-8"))


def norm(org_id: str | None) -> str:
    return org_id or DEFAULT


def multi_tenant() -> bool:
    with b.session() as s:
        return s.query(b.Organization).filter(b.Organization.id != DEFAULT).count() > 0


def org_markets(org_id: str | None) -> set[str]:
    org = norm(org_id)
    with b.session() as s:
        rows = s.query(b.Market.name, b.Market.org_id).all()
    return {n for n, o in rows if norm(o) == org}


def market_org(market: str) -> str:
    with b.session() as s:
        m = s.get(b.Market, market)
        return norm(m.org_id if m else None)


def plan_of(org_id: str | None) -> tuple[str, dict]:
    cfg = plans_config()
    name = cfg["default_plan"]
    with b.session() as s:
        o = s.get(b.Organization, norm(org_id))
        if o is not None:
            name = o.plan
    return name, cfg["plans"][name]


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.replace(tzinfo=None) if b.engine().dialect.name == "sqlite" else start


def usage(org_id: str | None, metric: str) -> float:
    with b.session() as s:
        v = (s.query(func.coalesce(func.sum(b.UsageRecord.quantity), 0.0))
             .filter(b.UsageRecord.org_id == norm(org_id), b.UsageRecord.metric == metric,
                     b.UsageRecord.at >= _month_start()).scalar())
    return float(v or 0)


def meter(org_id: str | None, metric: str, quantity: float = 1.0, ref: str | None = None) -> None:
    with b.session() as s:
        s.add(b.UsageRecord(org_id=norm(org_id), metric=metric, quantity=float(quantity), ref=(ref or "")[:255] or None))


def has_feature(org_id: str | None, feature: str) -> bool:
    return feature in (plan_of(org_id)[1].get("features") or [])


def require_feature(org_id: str | None, feature: str) -> None:
    if not has_feature(org_id, feature):
        name, _ = plan_of(org_id)
        raise LimitError(f"the '{name}' plan does not include '{feature}'")


def check_new_market(org_id: str | None, market: str) -> None:
    """Uploading to a new market: the name must be free (or already this org's) and within the plan."""
    with b.session() as s:
        m = s.get(b.Market, market)
        if m is not None:
            if norm(m.org_id) != norm(org_id):
                raise PermissionError(f"market name '{market}' belongs to another organization; choose another name")
            return
    limit = plan_of(org_id)[1].get("markets")
    if limit is not None and len(org_markets(org_id)) >= limit:
        raise LimitError(f"plan limit reached: {limit} markets")


def check_records(org_id: str | None, incoming: int = 0) -> None:
    limit = plan_of(org_id)[1].get("records_per_month")
    if limit is not None and usage(org_id, "records_ingested") + incoming > limit:
        raise LimitError(f"plan limit reached: {limit:,} records per month")


def check_new_user(org_id: str | None) -> None:
    limit = plan_of(org_id)[1].get("users")
    if limit is None:
        return
    with b.session() as s:
        n = sum(1 for (o,) in s.query(b.User.org_id).all() if norm(o) == norm(org_id))
    if n >= limit:
        raise LimitError(f"plan limit reached: {limit} users")


def summary(org_id: str | None) -> dict:
    name, plan = plan_of(org_id)
    with b.session() as s:
        o = s.get(b.Organization, norm(org_id))
        users = sum(1 for (x,) in s.query(b.User.org_id).all() if norm(x) == norm(org_id))
    metrics = ("records_ingested", "datasets_processed", "analyst_questions", "ai_calls")
    return {"org_id": norm(org_id), "name": o.name if o else "Default organization", "plan": name,
            "status": o.status if o else "active", "limits": {k: plan.get(k) for k in ("markets", "users", "records_per_month")},
            "features": plan.get("features") or [],
            "usage_this_month": {m: usage(org_id, m) for m in metrics},
            "counts": {"markets": len(org_markets(org_id)), "users": users}}
