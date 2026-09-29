"""Competitor watchlist (src/dip/watch.py): follow chosen listings, see their history and what moved."""

from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dip.api.util import clean
from dip.auth import Principal, assert_market, require
from dip.storage import lake

router = APIRouter()


class WatchIn(BaseModel):
    asin: str = Field(min_length=10, max_length=10)
    market: str | None = None
    label: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=2000)


def _by(p: Principal) -> str:
    return getattr(p, "email", None) or "local user"


@router.get("/watchlist")
def watchlist(market: str | None = None, principal: Principal = Depends(require("watchlist", "read"))):
    """Watched listings with their latest values, the changes worth reporting and recent observations."""
    from dip import watch

    scope = principal.market_scope()
    rows = watch.items(market)
    if scope is not None:
        rows = [r for r in rows if r["market_name"] is None or r["market_name"] in scope]
    return clean({"items": rows, "settings": {k: v for k, v in watch.config().items() if k != "budget_usd_per_run"}})


@router.post("/watchlist")
def watch_add(body: WatchIn, principal: Principal = Depends(require("watchlist", "write"))):
    from dip import watch

    if body.market is not None:
        try:
            lake.validate_market_name(body.market)
        except lake.InvalidMarketName as exc:
            raise HTTPException(400, str(exc)) from exc
        assert_market(principal, body.market)
    try:
        return clean(watch.add(body.asin, body.market, body.label, body.note, by=_by(principal)))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/watchlist/{item_id}")
def watch_remove(item_id: str, principal: Principal = Depends(require("watchlist", "write"))):
    from dip import watch

    if not watch.remove(item_id, by=_by(principal)):
        raise HTTPException(404, "not on the watchlist")
    return {"removed": True}


@router.get("/watchlist/{asin}/history")
def watch_history(asin: str, market: str | None = None, principal: Principal = Depends(require("watchlist", "read"))):
    from dip import watch

    if market is not None:
        assert_market(principal, market)
    try:
        pts = watch.history(watch._asin(asin), market)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return clean({"asin": asin.upper(), "market": market, "points": pts, "changes": watch.changes(pts)})


@router.post("/watchlist/refresh")
def watch_refresh(principal: Principal = Depends(require("watchlist", "write"))):
    """Re-observe every watched listing now with the Amazon detail provider (background; see /acquire/runs)."""
    from dip import acquire, watch

    if not acquire.status()["active"].get("amazon_detail"):
        raise HTTPException(409, "no Amazon detail provider is configured: set DIP_KEEPA_API_KEY (recommended) or a "
                                 "data-API key; the watchlist still updates from every processed snapshot")
    by = _by(principal)

    def work() -> None:
        try:
            watch.refresh(by=by)
        except Exception:  # noqa: BLE001 -- recorded on the run
            pass
    threading.Thread(target=work, daemon=True, name="watch-refresh").start()
    return {"status": "started"}
