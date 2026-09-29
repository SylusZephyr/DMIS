"""Own sales (src/dip/own_sales.py): Seller Central Business Reports as ground truth."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from dip.api.util import clean
from dip.auth import Principal, require

router = APIRouter()


@router.post("/own-sales")
def upload_own_sales(file: UploadFile = File(...), period_start: str = Form(...), period_end: str = Form(...),
                     principal: Principal = Depends(require("datasets", "write"))):
    """Import a Business Report ("Detail Page Sales and Traffic by Child Item") for the period it was exported for.
    The same ASIN and period imported again replaces the earlier numbers."""
    from dip import own_sales
    from dip.api.routes.operations import _save_upload

    path = _save_upload(file)
    try:
        return clean(own_sales.import_report(path, period_start, period_end, source_name=file.filename,
                                             by=getattr(principal, "email", None)))
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e)) from e


@router.get("/own-sales", dependencies=[Depends(require("projects", "read"))])
def list_own_sales(limit: int = Query(500, ge=1, le=5000)):
    from dip import own_sales

    return clean({"rows": own_sales.rows(limit)})


@router.get("/own-sales/estimate-check", dependencies=[Depends(require("projects", "read"))])
def own_sales_estimate_check():
    """Your actual monthly units against the market data's estimate of the same listing near the same time."""
    from dip import own_sales

    return clean(own_sales.estimate_check())
