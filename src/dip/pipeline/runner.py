"""Job runner: executes the v2 pipeline as a tracked job.

    dataset upload -> ingestion (+report) -> cleaning -> relevance (+explanations)
    -> discovery (Family/Segment) -> resolution (Product Master) -> models
    -> analytics -> forecasting (+horizon labels) -> customer pain
    -> suppliers -> opportunity (v2) -> history (across uploads) -> confidence
    -> lake -> knowledge graph -> vectors -> market summary

Each stage's timing and summary is written to the ``jobs`` table as it
finishes, so the UI can show live progress. Every record -- rejected,
irrelevant or low-quality -- is kept in the lake's std zone with reasons.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from dmie.engine import store as core_store
from dmie.engine.config import section
from dmie.engine.ownership import market_owner_hints
from dmie.engine.pain import analyze_reviews, pain_by_scope, review_pain_score
from dmie.engine.pipeline import _reviews_frame
from dip.pipeline import analytics, cleaning, clustering, forecasting, ingestion, opportunity, product_resolution, relevance
from dip.pipeline import supplier as supplier_stage
from dip import events
from dip.intelligence import competitors, confidence, trends
from dip.intelligence.history import listing_history, periods
from dip.pipeline.clustering.hierarchy import assign_variants, text_models
from dip.pipeline.universe import assign_branch, galaxy_layout
from dip.settings import get_settings
from dip.logging_setup import log_context
from dip.metrics import opportunity_thresholds
from dip.storage import business as b
from dip.storage import lake
from dip.storage.graph import get_graph_store
from dip.storage.vectors import get_vector_store, neighbours_for

log = logging.getLogger("dip.runner")
_run_lock = threading.Lock()  # one pipeline at a time per process (DuckDB/Qdrant local are single-writer)


def _peak_mb() -> int | None:
    """Process peak RSS so far (ops telemetry for large datasets); None where unsupported."""
    try:
        import resource
        import sys

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak / (2**20 if sys.platform == "darwin" else 1024))
    except (ImportError, OSError):
        return None


class StageTracker:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.stages: list[dict] = []

    def _save(self, **fields) -> None:
        with b.session() as s:
            job = s.get(b.Job, self.job_id)
            job.stages = [dict(x) for x in self.stages]
            for k, v in fields.items():
                setattr(job, k, v)

    def run(self, name: str, fn, *args, summary=None, **kwargs):
        self.stages.append({"name": name, "status": "running"})
        self._save(status="running")
        t = time.perf_counter()
        with log_context(job_id=self.job_id, stage=name):  # job id + stage on every log record of the stage
            out = fn(*args, **kwargs)
        self.stages[-1].update(status="done", seconds=round(time.perf_counter() - t, 2), peak_mb=_peak_mb(),
                               summary=summary(out) if summary else None)
        self._save()
        return out


def _analytics_con():
    return core_store.connect(get_settings().analytics_path)


def create_job(kind: str, market: str | None, dataset_id: str | None = None) -> str:
    with b.session() as s:
        job = b.Job(kind=kind, market_name=market, dataset_id=dataset_id, status="queued", stages=[])
        s.add(job)
        s.flush()
        return job.id


def _unchanged(market: str, fingerprint: str | None) -> dict | None:
    """The stored summary when this exact input was already processed for this market."""
    if fingerprint is None:
        return None
    with b.session() as s:
        m = s.get(b.Market, market)
        if m is not None and (m.summary or {}).get("input_fingerprint") == fingerprint:
            return dict(m.summary)
    return None


def process_dataset(source, market: str, job_id: str | None = None, snapshot_date: str | None = None,
                    reviews: pd.DataFrame | None = None, overrides: dict | None = None,
                    source_name: str | None = None, marketplace: str | None = None, force: bool = False,
                    org_id: str | None = None, allow_duplicate: bool = False, duplicate_reason: str | None = None) -> dict:
    """allow_duplicate: accept content already registered under another market/period (needs a reason;
    audited). Runs of one market are serialised by a lake lock (lake.market_lock)."""
    from dip import cache

    job_id = job_id or create_job("process_dataset", market)
    tr = StageTracker(job_id)
    if not snapshot_date:                       # a date in the file name ("..._2026-09.xlsx") is the export date
        named = snapshot_date_from_name(source_name or (Path(source).name if isinstance(source, (str, Path)) else ""))
        if named:
            snapshot_date = named
    try:
        market = lake.validate_market_name(market)
        with lake.market_lock(market), _run_lock:
            if isinstance(source, (str, Path)) and Path(source).is_file():   # cheap pre-check before ingestion
                _check_duplicate(lake.content_hash(source), market, snapshot_date, org_id, allow_duplicate, duplicate_reason)
            acon = _analytics_con()
            try:
                fb = core_store.read_feedback(acon, section("relevance").get("domain", "dental"))
            finally:
                acon.close()
            fp = cache.input_fingerprint(source, snapshot_date, marketplace, reviews, fb, overrides)
            done = None if force else _unchanged(market, fp)
            if done is not None:
                tr.stages.append({"name": "skipped", "status": "done", "seconds": 0.0,
                                  "summary": {"reason": "identical input already processed for this market "
                                                        "(same file, corrections, suppliers, config); "
                                                        "use force to re-run"}})
                tr._save(status="done", finished_at=datetime.now(timezone.utc), dataset_id=done.get("dataset_id"))
                return done
            summary = _process(tr, source, market, snapshot_date, reviews, overrides, source_name, marketplace, fp, org_id,
                               allow_duplicate=allow_duplicate, duplicate_reason=duplicate_reason)
        _integrity(tr, market)          # before the board warm-up: it updates the market row (the board cache key)
        _warm_board(tr, market)
        _watch_changes(market)
        tr._save(status="done", finished_at=datetime.now(timezone.utc))
        return summary
    except Exception as exc:  # the job records the failure; the caller gets it too
        if tr.stages and tr.stages[-1]["status"] == "running":
            tr.stages[-1]["status"] = "failed"
        tr._save(status="failed", error=f"{exc}\n{traceback.format_exc()[-2000:]}", finished_at=datetime.now(timezone.utc))
        raise


def _check_duplicate(content_hash: str, market: str, snapshot_date, org_id, allow: bool, reason: str | None) -> list[dict]:
    """Content-hash registry: the conflicts (empty when none). Raises b.DuplicateContent unless overridden."""
    if not allow:
        b.check_duplicate(content_hash, market, snapshot_date, org_id)
        return []
    if not (reason or "").strip():
        raise ValueError("allow_duplicate requires a reason")
    return b.duplicate_conflicts(content_hash, market, snapshot_date, org_id)


def _process(tr: StageTracker, source, market: str, snapshot_date, reviews, overrides, source_name, marketplace,
             fingerprint: str | None = None, org_id: str | None = None, allow_duplicate: bool = False,
             duplicate_reason: str | None = None) -> dict:
    started = datetime.now(timezone.utc)
    dataset_id = b.new_id()
    before = events.snapshot_state(market)  # previous state, for change events after this run
    from dip import significance
    before_v3 = significance.v3_state(market)  # previous v3 estimates with intervals, for significance tests
    domain = section("relevance").get("domain", "dental")

    ing = tr.run("ingestion", ingestion.ingest_dataset, source, market, dataset_id, overrides,
                 summary=lambda o: {k: o.report[k] for k in ("raw_rows", "accepted", "rejected", "rejection_reasons")})
    from dip.knowledge import currency as kn_currency          # one base currency before any money is compared (spec 68)
    ing.frame, fx = kn_currency.normalize(ing.frame, marketplace or ("US" if ing.adapter == "sellersprite" else None))
    if fx["converted"]:
        ing.report["warnings"].append(f"prices and revenue converted {fx['currency']} -> {fx['base']} at {fx['rate']} "
                                      f"(rates as of {fx.get('rates_as_of') or 'unstated'}); local values kept")
    conflicts = _check_duplicate(ing.content_hash, market, snapshot_date, org_id, allow_duplicate, duplicate_reason)
    dup = conflicts[0] if conflicts else None
    if dup:
        from dip import audit
        audit.record("dataset.duplicate_override", None, "datasets", dataset_id,
                     {"market": market, "snapshot_date": snapshot_date, "reason": duplicate_reason,
                      "content_hash": ing.content_hash, "existing": conflicts, "job_id": tr.job_id})
    with b.session() as s:
        s.add(b.Dataset(id=dataset_id, market_name=market, source_name=str(source_name or getattr(source, "name", source))[:500],
                        source_kind=ing.adapter, content_hash=ing.content_hash, snapshot_date=snapshot_date,
                        raw_rows=ing.report["raw_rows"], accepted_rows=ing.report["accepted"],
                        rejected_rows=ing.report["rejected"], report=ing.report, duplicate_of=dup["dataset_id"] if dup else None,
                        duplicate_reason=duplicate_reason if dup else None, org_id=org_id))
        if dup:
            ing.report["warnings"].append(f"identical content was already uploaded as dataset {dup['dataset_id']} for market "
                                          f"'{dup['market']}' (snapshot {dup['snapshot_date'] or '(none)'}); "
                                          f"accepted as a duplicate: {duplicate_reason}")
        job = s.get(b.Job, tr.job_id)
        job.dataset_id = dataset_id

    q = tr.run("cleaning", cleaning.clean, ing.frame, summary=lambda r: r.summary())
    ing.frame = None  # at 10^6 rows every retained copy costs ~1-2 GB
    quality_summary = q.summary()

    acon = _analytics_con()
    try:
        feedback = core_store.read_feedback(acon, domain)
    finally:
        acon.close()
    corrections = {k: bool(v) for k, v in zip(feedback["record_key"], feedback["label"])}
    rel = tr.run("relevance", relevance.classify, q.frame, feedback[["text", "label"]], corrections, domain, True,
                 summary=lambda r: {**r["relevance_status"].value_counts().to_dict(), "cache": dict(relevance.CACHE_STATS)})
    q.frame = None
    # category boundary: marketplace sub-categories outside the market's definition (config/categories.yaml)
    from dip.pipeline import scope as scope_stage
    rel, scope_table = tr.run("category_scope", scope_stage.apply, rel, market,
                              summary=lambda r: r[1].groupby("decision")["listings"].sum().to_dict() if len(r[1]) else {})

    # latest snapshot per listing, chosen on two columns so the 10^6-row frame is never copied whole
    keep = rel["is_relevant"].to_numpy() & rel["usable_for_market"].to_numpy()
    if rel["timestamp"].notna().any():
        key = rel.loc[keep, ["id", "timestamp"]].sort_values("timestamp", kind="stable")
        current = rel.loc[key.drop_duplicates("id", keep="last").index].copy()
    else:
        current = rel.loc[keep].copy()
    from dip.pipeline.clustering import taxonomy
    disc = tr.run("clustering", taxonomy.discover, current,
                  summary=lambda d: {"families": len(d.families), "segments": len(d.segments), "method": d.method})
    # keep segment IDs stable across uploads (inherited by listing overlap; forecast history is matched by them)
    prev_seg = lake.read_curated("listings", market, columns=["id", "segment_id"]) if lake.has_curated("listings", market) else None
    disc, _ = taxonomy.stabilize(disc, prev_seg)
    dd = tr.run("product_resolution", product_resolution.resolve, disc.frame, summary=lambda r: r.summary())
    # product identity (spec 19 stage 5, 95): split resolved products whose listings name different component
    # configurations (N3+H37L1 != N3+102L) and apply people's merge / keep-separate decisions
    from dip.knowledge import attributes as kn_attributes
    from dip.knowledge import identity as kn_identity
    id_decisions = kn_identity.load_decisions(market)

    def _identity():
        keys = kn_attributes.extract(dd.frame, market)["configuration_key"]
        frame, changes = kn_identity.regroup(dd.frame, keys, id_decisions)
        if changes:
            dd.frame, dd.products = kn_identity.rebuild(frame)
        else:
            dd.frame = frame
        return changes
    id_changes = tr.run("identity", _identity, summary=lambda c: {
        "configuration_splits": sum(x["change"] == "configuration_split" for x in c),
        "human_merges": sum(x["change"] == "human_merge" for x in c),
        "human_separations": sum(x["change"] == "human_keep_separate" for x in c), "products": int(len(dd.products))})
    # keep product IDs stable across uploads (hash IDs change when a product's listing set changes)
    from dip.pipeline.product_resolution.stable_ids import stabilize
    prev_ids = lake.read_curated("listings", market, columns=["id", "product_id"]) if lake.has_curated("listings", market) else None
    dd.frame, dd.products, id_stats = stabilize(dd.frame, dd.products, prev_ids)
    listings = dd.frame
    mk = tr.run("analytics", analytics.market_capacity, dd.products, listings, disc.segments,
                summary=lambda m: {k: m.category.get(k) for k in ("products", "listings", "monthly_revenue", "sales_coverage", "concentration")})

    # forecasting: dataset timestamps, else accumulated snapshot history
    seg_of_id = dict(zip(listings["id"], listings["segment_id"]))
    ts_mask = rel["is_relevant"].to_numpy() & rel["timestamp"].notna().to_numpy()
    ts_records = rel.loc[ts_mask, ["id", "timestamp", "revenue", "sales"]].assign(segment_id=lambda d: d["id"].map(seg_of_id))
    labels = dict(zip(mk.segments["segment_id"], mk.segments["segment_label"])) if len(mk.segments) else {}
    labels["__market__"] = "__market__"
    history = None
    if not rel["timestamp"].notna().any():
        period = pd.Timestamp(snapshot_date).date() if snapshot_date else started.date()
        rows = [{"subject": "__market__", "subject_label": "__market__", "revenue": mk.category.get("monthly_revenue"),
                 "sales": mk.category.get("monthly_sales")}]
        rows += [{"subject": s["segment_label"], "subject_label": s["segment_label"], "revenue": s["monthly_revenue"],
                  "sales": s["monthly_sales"]} for _, s in mk.segments.iterrows()]
        acon = _analytics_con()
        try:
            core_store.append_history(acon, market, period, rows, dataset_id)
            history = core_store.read_history(acon, market)
        finally:
            acon.close()
    fcs, horizons = tr.run("forecasting", forecasting.forecast, ts_records, history, labels,
                           summary=lambda r: r[1].get("__market__"))
    growth = {}
    label_to_seg = {v: k for k, v in labels.items()}
    for f in fcs:
        if f.status == "ok" and f.growth_rate is not None and f.subject != "__market__":
            growth[label_to_seg.get(f.subject, f.subject)] = f.growth_rate
    cohort = forecasting.launch_cohort(rel.loc[rel["is_relevant"].to_numpy(), ["launch_date", "revenue"]])

    # customer pain
    def _pain():
        id_to_product = dict(zip(listings["id"], listings["product_id"]))
        rv = _reviews_frame(rel, reviews, id_to_product)
        out = {"__market__": analyze_reviews(rv["text"].tolist(), rv["rating"].tolist(), "__market__")}
        seg_scores: dict[str, float] = {}
        if len(rv):
            prod_seg = dict(zip(dd.products["product_id"], dd.products["segment_id"]))
            rv["segment_id"] = rv["product_id"].map(prod_seg)
            for k, rep in pain_by_scope(rv.dropna(subset=["segment_id"]), "segment_id").items():
                out[f"segment:{k}"] = rep
                seg_scores[k] = review_pain_score(rep)
            for k, rep in pain_by_scope(rv.dropna(subset=["product_id"]), "product_id").items():
                out[f"product:{k}"] = rep
        return out, seg_scores
    pain, seg_pain = tr.run("customer_pain", _pain, summary=lambda r: {"status": r[0]["__market__"].status, "reviews": r[0]["__market__"].reviews})

    sup, sup_matches = tr.run("suppliers", supplier_stage.score_and_match, mk.segments, org_id,
                              summary=lambda r: {"suppliers": len(r[0]), "matches": len(r[1])})
    momentum = cohort.get("new_listing_revenue_share") if cohort.get("status") == "ok" else None
    segs = tr.run("opportunity", opportunity.score, mk.segments, growth, seg_pain, momentum, sup_matches,
                  summary=lambda s: {"top": s[["segment_label", "opportunity_score"]].head(3).to_dict("records")})
    products = opportunity.score_product_level(mk.products, segs.assign(opportunity_score=segs["opportunity_score"]))
    products = clustering.assign_models(products)
    products = assign_variants(text_models(products))   # Model (spec | text) -> Variant levels
    products = galaxy_layout(products)
    products["pain_status"] = products["product_id"].map(lambda p: pain.get(f"product:{p}").status if pain.get(f"product:{p}") else None)
    branch, branch_scores = assign_branch(products["title"].tolist())

    # listing history across uploads of this market -> data confidence per product / segment / market
    # one "as of" date for every number of this run: the declared (or file-name) snapshot date, else the import date
    period = pd.Timestamp(snapshot_date) if snapshot_date else pd.Timestamp(started.date())
    as_of_basis = "snapshot date" if snapshot_date else "import date (no snapshot date declared)"
    hist = tr.run("history", listing_history, market, rel, period, dataset_id, ing.content_hash,
                  summary=lambda h: {"periods": len(periods(h)), "listing_rows": len(h)})
    products = tr.run("confidence", confidence.score_products, products, listings, ing.adapter, hist,
                      summary=lambda p: p["confidence_level"].value_counts().to_dict())
    segs = segs.drop(columns=[c for c in ("confidence_score", "confidence_level") if c in segs]).merge(
        confidence.rollup(products, "segment_id"), on="segment_id", how="left")
    market_confidence = confidence.rollup(products)

    # trend detection (market + segments) from every signal the data supports
    fc_growth = {**growth, **{"__market__": f.growth_rate for f in fcs if f.subject == "__market__" and f.status == "ok"}}
    as_of = max(periods(hist)) if len(hist) else period
    trend = tr.run("trends", trends.detect, hist, listings, segs, pd.Timestamp(as_of), fc_growth,
                   summary=lambda d: d.loc[d["scope"] == "__market__", ["trend", "confidence"]].to_dict("records")[0])
    seg_tr = trend[trend["scope"] != "__market__"].rename(columns={"scope": "segment_id", "trend": "trend_label",
                                                                   "direction": "trend_direction", "confidence": "trend_confidence",
                                                                   "expected_growth_12m": "trend_growth_12m"})
    segs = segs.drop(columns=[c for c in ("trend_label", "trend_direction", "trend_confidence", "trend_growth_12m") if c in segs]).merge(
        seg_tr[["segment_id", "trend_label", "trend_direction", "trend_confidence", "trend_growth_12m"]], on="segment_id", how="left")
    products = products.merge(segs[["segment_id", "trend_label", "trend_direction"]], on="segment_id", how="left")
    market_trend = trend[trend["scope"] == "__market__"].iloc[0].to_dict()
    comp = tr.run("competitors", competitors.profile, products, listings, segs, pd.Timestamp(as_of), hist, pain,
                  summary=lambda c: {"brands": len(c), "leaders": c.loc[c["position"] == "Leader", "brand"].tolist()[:3] if len(c) else []})

    # metrics engine v3: interval-censored demand, estimated size / shares / entry / economics / opportunity
    from dip.metrics import engine as metrics_engine
    from dip.metrics.observation import classify as sales_kind_of
    acc = rel["accepted"].to_numpy() if "accepted" in rel else slice(None)
    sales_kind = sales_kind_of(rel.loc[acc, "sales"])[0]           # detected on the whole export, not the subset
    mv3 = tr.run("metrics", metrics_engine.compute, listings, products, segs,
                 period, max(len(periods(hist)), 1), seg_pain, sales_kind, as_of_basis,
                 summary=lambda o: {k: o.summary.get(k) for k in ("revenue_month", "evidence_grade")})
    listings, products, segs = mv3.listings, mv3.products, mv3.segments
    segs = taxonomy.describe(segs, listings)
    # one growth number: the trend's expected 12-month growth comes from the same v3 revenue history as the forecast
    trend = _align_growth(trend, market, period, mv3.summary, segs)
    market_trend = trend[trend["scope"] == "__market__"].iloc[0].to_dict()
    g12 = trend.set_index(trend["scope"].astype(str))["expected_growth_12m"]
    if "trend_growth_12m" in segs:                         # the opportunity engine reads the segment's growth from here
        segs["trend_growth_12m"] = segs["segment_id"].astype(str).map(g12).where(lambda v: v.notna(), segs["trend_growth_12m"])
    from dip.metrics import gaps as gaps_engine
    gaps_tab, recs = tr.run("gaps", gaps_engine.analyse, listings, segs,
                            summary=lambda r: {"tested": len(r[0]), "gaps": int(r[0]["is_gap"].sum()) if len(r[0]) else 0,
                                               "recommendations": len(r[1])})

    # product knowledge (spec: attributes, component role, Dental Confidence, applications, provenance)
    from dip.knowledge import stage as knowledge_stage
    observed_at = str(period.date())
    kn = tr.run("knowledge", knowledge_stage.build, rel, listings, products, segs, market, dataset_id,
                ing.adapter, observed_at, mv3.summary, summary=lambda k: k.summary())
    for c in kn.records.columns:
        rel[c] = kn.records[c].to_numpy()
    listings, products = kn.listings, kn.products
    idc = kn_identity.identity_confidence(listings, dd.edges)
    products = products.drop(columns=[c for c in ("identity_confidence", "identity_components") if c in products]).merge(
        idc, on="product_id", how="left")
    dup_candidates = kn_identity.duplicate_candidates(listings, dd.edges, id_decisions)
    # dynamic taxonomy from the products' attributes, with people's approvals (spec 9-10, 103-104)
    from dip.knowledge import taxonomy_discovery as kn_tax
    tax_nodes, tax_dims = tr.run("taxonomy_discovery", kn_tax.discover, products, market, kn_tax.load_decisions(market),
                                 summary=lambda r: {"nodes": int(len(r[0])), "dimensions_tested": int(len(r[1])),
                                                    "meaningful": r[1].loc[r[1]["meaningful"], "dimension"].tolist() if len(r[1]) else []})
    products["taxonomy_node"] = kn_tax.assign(products, tax_nodes)
    # market capacity per category / segment / taxonomy node, and the best-selling listing rule (spec 21-26, 37-38)
    from dip.knowledge import capacity as kn_cap
    best = kn_cap.best_listings(listings)
    products = products.drop(columns=["best_listing", "best_listing_basis"], errors="ignore").merge(best, on="product_id", how="left")
    listings["is_best_listing"] = listings["id"].astype(str).isin(set(best["best_listing"]))
    capacity_tab = tr.run("capacity", kn_cap.build, products, listings, segs, tax_nodes, market, mv3.interval_for,
                          summary=lambda t: {"scopes": t["scope"].value_counts().to_dict()})
    kn.observations = pd.concat([kn.observations, kn_cap.observations(capacity_tab, dataset_id, observed_at)], ignore_index=True)
    # opportunity engine: transparent dimensions, evidence matrix, patterns, risks, reasons (spec 35-36, 71-74, 155-156)
    from dip.knowledge import offline as kn_offline
    from dip.knowledge import opportunity as kn_opp

    def _opportunities():
        with b.session() as s:
            prj = pd.DataFrame([{"id": p.id, "segment_id": p.segment_id, "stage": p.stage, "status": p.status}
                                for p in s.query(b.Project).filter(b.Project.market_name == market).all()])
        return kn_opp.build(capacity_tab, segs, products, listings, tax_nodes, sup_matches, len(sup) > 0, prj, pain,
                             kn_offline.load(market))
    opp_tab = tr.run("opportunity_engine", _opportunities, summary=lambda o: {
        "scopes": int(len(o)), "ranked": int(o["opportunity_score"].notna().sum()) if len(o) else 0,
        "top": o.loc[o["opportunity_score"].notna(), ["label", "opportunity_score"]].head(3).to_dict("records") if len(o) else []})

    # lake
    def _lake():
        listings_out = listings.merge(products[["product_id", "opportunity_score", "model_id", "model_label"]], on="product_id", how="left")
        # join by record_id without copying the (possibly 10^6-row) record frame
        records = rel
        lk = listings_out.set_index("record_id")
        for c in ("segment_id", "segment_label", "family_id", "product_id", "is_best_listing"):
            records[c] = records["record_id"].map(lk[c])
        reason = pd.Series(None, index=records.index, dtype=object)
        rej = records["rejection_reasons"].map(len).to_numpy() > 0
        reason[rej] = "rejected: " + records.loc[rej, "rejection_reasons"].map(", ".join)
        m = reason.isna() & ~records["is_relevant"]
        reason[m] = "not relevant: " + records.loc[m, "relevance_status"]
        m = reason.isna() & ~records["usable_for_market"]
        reason[m] = "low data quality: " + records.loc[m, "quality_issues"].map(", ".join)
        m = reason.isna() & records["product_id"].isna()
        reason[m] = "older snapshot (history only)"
        records["excluded_reason"] = reason
        std = lake.write_std(records.drop(columns=["description", "review_text"], errors="ignore"), dataset_id)
        lake.copy_to_curated(std, "records", market)  # same file, written once
        lake.write_curated("listings", market, listings_out)
        lake.write_curated("products", market, products)
        lake.write_curated("segments", market, _segment_table(segs, opp_tab))
        lake.write_curated("families", market, disc.families)
        lake.write_curated("forecasts", market, pd.DataFrame([{"subject": f.subject, "status": f.status, "payload": f.to_dict(),
                                                                "horizons": horizons.get(f.subject)} for f in fcs]))
        lake.write_curated("pain", market, pd.DataFrame([{"scope": k, "status": v.status, "payload": v.to_dict()} for k, v in pain.items()]))
        lake.write_curated("supplier_matches", market, sup_matches)
        lake.write_curated("trends", market, trend)
        lake.write_curated("competitors", market, comp)
        lake.write_curated("dedup_pairs", market, dd.edges.drop(columns=["a", "b"], errors="ignore"))
        lake.write_curated("metrics", market, mv3.metrics)
        lake.write_curated("scope", market, scope_table)
        lake.write_curated("gaps", market, gaps_tab)
        lake.write_curated("recommendations", market, recs)
        lake.write_curated("duplicate_candidates", market, dup_candidates)
        lake.write_curated("taxonomy_nodes", market, tax_nodes)
        lake.write_curated("capacity", market, capacity_tab)
        lake.write_curated("opportunities", market, opp_tab)
        lake.write_curated("taxonomy_dimensions", market, tax_dims)
        lake.write_curated("observations", market, kn.observations)
        lake.write_curated("evidence", market, kn.evidence)
        from dip.knowledge import provenance as prov
        prev_obs = lake.read_curated("observation_history", market) if lake.has_curated("observation_history", market) else None
        from dip.knowledge import anomalies as kn_anom      # data anomalies for review (spec 154), before this run joins the history
        lake.write_curated("anomalies", market, kn_anom.detect(listings, prev_obs, dataset_id))
        lake.write_curated("observation_history", market, prov.append_history(prev_obs, kn.observations))
        from dip.metrics import brands as brands_mod
        if mv3.demand is not None:                          # fitted demand model, for the launch simulator
            from dip.metrics.demand import model_card
            lake.write_curated("demand_model", market, pd.DataFrame([{
                "card": json.dumps(model_card(mv3.demand, mv3.summary.get("as_of")))}]))
        # v3 revenue per snapshot period (market + segments) -> growth and forecast
        from dip.metrics import forecast as fc_mod
        rh_prev = lake.read_curated("revenue_history", market)
        rh_cur = fc_mod.history_rows(period, mv3.summary, segs)
        if len(rh_prev) and len(rh_cur):
            rh_prev = rh_prev[rh_prev["period"] != rh_cur["period"].iat[0]]
        if len(rh_cur):
            lake.write_curated("revenue_history", market, pd.concat([rh_prev, rh_cur], ignore_index=True) if len(rh_prev) else rh_cur)
        if mv3.brands is not None:
            lake.write_curated("brands", market, mv3.brands)
            if mv3.segment_brands is not None and len(mv3.segment_brands):
                lake.write_curated("segment_brands", market, mv3.segment_brands)
            lake.write_curated("cohorts", market, mv3.cohorts)
            lake.write_curated("momentum", market, mv3.momentum)
            # brand shares per snapshot period (the period the metrics describe) -> significance-tested changes
            prev = lake.read_curated("brand_history", market)
            # a declared snapshot date is the period these shares describe; otherwise the latest data period
            cur = brands_mod.history_rows(mv3.brands, period)
            if len(prev):
                prev = prev[prev["period"] != cur["period"].iat[0]]
            lake.write_curated("brand_history", market, pd.concat([prev, cur], ignore_index=True) if len(prev) else cur)
        return {"records": len(records), "products": len(products)}
    with lake.batch():                  # all-or-nothing: a failure in any write keeps every table's previous version
        tr.run("lake", _lake, summary=lambda r: r)
    forecast_v3 = _forecast_v3(market)

    # change events -> alerts for the employees who own this market (ownership synced first so a
    # new market's owners are known)
    owners = ing.owner_hints if ing.owner_hints is not None else market_owner_hints(rel)

    def _events():
        sync_business_entities(market, {"owners": owners}, products)
        changes = events.detect_changes(market, before, comp, segs, listings, market_trend.get("trend"), pd.Timestamp(as_of),
                                        mk.category)
        changes = significance.apply(changes, before_v3, significance.after_state(mv3.summary, mv3.brands, segs))
        events.publish("dataset.processed", market, str(source_name or dataset_id),
                       {"dataset_id": dataset_id, "job_id": tr.job_id, "products": int(len(products)),
                        "changes": len(changes)}, "info", "pipeline")
        n = events.publish_changes(market, changes, dataset_id, tr.job_id)
        from dip.projects import record_outcomes   # launched projects tracking listings in this market
        return n + record_outcomes(market, dataset_id)
    n_events = tr.run("events", _events, summary=lambda n: {"change_events": n})

    # vectors first (graph uses them for PRODUCT_SIMILAR_TO)
    def _vectors():
        vs = get_vector_store()
        texts = (products["title"].fillna("") + " " + products["brand"].fillna("") + " " + products["product_type"].fillna("")).tolist()
        payloads = [{"title": str(t)[:200], "brand": br, "segment_id": sg, "price": None if pd.isna(pr) else float(pr),
                     "monthly_sales": None if pd.isna(ms) else float(ms)}
                    for t, br, sg, pr, ms in zip(products["title"], products["brand"], products["segment_id"], products["price"], products["monthly_sales"])]
        return vs.replace_market(market, products["product_id"].tolist(), texts, payloads)
    tr.run("vectors", _vectors, summary=lambda n: {"indexed_products": n, "backend": get_vector_store().backend})

    from dip.intelligence import ontology
    nodes, edges = ontology.build(market, branch, disc.families, segs, products, listings, sup, sup_matches, pain,
                                  gaps_tab, recs, mv3.segment_brands, mv3.momentum, similar_pairs(products), trend)
    nodes, edges = add_project_nodes(market, nodes, edges)
    tr.run("knowledge_graph", get_graph_store().replace_market, market, nodes, edges,
           summary=lambda _: {"nodes": len(nodes), "edges": len(edges), "backend": get_graph_store().backend})

    summary = {
        "market_name": market, "dataset_id": dataset_id, "generated_at": started.isoformat(), "domain": domain,
        "snapshot": {"date": str(period.date()), "basis": as_of_basis},
        "industry_branch": branch, "branch_scores": branch_scores,
        # SellerSprite exports in this project are Amazon US (PRINCIPLES.md); other sources must say so explicitly
        "marketplace": marketplace or ("US" if ing.adapter == "sellersprite" else None), "currency": fx,
        "ingestion": ing.report, "quality": quality_summary,
        "relevance": {"relevant": int(rel["is_relevant"].sum()),
                      "uncertain": int((rel["relevance_status"] == "uncertain").sum()),
                      "irrelevant": int(rel["relevance_status"].isin(["irrelevant", "human_irrelevant"]).sum()),
                      "human_corrections_applied": int(rel["relevance_status"].str.startswith("human").sum())},
        "discovery": {"families": int(len(disc.families)), "segments": int(len(disc.segments)),
                      "models": int(products.loc[products["model_label"] != "other", "model_id"].nunique()), "method": disc.method},
        "dedup": dd.summary(), "product_ids": id_stats, "metrics_v3": {**mv3.summary, "top_segment": _top_segment(_segment_table(segs, opp_tab))},
        "scope": scope_table.groupby("decision")["listings"].sum().to_dict() if len(scope_table) else {},
        "recommendation": (recs.iloc[0][["segment_id", "segment_label", "features", "price_range", "expected_units",
                                         "expected_units_lo", "expected_units_hi", "basis"]].to_dict() if len(recs) else None), "category": mk.category, "confidence": market_confidence,
        "history_periods": [str(x.date()) for x in periods(hist)],
        "competitors": {"brands": int(len(comp)), "leaders": comp.loc[comp["position"] == "Leader", "brand"].tolist() if len(comp) else [],
                        "with_changes": int((comp["changes"] != "{}").sum()) if len(comp) else 0},
        "trend": {k: (json.loads(v) if k in ("signals", "evidence", "seasonality") else v) for k, v in market_trend.items() if k != "scope"},
        "forecast": forecast_v3,
        "horizons": horizons.get("__market__"), "launch_cohort": cohort,
        "pain": {"status": pain["__market__"].status, "reviews": pain["__market__"].reviews,
                 "top_complaints": pain["__market__"].complaints[:5]},
        "suppliers": {"count": int(len(sup)), "matches": int(len(sup_matches))},
        "opportunity": _opportunity_headline(_segment_table(segs, opp_tab)),
        # the explainable opportunity engine (knowledge layer): headline for market lists and the command center
        "opportunity_engine": {"top_label": opp_tab["label"].iat[0] if len(opp_tab) and opp_tab["opportunity_score"].notna().any() else None,
                               "top_score": float(opp_tab["opportunity_score"].max()) if len(opp_tab) and opp_tab["opportunity_score"].notna().any() else None,
                               "ranked": int(opp_tab["opportunity_score"].notna().sum()) if len(opp_tab) else 0,
                               "scopes": int(len(opp_tab))},
        "owners": owners, "change_events": n_events, "input_fingerprint": fingerprint, "knowledge": {**kn.summary(), "identity_changes": id_changes[:50],
                                                                                "duplicate_candidates": int(len(dup_candidates)),
                                                                                "taxonomy_nodes": int(len(tax_nodes))},
    }
    with b.session() as s:
        s.merge(b.Market(name=market, industry_branch=branch, run_id=tr.job_id, dataset_id=dataset_id, summary=_jsonable(summary),
                         org_id=org_id))
    from dip import tenancy   # usage metering (per organization and month)
    tenancy.meter(org_id, "records_ingested", ing.report["raw_rows"], dataset_id)
    tenancy.meter(org_id, "datasets_processed", 1, dataset_id)
    sync_business_entities(market, summary, products)
    return summary


def _jsonable(o):
    import json
    return json.loads(json.dumps(o, default=lambda x: x.item() if hasattr(x, "item") else str(x)))


def sync_business_entities(market: str, summary: dict, products: pd.DataFrame) -> None:
    """Brands -> companies; dataset category labels / owners -> categories + ownership."""
    with b.session() as s:
        for brand in products["brand"].dropna().unique()[:5000]:
            cid = b.stable_id("brand", brand)
            if s.get(b.Company, cid) is None:
                s.add(b.Company(id=cid, name=str(brand)[:255], kind="brand"))
        owners = summary.get("owners", {})
        for label in list(owners.get("category_labels", {}))[:20] or [market]:
            cat = s.get(b.Category, b.stable_id("cat", label)) or b.Category(id=b.stable_id("cat", label), label=label)
            cat.market_name = market
            s.merge(cat)
            for emp_name in owners.get("owners", {}):
                eid = b.stable_id("emp", emp_name)
                if s.get(b.Employee, eid) is None:
                    s.add(b.Employee(id=eid, name=emp_name))
                    s.flush()
                if not s.query(b.Ownership).filter_by(employee_id=eid, category_id=cat.id).first():
                    s.add(b.Ownership(employee_id=eid, category_id=cat.id, basis=f"named in dataset for market {market}"))


def _f(v):
    try:
        return None if v is None or pd.isna(v) else float(v)
    except (TypeError, ValueError):
        return None


def add_project_nodes(market, nodes, edges) -> tuple[list, list]:
    """Projects of this market as graph nodes: Segment -HAS_PROJECT-> Project (so a re-run keeps them)."""
    from dip.projects import project_graph

    pn, pe = project_graph(market)
    ids = {n["id"] for n in nodes}
    return nodes + pn, edges + [e for e in pe if e["source"] in ids or e["source"].startswith("project:")]


def _align_growth(trend: pd.DataFrame, market: str, period, summary: dict, segs: pd.DataFrame) -> pd.DataFrame:
    """Where the v3 revenue history (earlier snapshots + this one) supports a fit, the trend's expected 12-month
    growth is that fit's -- the number the forecast card shows -- instead of the older forecast on badge floors."""
    from dip.metrics import forecast as fc_mod
    prev = lake.read_curated("revenue_history", market) if lake.has_curated("revenue_history", market) else pd.DataFrame()
    cur = fc_mod.history_rows(period, summary, segs)
    if len(prev) and len(cur):
        prev = prev[prev["period"] != cur["period"].iat[0]]
    h = pd.concat([prev, cur], ignore_index=True) if len(prev) else cur
    if h.empty:
        return trend
    fc = fc_mod.market_forecast(h)
    fits = {"__market__": fc["market"]} if fc["market"].get("status") == "ok" else {}
    fits.update({str(x["segment_id"]): x for x in fc.get("segments", [])})
    if not fits:
        return trend
    t = trend.copy()
    t["expected_growth_basis"] = t["expected_growth_basis"].astype(object) if "expected_growth_basis" in t else None
    for i, sc in zip(t.index, t["scope"].astype(str)):
        f = fits.get(sc)
        if f is None:
            continue
        g12 = f.get("growth_12m", (1 + f["growth_per_month"]) ** 12 - 1)
        t.at[i, "expected_growth_12m"] = round(float(g12), 4)
        t.at[i, "expected_growth_basis"] = "metrics v3 revenue history (the forecast's fit)"
    return t


