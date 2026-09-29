# Universal Market Intelligence Engine — Audit & Architecture Plan (2026-09-24)

This document records (1) the audit of the Phase-1 prototype and (2) the
architecture of the new **Universal Market Intelligence Engine**
(`src/dmie/engine/`), which is layered *on top of* the prototype. Nothing
in the Phase-1 pipeline was deleted or rewritten.

## 1. Audit of the Phase-1 prototype

| Area | What exists | Limitation for a universal engine |
|---|---|---|
| Ingestion | `dmie.ingestion.excel_loader`, `dmie.connectors.sellersprite`, `scripts/ingest.py` | Hard-wired to SellerSprite's Chinese column names (`dmie.ingestion.schema.COLUMN_MAP`). No CSV/JSON/API path. |
| Cleaning | `dmie.cleaning.normalize`, `dmie.cleaning.validation`, `dmie.validation.dataset_validator` | Rule-based validity checks only; no statistical outlier detection, no per-record confidence. |
| Relevance | `dmie.classification.classifier` (rules → LLM) | Per-category keyword rules + paid LLM calls. The `implants` run stalled when the API budget ran out (PROGRESS.md) — a concrete demonstration of the vendor-dependency risk. |
| Product types | `dmie.classification.product_type_discovery` / `product_type_classifier` | Frozen, hand-written taxonomy per category; discovery produced one supercluster on heterogeneous data. |
| Entity resolution | `dmie.matching.*` | Good blocking/scoring; ambiguous pairs deferred to LLM arbitration. |
| Market metrics | `dmie.market.*` | Solid, deterministic, tested. Reused. |
| Trends | `dmie.market.trends` | Snapshot growth rates only; no forecasting. |
| Opportunity | `dmie.opportunity.{signals,scoring,vector,simulation}` | Signal-based; several dimensions unavailable. |
| Reviews | `dmie.reviews.*` | LLM extraction; no local NLP. |
| Graph | `api/graph.py` | API-level tree; no graph model or persistence. |
| Suppliers / consumer mode | — | Missing. |
| Personnel | `dmie.database.personnel`, `data/raw/employee_category_ownership.csv` | Exists; reused for Module 13. |
| Dashboard | Streamlit (9 pages) + FastAPI | Reads Phase-1 tables only. |
| Tests | 531 tests | 45 integration tests require a locally-built `database/dmie.duckdb` (gitignored) and fail on a fresh clone — pre-existing and unrelated to this change. |

**Conclusion:** the prototype is correct and careful, but category-specific
and LLM-dependent. The engine below makes the core fully offline and
schema-agnostic while keeping every Phase-1 module in place.

## 2. Architecture

```
            any file / API
                 │
   ┌─────────────▼─────────────┐
   │ M1 ingestion              │  schema_detector.py + adapters (sellersprite, generic file, json/api)
   │    → MarketRecord frame   │
   └─────────────┬─────────────┘
   ┌─────────────▼─────────────┐
   │ M2 quality                │  missing / duplicates / IQR / z-score / IsolationForest → data_confidence
   └─────────────┬─────────────┘
   ┌─────────────▼─────────────┐
   │ M3 relevance              │  TF-IDF (word + char n-grams) prototypes + LogisticRegression on corrections → 0-100
   └─────────────┬─────────────┘
   ┌─────────────▼─────────────┐
   │ features.py               │  text (TF-IDF→SVD) + numeric + generic spec extraction (rpm, W, V, mm, g, pcs…)
   └──────┬──────────────┬─────┘
   ┌──────▼──────┐ ┌─────▼──────────┐
   │ M4 discovery│ │ M5 dedup       │  DBSCAN (auto-eps) → K-Means fallback │ identity graph (NetworkX)
   └──────┬──────┘ └─────┬──────────┘
          └──────┬───────┘
   ┌─────────────▼─────────────┐
   │ M6 market  M7 forecast    │  capacity, HHI, price tiers │ MA, Holt, ARIMA, slope
   │ M8 opportunity M9 pain    │  weighted 0-100 score       │ lexicon sentiment + aspects
   │ M10 simulation            │  Monte Carlo with price elasticity
   └─────────────┬─────────────┘
   ┌─────────────▼─────────────┐
   │ M11 knowledge graph       │  Category→Family→Segment→Product→Listing→Brand→Supplier
   │ M12 suppliers M13 owners  │
   │ M15 consumer M16 AI(opt.) │
   └─────────────┬─────────────┘
          DuckDB `mi_*` tables  →  Streamlit pages 10-13 + FastAPI /api/engine
```

