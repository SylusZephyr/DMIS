"""Keyword intelligence (src/dip/keywords.py): import a keyword export, read keyword demand, gaps and sub-categories."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from dip.api.routes.catalog import _market
from dip.api.util import clean
from dip.auth import Principal, assert_market, require

router = APIRouter()


@router.post("/markets/{market}/keywords")
def upload_keywords(market: str, file: UploadFile = File(...), principal: Principal = Depends(require("datasets", "write"))):
    """Import a keyword export (CSV / Excel; e.g. SellerSprite keyword research or reverse-ASIN). Replaces the
    market's keyword list; returns the header mapping, notes and the per-sub-category summary."""
    _market(market)
    assert_market(principal, market)
    from dip import keywords
    from dip.api.routes.operations import _save_upload

    path = _save_upload(file)
    try:
        out = keywords.import_keywords(market, path, source_name=file.filename, by=getattr(principal, "email", None))
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e)) from e
    return clean(out)


@router.get("/markets/{market}/keywords/summary")
def keyword_summary(market: str, principal: Principal = Depends(require("markets", "read"))):
    _market(market)
    assert_market(principal, market)
    from dip import keywords

    return clean(keywords.summary(market))


@router.get("/markets/{market}/keywords")
def keyword_rows(market: str, segment: str | None = None, gaps: bool = False, q: str | None = Query(None, max_length=200),
                 sort: str = "score", desc: bool = True, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                 principal: Principal = Depends(require("markets", "read"))):
    """Keywords with their demand, competition, score (0-100, with components and coverage), gap flag and
    sub-category (``segment=unassigned`` lists the ones no sub-category could be matched to)."""
    _market(market)
    assert_market(principal, market)
    from dip import keywords

    return clean(keywords.rows(market, segment, gaps, q, sort, desc, limit, offset))
