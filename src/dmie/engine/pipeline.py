"""Engine orchestration (Phase 12 -- final integration).

    run_engine("any_export.xlsx", market_name="micromotor")

ingest (M1) -> quality (M2) -> relevance (M3) -> discovery (M4) ->
dedup (M5) -> market (M6) -> forecast (M7) -> pain (M9) ->
opportunity (M8) -> suppliers (M12) -> graph (M11) -> persist.

Every stage is offline. Every record -- including irrelevant and
low-quality ones -- is persisted with its reasons (PRINCIPLES.md principle 8).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import pandas as pd

from dmie.engine import store
from dmie.engine.config import section
from dmie.engine.dedup import resolve_products
from dmie.engine.discovery import discover_segments
from dmie.engine.forecast import Forecast, forecast_market, launch_cohort
from dmie.engine.graph import build_graph, graph_tables
from dmie.engine.ingestion import ingest
from dmie.engine.market import compute_market
from dmie.engine.opportunity import score_products, score_segments
from dmie.engine.ownership import market_owner_hints
from dmie.engine.pain import PainReport, analyze_reviews, pain_by_scope, review_pain_score
from dmie.engine.quality import assess_quality
from dmie.engine.relevance import classify_relevance
from dmie.engine.suppliers import normalize_suppliers, score_suppliers

logger = logging.getLogger("dmie.engine")
GLOBAL = "__global__"


@dataclass
class EngineResult:
    run_id: str
    market_name: str
    records: pd.DataFrame
    products: pd.DataFrame
    segments: pd.DataFrame
    families: pd.DataFrame
    forecasts: list[Forecast]
    pain: dict[str, PainReport]
    suppliers: pd.DataFrame
    supplier_matches: pd.DataFrame
    graph: nx.DiGraph
    dedup_edges: pd.DataFrame
    summary: dict = field(default_factory=dict)


def _run_id(market_name: str, started: datetime) -> str:
    return hashlib.sha1(f"{market_name}|{started.isoformat()}".encode()).hexdigest()[:16]


def _reviews_frame(records: pd.DataFrame, reviews: pd.DataFrame | None, id_to_product: dict) -> pd.DataFrame:
    parts = []
    if records["review_text"].notna().any():
        r = records.dropna(subset=["review_text"])
        parts.append(pd.DataFrame({"id": r["id"], "text": r["review_text"], "rating": r["rating"]}))
    if reviews is not None and len(reviews):
        cols = {str(c).lower(): c for c in reviews.columns}
        text_col = next((cols[c] for c in ("text", "review_text", "review", "body", "content", "comment") if c in cols), None)
        id_col = next((cols[c] for c in ("id", "asin", "listing_id", "product_id") if c in cols), None)
        if text_col is not None:
            parts.append(pd.DataFrame({
                "id": reviews[id_col].astype(str) if id_col else None,
                "text": reviews[text_col],
                "rating": pd.to_numeric(reviews[cols["rating"]], errors="coerce") if "rating" in cols else None,
            }))
    if not parts:
        return pd.DataFrame(columns=["id", "text", "rating", "product_id"])
    df = pd.concat(parts, ignore_index=True)
    df["product_id"] = df["id"].map(id_to_product)
    return df


def run_engine(source, market_name: str, domain: str | None = None, reviews: pd.DataFrame | None = None,
               suppliers: pd.DataFrame | None = None, overrides: dict | None = None,
               snapshot_date: str | None = None, db_path: str | Path | None = None,
               persist: bool = True) -> EngineResult:
    started = datetime.now(timezone.utc)
    run_id = _run_id(market_name, started)
    domain = domain or section("relevance").get("domain", "dental")
    con = store.connect(db_path) if persist else None

    # M1 -- ingestion
    ing = ingest(source, name=None if isinstance(source, (str, Path)) else market_name, overrides=overrides)
    # M2 -- quality
    q = assess_quality(ing.frame)
    # M3 -- relevance (+ human corrections as training data)
    feedback = store.read_feedback(con, domain) if con is not None else pd.DataFrame(columns=["record_key", "text", "label"])
    corrections = {k: bool(v) for k, v in zip(feedback["record_key"], feedback["label"])}
    rel = classify_relevance(q.frame, feedback[["text", "label"]], corrections, domain)

    # current market state: relevant, usable, latest snapshot per native id
    current = rel[rel["is_relevant"] & rel["usable_for_market"]].copy()
    if current["timestamp"].notna().any():
        current = current.sort_values("timestamp").drop_duplicates("id", keep="last")
    # M4 -- discovery, M5 -- deduplication
    disc = discover_segments(current)
    dd = resolve_products(disc.frame)
    listings = dd.frame
    # M6 -- market capacity
    mk = compute_market(dd.products, listings, disc.segments)

    # M7 -- forecasting (dataset timestamps, else accumulated run history)
    seg_of_id = dict(zip(listings["id"], listings["segment_id"]))
    ts_records = rel[rel["is_relevant"] & rel["timestamp"].notna()].assign(segment_id=lambda d: d["id"].map(seg_of_id))
    labels = dict(zip(mk.segments["segment_id"], mk.segments["segment_label"])) if len(mk.segments) else {}
    labels["__market__"] = "__market__"
    history = None
    if con is not None and not rel["timestamp"].notna().any():
        period = pd.Timestamp(snapshot_date).date() if snapshot_date else started.date()
        hist_rows = [{"subject": "__market__", "subject_label": "__market__",
                      "revenue": mk.category.get("monthly_revenue"), "sales": mk.category.get("monthly_sales")}]
        for _, s in mk.segments.iterrows():
            hist_rows.append({"subject": s["segment_label"], "subject_label": s["segment_label"],
                              "revenue": s["monthly_revenue"], "sales": s["monthly_sales"]})
        store.append_history(con, market_name, period, hist_rows, run_id)
        history = store.read_history(con, market_name)
    forecasts = forecast_market(ts_records, history, labels)
    market_fc = next((f for f in forecasts if f.subject == "__market__"), forecasts[0] if forecasts else None)
    growth = {}
    label_to_seg = {v: k for k, v in labels.items()}
    for f in forecasts:
        if f.status == "ok" and f.growth_rate is not None and f.subject != "__market__":
            growth[label_to_seg.get(f.subject, f.subject)] = f.growth_rate
    cohort = launch_cohort(rel[rel["is_relevant"]])

    # M9 -- pain analysis
    id_to_product = dict(zip(listings["id"], listings["product_id"]))
    rv = _reviews_frame(rel, reviews, id_to_product)
    pain: dict[str, PainReport] = {"__market__": analyze_reviews(rv["text"].tolist(), rv["rating"].tolist(), "__market__")}
    seg_pain: dict[str, float] = {}
    if len(rv):
        prod_seg = dict(zip(dd.products["product_id"], dd.products["segment_id"]))
        rv["segment_id"] = rv["product_id"].map(prod_seg)
        for k, rep in pain_by_scope(rv.dropna(subset=["segment_id"]), "segment_id").items():
            pain[f"segment:{k}"] = rep
            seg_pain[k] = review_pain_score(rep)
        for k, rep in pain_by_scope(rv.dropna(subset=["product_id"]), "product_id").items():
            pain[f"product:{k}"] = rep

    # M8 -- opportunity
    momentum = cohort.get("new_listing_revenue_share") if cohort.get("status") == "ok" else None
    seg_scored = score_segments(mk.segments, growth, seg_pain, momentum)
    products = score_products(mk.products, seg_scored)
    listings = listings.merge(products[["product_id", "opportunity_score"]], on="product_id", how="left")

    # M12 -- suppliers (global table + any supplied now)
    sup_raw = store.read_table(con, "mi_suppliers", GLOBAL) if con is not None else pd.DataFrame()
    if suppliers is not None and len(suppliers):
        sup_raw = pd.concat([sup_raw, normalize_suppliers(suppliers)], ignore_index=True) if len(sup_raw) else normalize_suppliers(suppliers)
        sup_raw = sup_raw.drop_duplicates("supplier_id", keep="last")
    if len(sup_raw):
        sup_cols = [c for c in normalize_suppliers(pd.DataFrame(columns=["name"])).columns if c in sup_raw]
        sup_scored, sup_matches = score_suppliers(sup_raw[sup_cols], seg_scored)
    else:
        sup_scored, sup_matches = pd.DataFrame(), pd.DataFrame(columns=["supplier_id", "segment_id", "segment_label", "match_score"])

    # M11 -- knowledge graph
    G = build_graph(market_name, disc.families, seg_scored, products, listings,
                    sup_scored if len(sup_scored) else None, sup_matches if len(sup_matches) else None)

    # full record table: every record, with reasons, plus segment/product for the current ones
    join_cols = ["record_id", "segment_id", "segment_label", "family_id", "segment_confidence", "product_id",
                 "is_best_listing", "opportunity_score"]
    records = rel.merge(listings[join_cols], on="record_id", how="left")
    records["excluded_reason"] = None
    records.loc[~records["is_relevant"], "excluded_reason"] = "not relevant: " + records["relevance_status"]
    records.loc[records["is_relevant"] & ~records["usable_for_market"], "excluded_reason"] = "low data quality: " + records["quality_issues"].map(lambda x: ", ".join(x))
    stale = records["is_relevant"] & records["usable_for_market"] & records["product_id"].isna()
    records.loc[stale, "excluded_reason"] = "older snapshot of a listing (history only)"

    summary = {
        "market_name": market_name,
        "run_id": run_id,
        "domain": domain,
        "generated_at": started.isoformat(),
        "ingestion": ing.summary(),
        "quality": q.summary(),
        "relevance": {
            "relevant": int(rel["is_relevant"].sum()),
            "uncertain": int((rel["relevance_status"] == "uncertain").sum()),
            "irrelevant": int(rel["relevance_status"].isin(["irrelevant", "human_irrelevant"]).sum()),
            "human_corrections_applied": int(rel["relevance_status"].str.startswith("human").sum()),
            "feedback_labels_available": int(len(feedback)),
        },
        "discovery": {"families": int(len(disc.families)), "segments": int(len(disc.segments)), "method": disc.method},
        "dedup": dd.summary(),
        "category": mk.category,
        "forecast": market_fc.to_dict() if market_fc else {},
        "launch_cohort": cohort,
        "pain": {"status": pain["__market__"].status, "reviews": pain["__market__"].reviews},
        "suppliers": {"count": int(len(sup_scored)), "matches": int(len(sup_matches))},
        "graph": {"nodes": G.number_of_nodes(), "edges": G.number_of_edges()},
        "owners": market_owner_hints(rel),
    }
    result = EngineResult(run_id, market_name, records, products, seg_scored, disc.families, forecasts, pain,
                          sup_scored, sup_matches, G, dd.edges, summary)
    if con is not None:
        persist_result(con, result, ing.source, ing.adapter, started)
        con.close()
    return result


def persist_result(con, r: EngineResult, source: str, adapter: str, started: datetime) -> None:
    m = r.market_name
    rec = r.records.drop(columns=["description"], errors="ignore").assign(run_id=r.run_id)
    store.replace_rows(con, "mi_records", m, rec)
    store.replace_rows(con, "mi_products", m, r.products.assign(run_id=r.run_id))
    store.replace_rows(con, "mi_segments", m, r.segments.assign(run_id=r.run_id))
    store.replace_rows(con, "mi_families", m, r.families.assign(run_id=r.run_id))
    fc = pd.DataFrame([{"subject": f.subject, "status": f.status, "payload": f.to_dict()} for f in r.forecasts])
    store.replace_rows(con, "mi_forecasts", m, fc)
    pn = pd.DataFrame([{"scope": k, "status": v.status, "payload": v.to_dict()} for k, v in r.pain.items()])
    store.replace_rows(con, "mi_pain", m, pn)
    store.replace_rows(con, "mi_supplier_matches", m, r.supplier_matches)
    nodes, edges = graph_tables(r.graph)
    store.replace_rows(con, "mi_graph_nodes", m, nodes)
    store.replace_rows(con, "mi_graph_edges", m, edges)
    store.replace_rows(con, "mi_dedup_pairs", m, r.dedup_edges.drop(columns=["a", "b"]))
    owners = r.summary.get("owners", {})
    store.replace_rows(con, "mi_markets", m, pd.DataFrame([{
        "run_id": r.run_id, "summary": r.summary, "owners": owners.get("owners", {}),
        "category_labels": owners.get("category_labels", {}), "updated_at": datetime.now(timezone.utc),
    }]))
    store.record_run(con, r.run_id, m, source, adapter, started, "ok", {
        k: r.summary[k] for k in ("quality", "relevance", "dedup", "category") if k in r.summary})


def save_suppliers(raw: pd.DataFrame, db_path: str | Path | None = None) -> int:
    """Import user-supplied suppliers into the global supplier table."""
    con = store.connect(db_path)
    try:
        new = normalize_suppliers(raw)
        old = store.read_table(con, "mi_suppliers", GLOBAL)
        if len(old):
            old = old[[c for c in new.columns if c in old]]
            new = pd.concat([old, new], ignore_index=True).drop_duplicates("supplier_id", keep="last")
        return store.replace_rows(con, "mi_suppliers", GLOBAL, new)
    finally:
        con.close()
