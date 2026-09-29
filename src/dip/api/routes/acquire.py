"""Live data acquisition: provider status, market runs (background), run history and acquired review text."""

from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from dip.api.util import clean
from dip.auth import Principal, assert_market, require
from dip.storage import lake

router = APIRouter(tags=["acquisition"])


@router.get("/acquire/status", dependencies=[Depends(require("datasets", "read"))])
def acquire_status():
    from dip import acquire
    from dip.acquire.base import config

    st = acquire.status()
    rc = config()["run"]
    return {**st, "run": {k: rc[k] for k in ("max_listings_per_market", "reviews_listings", "reviews_per_listing",
                                                "history_months", "budget_usd_per_run")},
            "schedule_every_days": config()["schedule"]["every_days"]}


class RunIn(BaseModel):
    history: bool = False
    reviews: bool = True
    max_listings: int | None = None


@router.post("/acquire/markets/{market}/run")
def acquire_market(market: str, body: RunIn, principal: Principal = Depends(require("datasets", "write"))):
    """Start a live acquisition for a market in the background; poll /acquire/runs/{id}."""
    from dip import acquire
    from dip.acquire.run import create_run, run_market

    try:
        market = lake.validate_market_name(market)
    except lake.InvalidMarketName as exc:
        raise HTTPException(400, str(exc)) from exc
    assert_market(principal, market)
    if not acquire.status()["ready"]:
        raise HTTPException(409, "no Amazon provider is configured: set DIP_KEEPA_API_KEY (recommended) or a data-API "
                                 "key (DIP_SCRAPER_API_KEY + DIP_SCRAPER_API_URL); see config/platform/acquisition.yaml")
    by = getattr(principal, "email", None) or "local user"
    run_id = create_run(market, history=body.history, reviews=body.reviews, by=by, max_listings=body.max_listings, status="queued")

    def work() -> None:
        try:
            run_market(market, history=body.history, reviews=body.reviews, max_listings=body.max_listings, by=by, run_id=run_id)
        except Exception:  # noqa: BLE001 -- the failure is recorded on the run
            pass
    threading.Thread(target=work, daemon=True, name=f"acquire-{market}").start()
    return {"id": run_id, "status": "queued", "market": market}


@router.get("/acquire/runs", dependencies=[Depends(require("datasets", "read"))])
def acquire_runs(market: str | None = None, limit: int = Query(20, le=200)):
    from dip.acquire.run import runs

    return clean(runs(market, limit))


@router.get("/acquire/runs/{run_id}", dependencies=[Depends(require("datasets", "read"))])
def acquire_run(run_id: str):
    from dip.storage import business as b

    with b.session() as s:
        r = s.get(b.AcquisitionRun, run_id)
        if r is None:
            raise HTTPException(404, f"run '{run_id}' not found")
        return clean(b.row_dict(r))


@router.get("/markets/{market}/acquired-reviews")
def acquired_reviews(market: str, asin: str | None = None, limit: int = Query(100, le=1000),
                     principal: Principal = Depends(require("markets", "read"))):
    """Review text acquired live for a market (latest run), optionally for one listing."""
    from dip.acquire.run import acquired_reviews as load

    assert_market(principal, market)
    df = load(market, asin, limit)
    return {"market": market, "asin": asin, "reviews": clean(df), "total": int(len(df))}
