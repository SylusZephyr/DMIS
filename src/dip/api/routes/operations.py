"""Data operations: health, dataset upload -> job, job progress, ingestion reports."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from dip import __version__
from dip.api.util import clean
from dip.auth import Principal, require
from dip.pipeline import runner
from dip.settings import get_settings
from dip.storage import business as b
from dip.storage.graph import get_graph_store
from dip.storage.vectors import get_vector_store

router = APIRouter(tags=["operations"])
UPLOAD_EXT = {".xlsx", ".xls", ".csv", ".tsv", ".json", ".jsonl"}


@router.get("/health")
def health():
    return {"status": "ok", "version": __version__, "storage": get_settings().describe(),
            "graph": get_graph_store().stats(), "vectors": {"backend": get_vector_store().backend, "points": get_vector_store().count()}}


def _save_upload(f: UploadFile) -> Path:
    """Copy an upload to a private temp file, refusing it (413) past DIP_MAX_UPLOAD_MB and (400) past
    DIP_MAX_UPLOAD_ROWS lines for text formats (Excel/JSON rows are checked by ingestion)."""
    from dip.api.security import max_upload_bytes, max_upload_rows

    ext = Path(f.filename or "").suffix.lower()
    if ext not in UPLOAD_EXT:
        raise HTTPException(400, f"unsupported file type '{ext}'; use one of {sorted(UPLOAD_EXT)}")
    limit, rows_limit = max_upload_bytes(), max_upload_rows()
    path = Path(tempfile.mkdtemp(prefix="dip_upload_")) / (Path(f.filename).name or f"upload{ext}")
    size = lines = 0
    try:
        with path.open("wb") as out:
            while chunk := f.file.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"file too large: the limit is {limit // 2**20} MB (DIP_MAX_UPLOAD_MB)")
                lines += chunk.count(b"\n")
                out.write(chunk)
        if ext in (".csv", ".tsv", ".jsonl") and lines - 1 > rows_limit:
            raise HTTPException(400, f"too many rows: about {lines - 1:,}; the limit is {rows_limit:,} (DIP_MAX_UPLOAD_ROWS)")
        if size == 0:
            raise HTTPException(400, "the uploaded file is empty")
    except BaseException:
        shutil.rmtree(path.parent, ignore_errors=True)
        raise
    return path


def _parse_mapping(mapping: str | None) -> dict | None:
    """A confirmed column mapping from the import preview: JSON {universal field: source column}."""
    if not (mapping or "").strip():
        return None
    import json

    from dmie.engine.records import UNIVERSAL_FIELDS
    try:
        m = json.loads(mapping)
    except ValueError as exc:
        raise HTTPException(400, "mapping must be a JSON object {field: column}") from exc
    if not isinstance(m, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in m.items()):
        raise HTTPException(400, "mapping must be a JSON object {field: column}")
    unknown = sorted(set(m) - set(UNIVERSAL_FIELDS))
    if unknown:
        raise HTTPException(400, f"unknown field(s) in mapping: {', '.join(unknown)}")
    return m or None


@router.post("/imports/preview")
def import_preview(file: UploadFile = File(...), mapping: str | None = Form(None),
                   principal: Principal = Depends(require("datasets", "write"))):
    """Inspect a file before importing it (spec 64-66): detected column for every field with its confidence
    and alternatives, a mapped sample, and data-quality issues. Nothing is stored. Confirm by uploading the
    same file to POST /datasets with the (corrected) ``mapping``."""
    from dip.knowledge.import_preview import preview
    overrides = _parse_mapping(mapping)
    path = _save_upload(file)
    try:
        out = preview(path, overrides)
    except Exception as exc:  # unreadable file: say so instead of a 500
        raise HTTPException(400, f"could not read the file: {type(exc).__name__}: {exc}") from exc
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)
    if overrides:
        missing = sorted(c for c in overrides.values() if c not in {x["name"] for x in out["columns"]})
        if missing:
            raise HTTPException(400, f"mapping names column(s) not in the file: {', '.join(missing)}")
    return clean(out)


@router.post("/datasets")
def upload_dataset(file: UploadFile = File(...), market: str = Form(...), snapshot_date: str | None = Form(None),
                   marketplace: str | None = Form(None), reviews: UploadFile | None = File(None),
                   force: bool = Form(False), allow_duplicate: bool = Form(False), reason: str | None = Form(None),
                   mapping: str | None = Form(None),
                   principal: Principal = Depends(require("datasets", "write"))):
    """Upload any dataset; processing runs as a background job. Poll /jobs/{id}.
    An identical input (same file, corrections, suppliers, owners, config) is not recomputed unless force=true.
    Content already uploaded for another market or snapshot period is refused (409) unless
    allow_duplicate=true with a reason (recorded in the audit log)."""
    from dip.storage import lake
    try:
        market = lake.validate_market_name(market)
    except lake.InvalidMarketName as exc:
        raise HTTPException(400, str(exc)) from exc
    if allow_duplicate and not (reason or "").strip():
        raise HTTPException(400, "allow_duplicate=true requires a reason")
    overrides = _parse_mapping(mapping)
    from dip import tenancy
    org = principal.org_id
    try:
        tenancy.check_new_market(org, market)
        tenancy.check_records(org)
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except tenancy.LimitError as exc:
        raise HTTPException(402, str(exc)) from exc
    if not principal.may_see_market(market) and principal.role == "product_manager":
        raise HTTPException(403, f"market '{market}' is not assigned to you")
    path = _save_upload(file)
    snapshot_date = (snapshot_date or "").strip() or None
    if not allow_duplicate:
        try:
            b.check_duplicate(lake.content_hash(path), market, snapshot_date, org)
        except b.DuplicateContent as exc:
            shutil.rmtree(path.parent, ignore_errors=True)
            raise HTTPException(409, str(exc)) from exc
    rv = None
    if reviews is not None and reviews.filename:
        from dmie.engine.ingestion import read_table
        rv = read_table(_save_upload(reviews))
    job_id = runner.run_in_background(source=path, market=market, snapshot_date=snapshot_date,
                                      reviews=rv, source_name=file.filename, marketplace=marketplace or None, force=force,
                                      org_id=org, allow_duplicate=allow_duplicate, overrides=overrides,
                                      duplicate_reason=reason.strip() if allow_duplicate and reason else None)
    return {"job_id": job_id, "market": market}


@router.get("/jobs")
def jobs(limit: int = 20, principal: Principal = Depends(require("datasets", "read"))):
    scope = principal.market_scope()
    with b.session() as s:
        rows = s.query(b.Job).order_by(b.Job.created_at.desc()).limit(limit * 5 if scope is not None else limit).all()
        out = [b.row_dict(r) for r in rows if scope is None or r.market_name in scope]
    return clean(out[:limit])


@router.get("/jobs/{job_id}", dependencies=[Depends(require("datasets", "read"))])
def job(job_id: str):
    with b.session() as s:
        j = s.get(b.Job, job_id)
        if j is None:
            raise HTTPException(404, "job not found")
        out = b.row_dict(j)
        if j.dataset_id:
            d = s.get(b.Dataset, j.dataset_id)
            out["report"] = d.report if d else None
        return clean(out)


@router.get("/datasets")
def datasets(market: str | None = None, principal: Principal = Depends(require("datasets", "read"))):
    with b.session() as s:
        q = s.query(b.Dataset).order_by(b.Dataset.created_at.desc())
        if market:
            q = q.filter(b.Dataset.market_name == market)
        rows = [b.row_dict(r) for r in q.limit(500).all()]
    scope = principal.market_scope()
    return clean([r for r in rows if scope is None or r["market_name"] in scope][:200])


@router.get("/freshness")
def freshness(principal: Principal = Depends(require("datasets", "read"))):
    """Per market: latest snapshot and its age, whether it is stale, and how many dated snapshots exist
    against what growth trends and seasonality need."""
    from dip import freshness as fr

    scope = principal.market_scope()
    return clean(fr.report(None if scope is None else list(scope)))