### Design rules

* **Offline core.** Every module runs with numpy / pandas / scikit-learn /
  statsmodels / NetworkX. `dmie.engine.assistant` is the only place an LLM
  may be used, and it has a deterministic offline fallback.
* **No hardcoded categories.** Column synonyms, the relevance domain
  lexicon, review aspects, opportunity weights and thresholds all live in
  `config/engine/*.yaml`. A new industry is a new lexicon file, not code.
* **Listing ≠ product.** M5 produces a Product Master; all market metrics
  in M6 are computed on products, with listing counts kept separately.
* **Nothing silently discarded.** Irrelevant or low-quality records are
  flagged with reasons, never dropped from `mi_records`.
* **Traceability.** Every run gets an `engine_run_id`; every table row
  carries it. Relevance corrections are stored in `mi_relevance_feedback`
  and become training data on the next run.
* **Raw data immutable.** The engine reads `data/raw/` and never writes to it.
* **Honest outputs.** Forecasts need ≥3 time periods; otherwise status is
  `insufficient_history`. Pain analysis with no review text reports
  `no_review_data`. Suppliers come only from user-supplied data.

### Package layout

```
src/dmie/engine/
  records.py            MarketRecord + universal column list
  config.py             loads config/engine/*.yaml
  ingestion/
    schema_detector.py  header-synonym + value-profile column detection
    adapters.py         SellerSprite, generic file (csv/xlsx/json), API/JSON adapters
  quality.py            M2
  relevance.py          M3
  features.py           shared feature extraction + spec parser
  discovery.py          M4
  dedup.py              M5
  market.py             M6
  forecast.py           M7
  opportunity.py        M8
  pain.py               M9
  simulation.py         M10
  graph.py              M11
  suppliers.py          M12
  ownership.py          M13
  recommend.py          M15
  assistant.py          M16 (optional AI)
  store.py              DuckDB persistence (mi_* tables)
  pipeline.py           orchestration: run_engine()
scripts/run_engine.py   CLI
dashboard/pages/10-13   engine pages (upload, market map 3D, simulator/consumer, suppliers/owners)
api/routers/engine.py   read-only engine API
```

## 3. Phase status

| Phase | Status |
|---|---|
| 1 Architecture refactor | New `dmie.engine` package, Phase-1 untouched |
| 2 Universal ingestion | M1 done |
| 3 Data purification | M2 + M3 done |
| 4 Product discovery | M4 done |
| 5 Deduplication | M5 done |
| 6 Market analysis | M6 done |
| 7 Forecasting | M7 done (needs time-stamped data to produce forecasts) |
| 8 Opportunity engine | M8 + M9 + M10 done |
| 9 Knowledge graph | M11 done |
| 10 Dashboard | Pages 10-13 done |
| 11 Supplier intelligence | M12 done (empty until a supplier file is imported) |
| 12 Final integration | `scripts/run_engine.py`, smoke tests, verification run |

## 4. Verification (2026-09-24)

* **Pipeline run:** all three real SellerSprite exports (dental_models,
  denture_base, implants) run end-to-end offline, ~6 s each.
* **Dataset upload test:** a CSV with unfamiliar headers (`Item Code`,
  `Product Name`, `Maker`, `Cost to customer`, `Units/Month`, `Col_X`)
  uploaded through the dashboard. Price and revenue were identified purely
  from the `revenue ≈ price × sales` identity.
* **Market analysis test:** planted segments, duplicates, growth trends and
  review complaints were all recovered (tests/engine/test_engine_smoke.py).
* **UI verification:** every tab of pages 10–13 was driven in headless
  Chromium with no exceptions.

## 5. Domain vs. category relevance

M3 answers "is this a *dental* product?" (domain relevance), so a
denture-base market keeps adhesives and repair kits, and M4 separates them
into their own segments. Phase 1's stricter per-category relevance
("is this *denture base*?") still exists in `dmie.classification` for
curated categories. The two are complementary.

## 6. Next steps

* Feed dated monthly exports (`--snapshot-date`) so M7 can forecast.
* Import supplier lists and review exports to populate M9/M12.
* Correct relevance decisions in the review queue; the local model starts
  learning from them at 8 labels.
* Image similarity in M5 currently compares image IDs only. Perceptual
  hashing can be added if image files become available.