def _forecast_v3(market: str) -> dict:
    """The market summary's forecast: the v3 fit over the v3 revenue history, i.e. the same revenue every other
    surface shows. (The v1 forecast sums observed badge floors; it stays in the 'forecasts' table for older views.)"""
    from dip.metrics import forecast as fc_mod
    h = lake.read_curated("revenue_history", market) if lake.has_curated("revenue_history", market) else pd.DataFrame()
    h = h[h["scope"] == "market"].sort_values("period") if len(h) else h
    fit = fc_mod.fit(h) if len(h) else {"status": "needs_snapshots", "periods": 0}
    hist = [[str(p), float(e)] for p, e in zip(h["period"], h["est"]) if pd.notna(e)] if len(h) else []
    return {**fit, "history": hist, "basis": "metrics v3 revenue estimate per snapshot"}


def _segment_table(segs: pd.DataFrame, opp: pd.DataFrame | None = None) -> pd.DataFrame:
    """The stored segment table carries one revenue and one opportunity score, the same numbers every page shows:

    * ``monthly_revenue`` is the metrics-v3 estimate; the older sum of observed badge floors keeps its own name
      (``observed_revenue_floor``);
    * ``opportunity_score`` is the explainable opportunity engine's (knowledge layer: evidence matrix, reasons,
      gates), null where it has too little evidence (``opportunity_status`` says why); the metrics-v3 index stays
      as ``opportunity_index``, a named input, and the v1 score computed on badge floors is not stored."""
    out = segs.copy()
    if "revenue_est" in out and "monthly_revenue" in out:
        out["observed_revenue_floor"] = out["monthly_revenue"]
        out["monthly_revenue"] = out["revenue_est"].where(out["revenue_est"].notna(), out["monthly_revenue"])
    out = out.drop(columns=["opportunity_v2"], errors="ignore")
    if opp is not None and len(opp) and "opportunity_score" in opp:
        o = opp[opp["scope"] == "segment"].assign(scope_id=lambda d: d["scope_id"].astype(str)).set_index("scope_id")
        sid = out["segment_id"].astype(str)
        out["opportunity_score"] = sid.map(o["opportunity_score"]).astype(float)
        out["opportunity_status"] = sid.map(o["status"]) if "status" in o else None
        out = out.sort_values(["opportunity_score", "opportunity_index"] if "opportunity_index" in out else ["opportunity_score"],
                              ascending=False, na_position="last").reset_index(drop=True)
    return out


