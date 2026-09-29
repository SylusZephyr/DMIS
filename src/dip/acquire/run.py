"""A market acquisition run: Amazon now (and, optionally, the past 12 months) as dated snapshots.

1. **Search** the market's ``search_terms`` (config/categories.yaml) with the amazon_search provider.
2. **Track** listings the market already knows (so a listing that drops out of search is still observed).
3. **Details** for the union (capped at ``run.max_listings_per_market``) from the amazon_detail provider;
   search-page fields fill whatever the detail source lacks.
4. **Reviews**: text for the top-selling listings from the amazon_reviews provider (when one is configured).
5. **History backfill** (``history=True`` with an amazon_history provider): one snapshot per past month end,
   rebuilt from price / sales-badge / rating / review-count histories.
6. Each snapshot is written as ``<data_dir>/acquired/<market>/<date>.csv`` (+ ``<date>.reviews.csv``) and
   processed by ``runner.process_dataset`` with its snapshot date -- oldest first, so growth, seasonality and
   change alerts see a real time series. The run, its providers, counts and cost are one ``acquisition_runs`` row.

A provider error or the run budget stops the step it happens in; what was already fetched is still processed,
and the run is marked ``partial`` with the reason.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from dip.acquire.base import AcquireError, BudgetExceeded, Ledger, config
from dip.acquire.models import Listing, Review, history_snapshots, to_review_frame, to_snapshot_frame
from dip.settings import get_settings
from dip.storage import business as b
from dip.storage import lake


def search_terms(market: str) -> list[str]:
    from dip.pipeline.scope import definitions

    d = definitions().get(market) or {}
    terms = d.get("search_terms") or ([d["leaf_category_en"]] if d.get("leaf_category_en") else [market.replace("_", " ")])
    return [str(t) for t in terms]


def known_asins(market: str) -> list[str]:
    if not lake.has_curated("listings", market):
        return []
    df = lake.read_curated("listings", market, columns=["id"])
    return [str(x) for x in df["id"].dropna().astype(str) if len(str(x)) == 10]


def snapshot_dir(market: str) -> Path:
    d = get_settings().data_dir / "acquired" / lake.validate_market_name(market)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _collect(market: str, ledger: Ledger, *, history: bool, reviews: bool, today: date, provider_kw: dict,
             max_listings: int | None = None) -> dict:
    from dip.acquire import provider_for
    from dip.acquire.amazon.keepa import Keepa, month_ends

    rc = config()["run"]
    limit = int(max_listings or rc["max_listings_per_market"])
    used: dict[str, str | None] = {}
    notes: list[str] = []
    searcher = provider_for("amazon_search", ledger, **provider_kw)
    detailer = provider_for("amazon_detail", ledger, **provider_kw)
    used["amazon_search"], used["amazon_detail"] = (searcher.name if searcher else None), (detailer.name if detailer else None)
    if searcher is None or detailer is None:
        raise AcquireError("no Amazon provider configured: set DIP_KEEPA_API_KEY (recommended) or DIP_SCRAPER_API_KEY + "
                           "DIP_SCRAPER_API_URL, or PA-API credentials (see config/platform/acquisition.yaml)")

    # 1-2. search + tracked listings
    seen: dict[str, Listing] = {}
    order: list[str] = []
    terms = search_terms(market)
    try:
        for term in terms:
            for page in range(int(rc["search_pages_per_term"])):
                if hasattr(searcher, "search_listings"):
                    found = searcher.search_listings(term, page + 1)
                    asins = [x.asin for x in found]
                    for x in found:
                        seen[x.asin] = seen[x.asin].merge(x) if x.asin in seen else x
                else:
                    asins = searcher.search(term, page)
                if not asins:
                    break
                order += [a for a in asins if a not in order]
    except BudgetExceeded as exc:
        notes.append(str(exc))
    except AcquireError as exc:
        notes.append(f"search stopped: {exc}")
    tracked = known_asins(market) if rc["track_existing_listings"] else []
    wanted = (order + [a for a in tracked if a not in order])[:limit]

    # 3. details (Keepa: keep the raw products for history)
    listings: dict[str, Listing] = {}
    raw_products: list[dict] = []
    try:
        if isinstance(detailer, Keepa):
            raw_products = detailer.products(wanted)
            got = [detailer.listing(p) for p in raw_products if p.get("asin")]
        else:
            got = detailer.details(wanted)
        for x in got:
            listings[x.asin] = x.merge(seen[x.asin]) if x.asin in seen else x
    except BudgetExceeded as exc:
        notes.append(str(exc))
    except AcquireError as exc:
        notes.append(f"details stopped: {exc}")
    for a, x in seen.items():                     # search-only observations still count
        listings.setdefault(a, x)

    # 4. reviews for the best sellers
    revs: list[Review] = []
    reviewer = provider_for("amazon_reviews", ledger, **provider_kw) if reviews else None
    used["amazon_reviews"] = reviewer.name if reviewer else None
    if reviewer is not None:
        top = sorted(listings.values(), key=lambda x: (x.sales or 0, x.reviews or 0), reverse=True)[:int(rc["reviews_listings"])]
        for x in top:
            try:
                revs += reviewer.reviews(x.asin, int(rc["reviews_per_listing"]))
            except BudgetExceeded as exc:
                notes.append(str(exc))
                break
            except AcquireError as exc:
                notes.append(f"reviews for {x.asin}: {exc}")
                if "robot check" in str(exc):
                    break
    elif reviews:
        notes.append("no review provider configured: customer pain stays rating-based")

    # 5. history
    months: dict[date, pd.DataFrame] = {}
    if history:
        hp = provider_for("amazon_history", ledger, **provider_kw)
        used["amazon_history"] = hp.name if hp else None
        if hp is None:
            notes.append("history backfill needs a history provider (Keepa)")
        else:
            if not raw_products:
                try:
                    raw_products = hp.products(list(listings))
                except AcquireError as exc:
                    notes.append(f"history stopped: {exc}")
            ends = month_ends(int(rc["history_months"]), today)
            points = [pt for p in raw_products if p.get("asin") for pt in Keepa.history(p, ends)]
            months = history_snapshots(points, listings)
    return {"terms": terms, "tracked": len(tracked), "listings": list(listings.values()), "reviews": revs,
            "months": months, "providers": used, "notes": notes}


def run_market(market: str, *, history: bool = False, reviews: bool = True, process: bool = True,
               today: date | None = None, ledger: Ledger | None = None, provider_kw: dict | None = None,
               max_listings: int | None = None, by: str | None = None, run_id: str | None = None) -> dict:
    """Acquire one market now; returns the run record (also stored in ``acquisition_runs``).
    ``run_id``: an already created (queued) run to fill in, as the API does for background runs."""
    from dip.pipeline import runner

    market = lake.validate_market_name(market)
    today = today or datetime.now(timezone.utc).date()
    ledger = ledger or Ledger()
    run_id = run_id or create_run(market, history=history, reviews=reviews, by=by, max_listings=max_listings)
    with b.session() as s:
        s.get(b.AcquisitionRun, run_id).status = "running"
    status, error, result, providers = "done", None, {}, None
    try:
        got = _collect(market, ledger, history=history, reviews=reviews, today=today, provider_kw=provider_kw or {},
                       max_listings=max_listings)
        providers = got["providers"]
        out = snapshot_dir(market)
        snap = to_snapshot_frame(got["listings"])
        rev = to_review_frame(got["reviews"])
        files: list[dict] = []
        for m, frame in got["months"].items():               # oldest first, before today's snapshot
            if m >= today or frame.empty:
                continue
            p = out / f"{m.isoformat()}.history.csv"
            frame.to_csv(p, index=False)
            files.append({"snapshot_date": m.isoformat(), "path": str(p), "listings": len(frame), "kind": "history"})
        if not snap.empty:
            p = out / f"{today.isoformat()}.csv"
            snap.to_csv(p, index=False)
            rp = None
            if len(rev):
                rp = out / f"{today.isoformat()}.reviews.csv"
                rev.to_csv(rp, index=False)
            files.append({"snapshot_date": today.isoformat(), "path": str(p), "listings": len(snap), "kind": "live",
                          "reviews": len(rev), "reviews_path": str(rp) if rp else None})
        jobs = []
        if process:
            for f in files:
                rv = pd.read_csv(f["reviews_path"]) if f.get("reviews_path") else None
                s_ = runner.process_dataset(Path(f["path"]), market, snapshot_date=f["snapshot_date"], reviews=rv,
                                            source_name=f"amazon-live {f['kind']} {f['snapshot_date']}", marketplace="US")
                jobs.append({"snapshot_date": f["snapshot_date"], "dataset_id": s_.get("dataset_id"),
                             "products": (s_.get("category") or {}).get("products")})
        badged = int(snap["sales"].notna().sum()) if len(snap) else 0
        result = {"terms": got["terms"], "listings": len(snap), "tracked": got["tracked"], "badged": badged,
                  "reviews": len(rev), "history_months": len([f for f in files if f["kind"] == "history"]),
                  "snapshots": files, "processed": jobs, "notes": got["notes"]}
        if got["notes"]:
            status = "partial"
        if snap.empty:
            status, error = "failed", "no listings acquired" + (f": {got['notes'][0]}" if got["notes"] else "")
    except AcquireError as exc:
        status, error = "failed", str(exc)
    except Exception as exc:  # noqa: BLE001 -- recorded on the run; the caller sees it too
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        _finish(run_id, status, error, result, ledger, providers)
        raise
    _finish(run_id, status, error, result, ledger, providers)
    return {"id": run_id, "market": market, "status": status, "error": error, **result, "ledger": ledger.to_dict()}


def create_run(market: str, *, history: bool, reviews: bool, by: str | None, max_listings: int | None,
               status: str = "running") -> str:
    with b.session() as s:
        run = b.AcquisitionRun(kind="market_snapshot" if not history else "history_backfill", market_name=market, status=status,
                               params={"history": history, "reviews": reviews, "by": by, "max_listings": max_listings})
        s.add(run)
        s.flush()
        return run.id


def due_markets(now: datetime | None = None) -> list[str]:
    """Processed markets whose last acquisition is older than ``schedule.every_days`` (or never ran)."""
    from datetime import timedelta

    now = now or datetime.now(timezone.utc)
    every = timedelta(days=float(config()["schedule"]["every_days"]))
    with b.session() as s:
        names = [m.name for m in s.query(b.Market).all()]
        last: dict[str, datetime] = {}
        for r in s.query(b.AcquisitionRun).filter(b.AcquisitionRun.status.in_(["done", "partial"])).all():
            t = r.started_at if r.started_at.tzinfo else r.started_at.replace(tzinfo=timezone.utc)
            if r.market_name and (r.market_name not in last or t > last[r.market_name]):
                last[r.market_name] = t
    return [n for n in names if n not in last or now - last[n] >= every]


def _finish(run_id: str, status: str, error: str | None, result: dict, ledger: Ledger, providers: dict | None) -> None:
    with b.session() as s:
        r = s.get(b.AcquisitionRun, run_id)
        r.status, r.error, r.result, r.ledger = status, error, result, ledger.to_dict()
        r.providers = providers
        r.finished_at = datetime.now(timezone.utc)
    from dip import audit
    audit.record("acquisition.run", None, "acquisition_runs", run_id, {"status": status, "listings": result.get("listings")})


def runs(market: str | None = None, limit: int = 20) -> list[dict]:
    with b.session() as s:
        q = s.query(b.AcquisitionRun)
        if market:
            q = q.filter(b.AcquisitionRun.market_name == market)
        return [b.row_dict(r) for r in q.order_by(b.AcquisitionRun.started_at.desc()).limit(limit)]


def acquired_reviews(market: str, asin: str | None = None, limit: int = 200) -> pd.DataFrame:
    """Review text acquired for a market (latest file first), optionally one listing."""
    d = snapshot_dir(market)
    files = sorted(d.glob("*.reviews.csv"), reverse=True)
    if not files:
        return pd.DataFrame(columns=["asin", "review_id", "rating", "title", "text", "date", "verified", "helpful", "source"])
    df = pd.read_csv(files[0])
    if asin:
        df = df[df["asin"].astype(str) == asin]
    return df.head(limit)
