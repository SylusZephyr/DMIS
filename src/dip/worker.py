"""Durable job queue and worker.

With ``DIP_JOB_MODE=queue`` the API does not process uploads in a thread: it records a
``job_queue`` row and returns. One or more workers (``python scripts/dmis.py worker``, e.g. one
container per worker) claim jobs atomically from the business database and run the same pipeline,
so jobs survive API restarts and scale horizontally. Default mode ``thread`` keeps the embedded
single-process behaviour. A job whose worker died is re-queued after ``STALE_MINUTES``.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from dip.logging_setup import log_context
from dip.storage import business as b

log = logging.getLogger("dip.worker")
STALE_MINUTES = 360
MAX_ATTEMPTS = 3


def mode() -> str:
    return os.environ.get("DIP_JOB_MODE", "thread").strip().lower()


def _now():
    n = datetime.now(timezone.utc)
    return n.replace(tzinfo=None) if b.engine().dialect.name == "sqlite" else n


def _staging_dir(job_id: str) -> Path:
    """Queued inputs live under DIP_DATA_DIR (a volume every API and worker container mounts), not in the
    API's private temp dir, so a worker on another container/host can read them."""
    from dip.settings import get_settings

    d = Path(get_settings().data_dir) / "queue" / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def enqueue(job_id: str, kwargs: dict) -> str:
    payload = {k: v for k, v in kwargs.items() if k != "reviews"}
    src = Path(payload["source"])
    if src.is_file():
        staged = _staging_dir(job_id) / src.name
        shutil.copy2(src, staged)
        payload["source"] = str(staged)
    else:
        payload["source"] = str(src)
    rv = kwargs.get("reviews")
    if rv is not None and len(rv):
        path = _staging_dir(job_id) / "reviews.parquet"
        rv.astype(str).to_parquet(path, index=False)
        payload["reviews_path"] = str(path)
    with b.session() as s:
        q = b.QueuedJob(job_id=job_id, payload=payload)
        s.add(q)
        s.flush()
        return q.id


def claim(worker: str) -> b.QueuedJob | None:
    """Atomically take the oldest queued job (compare-and-set on status)."""
    from sqlalchemy import update

    with b.session() as s:
        stale = _now() - timedelta(minutes=STALE_MINUTES)
        s.execute(update(b.QueuedJob).where(b.QueuedJob.status == "running", b.QueuedJob.started_at < stale,
                                            b.QueuedJob.attempts < MAX_ATTEMPTS).values(status="queued"))
    for _ in range(5):
        with b.session() as s:
            cand = (s.query(b.QueuedJob).filter(b.QueuedJob.status == "queued").order_by(b.QueuedJob.created_at.asc()).first())
            if cand is None:
                return None
            res = s.execute(update(b.QueuedJob).where(b.QueuedJob.id == cand.id, b.QueuedJob.status == "queued")
                            .values(status="running", worker=worker, started_at=_now(), attempts=b.QueuedJob.attempts + 1))
            if res.rowcount == 1:
                s.flush()
                return s.get(b.QueuedJob, cand.id)
    return None


def run_one(worker: str | None = None) -> str | None:
    """Process one queued job; returns its id (None when the queue is empty)."""
    from dip.pipeline import runner

    worker = worker or f"{socket.gethostname()}:{os.getpid()}"
    q = claim(worker)
    if q is None:
        return None
    p = dict(q.payload)
    rv = pd.read_parquet(p.pop("reviews_path")) if p.get("reviews_path") else None
    status, err = "done", None
    with log_context(job_id=q.job_id):
        log.info("claimed queued job %s (attempt %s) on %s", q.id, q.attempts, worker)
        try:
            runner.process_dataset(source=Path(p.pop("source")), job_id=q.job_id, reviews=rv, **p)
        except Exception as exc:  # recorded on the Job too
            status, err = "failed", str(exc)[:2000]
            log.exception("queued job %s failed", q.id)
        else:
            log.info("queued job %s done", q.id)
    with b.session() as s:
        row = s.get(b.QueuedJob, q.id)
        row.status, row.error, row.finished_at = status, err, _now()
    from dip.settings import get_settings
    shutil.rmtree(Path(get_settings().data_dir) / "queue" / q.job_id, ignore_errors=True)   # staged inputs
    return q.id


def work(poll_seconds: float = 5.0, once: bool = False) -> int:
    n = 0
    while True:
        done = run_one()
        if done:
            n += 1
            continue
        if once:
            return n
        time.sleep(poll_seconds)


def stats() -> dict:
    from sqlalchemy import func

    with b.session() as s:
        rows = s.query(b.QueuedJob.status, func.count(b.QueuedJob.id)).group_by(b.QueuedJob.status).all()
    return {"mode": mode(), **{k: int(v) for k, v in rows}}