def _opportunity_headline(seg_table: pd.DataFrame) -> dict:
    """The market's best segment by the one opportunity score (the explainable engine's)."""
    scored = seg_table[seg_table["opportunity_score"].notna()] if "opportunity_score" in seg_table else seg_table.iloc[0:0]
    top = scored.iloc[0] if len(scored) else None
    return {"top_segment": None if top is None else top["segment_label"],
            "top_score": None if top is None else float(top["opportunity_score"]),
            "high_opportunity_segments": int((scored["opportunity_score"] >= opportunity_thresholds()[1]).sum()),
            "basis": "explainable opportunity engine (knowledge layer)"}


def _top_segment(segs: pd.DataFrame) -> dict | None:
    """Headline of the market: its best segment by the one opportunity score (the explainable engine's; segments
    it cannot score yet fall back to the metrics-v3 index, and ``ranked_by`` says which decided)."""
    if segs is None or segs.empty:
        return None
    by = next((c for c in ("opportunity_score", "opportunity_index") if c in segs and segs[c].notna().any()), None)
    if by is None:
        return None
    r = segs.loc[segs[by].idxmax()]
    f = lambda k: None if k not in r or pd.isna(r.get(k)) else float(r.get(k))  # noqa: E731
    return {"segment_id": r["segment_id"], "segment_label": r.get("segment_label"), "opportunity_score": f("opportunity_score"),
            "opportunity_index": f("opportunity_index"), "opportunity_level": r.get("opportunity_level"),
            "revenue_est": f("revenue_est"), "ranked_by": by}


def similar_pairs(products: pd.DataFrame, k: int = 3, min_score: float = 0.55) -> list[tuple[str, str, float]]:
    """Nearest-neighbour title/brand embedding pairs (cosine >= ``min_score``), each pair once."""
    if len(products) < 2:
        return []
    from dip.storage.vectors import embed
    texts = (products["title"].fillna("") + " " + products["brand"].fillna("")).tolist()
    V = embed(texts)
    ids = products["product_id"].tolist()
    out = []
    for i, js in enumerate(neighbours_for(texts, k=min(k, len(products) - 1))):
        for j in js:
            sc = float(V[i] @ V[j])
            if sc >= min_score and i < j:
                out.append((ids[i], ids[j], round(sc, 3)))
    return out


def run_in_background(**kwargs) -> str:  # kwargs as process_dataset (incl. force, org_id)
    job_id = create_job("process_dataset", kwargs.get("market"))
    from dip import worker
    if worker.mode() == "queue":            # durable queue: a separate worker process runs it
        worker.enqueue(job_id, kwargs)
        return job_id
    t = threading.Thread(target=lambda: _safe(process_dataset, job_id=job_id, **kwargs), daemon=True)
    t.start()
    return job_id


def _integrity(tr: StageTracker, market: str) -> None:
    """Truth harness (src/dip/metrics/integrity.py): record which displayed numbers disagree or lack support.
    Best effort -- it reports on the run, never fails it."""
    from dip.metrics import integrity

    try:
        rep = tr.run("integrity", integrity.check_market, market, summary=lambda r: {"status": r["status"], **r["counts"]})
    except Exception as exc:
        if tr.stages and tr.stages[-1]["name"] == "integrity":
            tr.stages[-1].update(status="skipped", summary={"error": str(exc)[:300]})
        log.warning("integrity check failed for %s", market, exc_info=True)
        return
    with b.session() as s:
        m = s.get(b.Market, market)
        if m is not None:
            m.summary = {**(m.summary or {}), "integrity": {"status": rep["status"], "counts": rep["counts"]}}


def _warm_board(tr: StageTracker, market: str) -> None:
    """Compute the market's opportunity-board rows (launch simulations included) now, so the first visit to the
    board after processing is served from the cache. Best effort: a failure here never fails the run."""
    from dip.metrics import board

    try:
        tr.run("board", board.warm, market, summary=lambda n: {"rows": n})
    except Exception as exc:
        if tr.stages and tr.stages[-1]["name"] == "board":
            tr.stages[-1].update(status="skipped", summary={"error": str(exc)[:300]})
        log.warning("board warm-up failed for %s", market, exc_info=True)


def _watch_changes(market: str) -> None:
    """Report what moved for the market's watched listings in this snapshot (best effort)."""
    from dip import watch

    try:
        watch.after_processing(market)
    except Exception:
        log.warning("watchlist change detection failed for %s", market, exc_info=True)


def _safe(fn, **kw):
    try:
        fn(**kw)
    except Exception:  # already recorded on the job
        log.exception("background job failed", extra={"job_id": kw.get("job_id")})


def import_raw_folder(raw_dir: Path | None = None) -> list[dict]:
    """Process every data/raw/<market>/<market>_sellersprite.xlsx (v1 exports) into v2."""
    from dip.settings import PROJECT_ROOT
    raw_dir = raw_dir or PROJECT_ROOT / "data" / "raw"
    out = []
    for d in sorted(p for p in raw_dir.iterdir() if p.is_dir()):
        f = d / f"{d.name}_sellersprite.xlsx"
        if not f.exists():
            files = sorted(d.glob("*.xlsx"))
            if not files:
                continue
            f = files[-1]
        s = process_dataset(f, d.name, source_name=f.name)
        out.append({"market": d.name, "products": s["category"]["products"], "segments": s["discovery"]["segments"]})
    return out


_DATE_IN_NAME = __import__("re").compile(r"(20\d{2})[-_.]?(0[1-9]|1[0-2])(?:[-_.]?(0[1-9]|[12]\d|3[01]))?")


def snapshot_date_from_name(name: str) -> str | None:
    """'micromotor_2025-03.xlsx' -> '2025-03-01'; '..._20250315...' -> '2025-03-15'."""
    m = _DATE_IN_NAME.search(name)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3) or '01'}" if m else None


def process_folder(folder: Path, market: str, force: bool = False) -> list[dict]:
    """Batch: every export in a folder as successive snapshots of one market, oldest first
    (snapshot date from the file name when it has one, else file modification order).
    Unchanged files are skipped by the input fingerprint; repeated texts hit the relevance cache."""
    exts = {".xlsx", ".xls", ".csv", ".tsv", ".json", ".jsonl"}
    files = [f for f in Path(folder).iterdir() if f.is_file() and f.suffix.lower() in exts]
    files.sort(key=lambda f: (snapshot_date_from_name(f.name) or "9999", f.stat().st_mtime, f.name))
    out = []
    for f in files:
        t = time.perf_counter()
        s = process_dataset(f, market, snapshot_date=snapshot_date_from_name(f.name), source_name=f.name, force=force)
        out.append({"file": f.name, "snapshot_date": snapshot_date_from_name(f.name), "products": s["category"]["products"],
                    "seconds": round(time.perf_counter() - t, 2), "trend": (s.get("trend") or {}).get("trend"),
                    "change_events": s.get("change_events")})
    return out


def import_ownership_csv(path: Path | None = None) -> int:
    from dmie.engine.ownership import load_ownership_table
    own = load_ownership_table(path)
    n = 0
    with b.session() as s:
        for _, r in own.iterrows():
            eid, cid = b.stable_id("emp", r["employee"]), b.stable_id("cat", r["category_label"])
            if s.get(b.Employee, eid) is None:
                s.add(b.Employee(id=eid, name=r["employee"]))
            cat = s.get(b.Category, cid)
            if cat is None:
                s.add(b.Category(id=cid, label=r["category_label"],
                                 source_listing_count=None if pd.isna(r["listing_count"]) else int(r["listing_count"])))
            s.flush()
            if not s.query(b.Ownership).filter_by(employee_id=eid, category_id=cid).first():
                s.add(b.Ownership(employee_id=eid, category_id=cid, basis=f"ownership file {r['source']}"))
                n += 1
    return n
