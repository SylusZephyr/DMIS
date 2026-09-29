# ARCHITECTURE V2 — Dental Market Intelligence Operating System

**Status:** plan approved for execution · **Date:** 2026-09-24
**Principle:** *Do not rebuild. Do not delete.* Everything built in M1–M16 and
the Universal Engine becomes **Intelligence Core v1** and keeps running
unchanged. **Intelligence Platform v2** is added around it.

---

## 1. Current system (audit)

### 1.1 Inventory

| Area | Location | Size | State |
|---|---|---|---|
| Phase-1 core (M1–M16) | `src/dmie/{ingestion,cleaning,classification,matching,market,opportunity,reviews,validation,database,ai,connectors}` | ~5.0k LOC | Working, tested, AI-assisted (optional) |
| Universal Engine (offline) | `src/dmie/engine/` | ~3.4k LOC | Working: schema detection, quality, TF-IDF relevance, DBSCAN/K-Means discovery, identity-graph dedup, market, forecast, opportunity, pain, simulation, graph, suppliers, ownership, recommendations |
| Database | `src/dmie/database/schema.sql` + `mi_*` tables | 21 + 14 tables | DuckDB single file |
| API | `api/` (FastAPI, read-only) | ~1.0k LOC | `/api/categories`, `/products`, `/employees`, `/graph`, `/engine` |
| Dashboard | `dashboard/` (Streamlit, 13 pages) | ~3.2k LOC | Prototype-grade; single-process; Plotly |
| CLI | `scripts/` | ~1.7k LOC | `run_pipeline.py` (v1), `run_engine.py` (engine) |
| Tests | `tests/` | 507 tests | 45 integration tests need a locally built DB |
| Config | `config/*.yaml`, `config/engine/*.yaml` | — | All category logic externalised |
| Docs | `docs/`, `PROGRESS.md`, `DECISIONS.md` | 23 docs | Complete decision trail |
| Data | `data/raw/` (immutable), `data/validated/` (gold) | 3 real markets | denture_base, dental_models, implants |

### 1.2 Strengths to preserve

1. **Listing ≠ Product** is enforced everywhere (v1 `matching/`, engine `dedup.py`).
2. **Traceability**: decision log, pipeline runs, dataset versions, human-review queues.
3. **Honest numbers**: missing ≠ 0, coverage reported, no fabricated forecasts/suppliers.
4. **Category-agnostic engine**: schema detector + domain lexicons in YAML.
5. **Offline core**: the engine runs with no LLM; AI is optional.

### 1.3 Limits that v2 must remove

| Limit | Evidence | Consequence |
|---|---|---|
| Single-file DuckDB for everything (business + analytics + graph) | `database/dmie.duckdb` | Write-lock conflicts (dashboard vs. scripts, documented in PROGRESS.md); no multi-user business data |
| pandas + in-memory scikit-learn over full datasets | `engine/dedup.py`, `discovery.py` | Comfortable to ~10⁵ listings, not 10⁶–10⁷ |
| No data lake / raw-zone history | uploads go straight to tables | Re-processing, lineage and time series depend on re-uploading |
| Graph is recomputed per run and stored as flat tables | `mi_graph_nodes/edges` | No cross-market graph queries (COMPETES_WITH across categories, SUPPLIED_BY) |
| No vector index | TF-IDF matrices built per run | Similar-product search and dedup blocking are O(n log n) per run and not persistent |
| Streamlit UI | `dashboard/` | Cannot deliver a 3D command-center experience, multi-user sessions, or fine-grained interactivity |
| No auth / permissions / multi-tenant business DB | — | Cannot hand to multiple teams |

---

## 2. Target architecture

```
 DATA SOURCES   SellerSprite · Amazon exports · CSV/Excel · APIs · supplier lists · offline/exhibition data
      │
      ▼
 DATA LAKE      data/lake/  (Parquet, Hive-partitioned)         ← dip.lake
                raw/  source=<s>/dataset=<id>/   (immutable, as received)
                std/  dataset=<id>/              (universal MarketRecord schema)
                curated/ market=<m>/run=<r>/     (products, segments, metrics)
      │
      ▼
 DATA PROCESSING ENGINE   dip.pipeline.{ingestion,cleaning,relevance}
                          Polars/DuckDB scans · schema detection · ingestion report · quality · dental confidence
      │
      ▼
 PRODUCT INTELLIGENCE     dip.pipeline.{product_resolution,clustering}
                          identity graph + Qdrant ANN blocking · segment discovery (DBSCAN/HDBSCAN/K-Means)
      │
      ▼
 KNOWLEDGE GRAPH          dip.storage.graph  → Neo4j (server) | embedded NetworkX+DuckDB (fallback)
      │
      ▼
 MARKET ANALYTICS         dip.pipeline.analytics  (DuckDB SQL over Parquet)
      │
      ▼
 OPPORTUNITY / FORECAST   dip.pipeline.{opportunity,forecasting}
      │
      ▼
 SUPPLY CHAIN             dip.pipeline.supplier  + PostgreSQL business DB
      │
      ▼
 PLATFORM API             dip.api  (FastAPI  /api/v2/*)   — v1 /api/* stays mounted
      │
      ▼
 3D INTELLIGENCE UI       frontend/  Next.js · TypeScript · React Three Fiber · Sigma.js · ECharts · Tailwind · shadcn/ui
```

### 2.1 Relationship between v1 and v2

```
                ┌──────────────────── Intelligence Platform v2 (src/dip, frontend/) ─────────────────────┐
                │  lake · storage adapters · pipeline stages · /api/v2 · Next.js command center          │
                │                          │ calls (never copies)                                       │
                │  ┌───────────── Intelligence Core v1 (src/dmie) — unchanged ─────────────┐             │
                │  │ dmie.engine.*  (schema detection, quality, relevance, discovery,     │             │
                │  │                 dedup, market, forecast, opportunity, pain, suppliers)│             │
                │  │ dmie.{classification,matching,market,...} (Phase-1 curated pipeline)  │             │
                │  └──────────────────────────────────────────────────────────────────────┘             │
                └───────────────────────────────────────────────────────────────────────────────────────┘
```

v2 **wraps** v1 algorithms behind stage interfaces. A v2 stage may later
replace a v1 algorithm with a faster one (e.g. Polars quality checks), but
only behind the same interface and only after it reproduces v1's results
on the three real markets.

### 2.2 Storage — hybrid, pluggable

| Store | Role | Server mode | Embedded fallback (no Docker) |
|---|---|---|---|
| **PostgreSQL** | Business DB: users, employees, categories, ownership, permissions, suppliers, companies, datasets, jobs | `DIP_POSTGRES_URL=postgresql+psycopg://…` | SQLite file `data/platform/business.db` (same SQLAlchemy models) |
| **DuckDB** | Analytics over the lake (Parquet) | `DIP_DUCKDB_PATH` | same |
| **Neo4j** | Knowledge graph | `DIP_NEO4J_URI` / user / password | NetworkX graph persisted to DuckDB tables (same `GraphStore` API; Cypher export kept) |
| **Qdrant** | Vector similarity (similar products, dedup blocking, unknown-product clustering) | `DIP_QDRANT_URL` | `qdrant-client` local mode on disk (`data/platform/qdrant/`) — the real Qdrant API, no server |

The platform must run on a laptop with zero services and scale by setting
environment variables — no code change. `infra/docker-compose.yml` brings up
Postgres, Neo4j and Qdrant.

**Graph model (Neo4j):**
`(:Category)-[:HAS_FAMILY]->(:ProductFamily)-[:HAS_SEGMENT]->(:Segment)-[:HAS_MODEL]->(:Product)-[:HAS_LISTING]->(:Listing)-[:SOLD_BY]->(:Seller)`
plus `(:Product)-[:MADE_BY]->(:Brand)`, `(:Product)-[:PRODUCT_SIMILAR_TO {score}]->(:Product)`,
`(:Product)-[:COMPETES_WITH]->(:Product)`, `(:Product|Segment)-[:SUPPLIED_BY {score}]->(:Supplier)`,
`(:Supplier)-[:LOCATED_IN]->(:Country)`, `(:X)-[:BELONGS_TO]->(:Category)`.

**Vectors (Qdrant):** collection `products` — one point per product, a
hashed char-n-gram + word TF-IDF embedding (fixed dimension, deterministic,
no model download); payload = market, segment, brand, price, sales. Optional
upgrade: a local sentence-embedding model when available.

### 2.3 Pipeline package (`src/dip/pipeline/`)

| Stage | Wraps (v1) | v2 additions |
|---|---|---|
| `ingestion/` | `dmie.engine.ingestion` | Lake write (raw + std Parquet), **ingestion report** (accepted / rejected with reasons: missing price, duplicate, invalid ASIN, unparseable row), chunked Polars reading |
| `cleaning/` | `dmie.engine.quality` | Rejection rules as data; per-record reasons persisted |
| `relevance/` | `dmie.engine.relevance`, `dmie.classification` | Dental confidence **with per-signal explanation** (title / category / image-URL tokens); optional XGBoost when ≥N labels |
| `product_resolution/` | `dmie.engine.dedup` | Qdrant ANN blocking for 10⁶+; Product Master with best listing |
| `clustering/` | `dmie.engine.discovery` | Hierarchy Family → Segment → Model (spec-driven sub-clusters) |
| `analytics/` | `dmie.engine.market` | DuckDB SQL implementations over curated Parquet for scale |
| `forecasting/` | `dmie.engine.forecast` | Regression + optional Prophet; trend labels (Stable / Growing / Declining) at 6 and 12 months |
| `opportunity/` | `dmie.engine.opportunity` | v2 formula: Demand + Growth + Pain + Competition gap + Supplier availability − Difficulty |
| `supplier/` | `dmie.engine.suppliers` | Postgres-backed supplier DB, SUPPLIED_BY edges, country geography |

`dip.pipeline.runner` executes stages as a **job** (status, timings, stage
reports) recorded in the business DB, so the UI can show live progress.

### 2.4 API (`src/dip/api`, `/api/v2`)

`POST /datasets` (upload → job) · `GET /jobs/{id}` · `GET /markets` ·
`GET /markets/{m}` (summary + ingestion report) · `/markets/{m}/segments` ·
`/markets/{m}/products` (paged, sortable) · `/products/{id}` (detail: listings,
competition, suppliers, complaints, opportunity) · `/products/{id}/similar` (Qdrant) ·
`/markets/{m}/galaxy` (nodes+edges+3D layout) · `/universe` (industry → category tree) ·
`/graph/explore?node=&depth=` · `/geo` (supplier/company geography) ·
`/suppliers` CRUD + import · `/employees`, `/employees/{id}/dashboard` ·
`/recommend?q=` (shopping mode) · `/simulate` · `/ask` (optional AI).

The v1 read-only API (`api/`) is mounted unchanged under `/api`.

### 2.5 Frontend (`frontend/`, Next.js App Router)

| Screen | Tech | Content |
|---|---|---|
| **Global Command Center** (home) | React Three Fiber globe | Countries: supplier counts, market presence, opportunity; KPI HUD |
| **Market Universe** | R3F | Industry → categories → segments as orbiting nodes; size = market value, color = opportunity, glow = growth |
| **Product Galaxy** | R3F instanced meshes | Products as stars; size = sales, brightness = opportunity, links = PRODUCT_SIMILAR_TO |
| **Product Detail** | shadcn + ECharts | Image, market data, listings, competition, suppliers, complaints, opportunity breakdown |
| **Knowledge Graph Explorer** | Sigma.js (WebGL) | Expand/collapse, filters by node type |
| **Market Analytics** | ECharts | Market matrix (price × sales, bubble = revenue, color = opportunity), segments, tiers, forecasts |
| **Data Ops** | shadcn | Upload → live job progress → ingestion report |
| **Shopping Mode** | shadcn | Guided: need → budget → purpose → experience → ranked visual results |
| **Product Manager Mode / My Portfolio** | shadcn + ECharts | Owned categories, opportunities, potential products, supplier matches |
| **Suppliers** | shadcn + globe | Supplier DB, scores, matches |

Mapbox needs an access token → the globe is built with Three.js/R3F
(no token); a Mapbox 2D map is an optional layer enabled by
`NEXT_PUBLIC_MAPBOX_TOKEN`. D3 is used for scales/layout helpers.

---

## 3. Migration plan

1. **Nothing is moved or renamed.** `src/dmie`, `api/`, `dashboard/`, `scripts/`, `tests/` stay where they are and keep passing.
2. v2 lives in **new** top-level locations: `src/dip/`, `frontend/`, `infra/`, `data/lake/`, `data/platform/`.
3. v2 stages call v1 functions. Behaviour changes happen only inside v2 wrappers.
4. Streamlit remains available as the **analyst console** until the Next.js UI reaches feature parity, then is retired from the default run path (not deleted).
5. Existing DuckDB tables (`categories`, `listings`, `products`, `mi_*`, …) are read by a **v1 import job** that loads them into the lake/graph/vector stores, so historical work flows into v2.

---

## 4. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Server dependencies (Postgres/Neo4j/Qdrant) block local use | Team can't run it | Embedded fallbacks behind the same interfaces; CI runs in fallback mode |
| v2 wrappers drift from v1 numbers | Silent metric changes | Parity tests: v2 stage output == v1 engine output on the 3 real markets |
| Scale: pandas/sklearn stages at 10⁶+ rows | Memory/time | Partition by market/segment; Qdrant ANN blocking; DuckDB SQL analytics; Polars streaming ingestion; MiniBatchKMeans fallback for discovery |
| 3D UI performance with 10⁵ nodes | Unusable UI | Instanced meshes, server-side layout + LOD sampling (top-N by sales, aggregate the rest), progressive loading |
| Fabricated geography | Misleading globe | Countries only from real fields (supplier country, marketplace); empty states otherwise |
| Scope creep (auth, multi-tenant) before core works | Delays | Phase order below; auth scaffolding (users/permissions tables) first, enforcement later |
| Prophet / XGBoost heavy installs | Setup friction | Optional extras; stages detect availability and report which method ran |
| Raw data duplication (`dental_models_sellersprite_v1.xlsx` == denture_base export) | Wrong market | Lake stores a content hash per dataset; duplicate uploads are detected and reported |

---

## 5. Implementation sequence

| Phase | Deliverable | Done when |
|---|---|---|
| **1 Architecture migration** | `src/dip` skeleton, config, storage adapters (business/graph/vector/lake), `/api/v2/health`, Next.js shell, docker-compose | API + frontend start; storage adapters pass smoke tests in fallback mode |
| **2 Data Intelligence Engine** | Lake + ingestion report + cleaning + relevance explanation + product resolution + clustering hierarchy, job runner, v1 import | Upload → job → report → products for the 3 real markets, parity with v1 engine |
| **3 Knowledge Graph** | GraphStore (Neo4j + embedded), full graph model incl. SIMILAR_TO/COMPETES_WITH/SUPPLIED_BY | `/graph/explore` works in both modes |
| **4 Analytics Engine** | Market capacity/competition/opportunity v2/forecast labels via DuckDB | `/markets/{m}` + analytics endpoints |
| **5 Supplier Engine** | Business-DB suppliers, import, scoring, matching, geography | `/suppliers`, `/geo` |
| **6 3D Interface** | Command Center globe, Market Universe, Product Galaxy, Graph Explorer, Product Detail, Shopping & PM modes, My Portfolio | Every screen backed by real API data |
| **7 Final integration** | End-to-end demo: upload → processing → product intelligence → opportunity → suppliers → 3D | Scripted E2E run + browser verification |

Progress against this plan is tracked at the bottom of this file.

---

## 6. Progress log

- **Phase 0 — Audit & plan:** this document.
- **Phase 1 — Architecture migration (done):** `src/dip/` package; `settings.py`
  (env-driven); storage adapters: `business.py` (SQLAlchemy models for users, permissions,
  employees, categories, ownership, companies, suppliers, datasets, jobs, markets;
  PostgreSQL or SQLite), `lake.py` (raw/std/curated Parquet + DuckDB SQL), `graph.py`
  (Neo4j or embedded NetworkX+DuckDB, same API), `vectors.py` (Qdrant server or
  local mode, deterministic hashed embeddings); `infra/docker-compose.yml`; FastAPI app
  with the v1 API still mounted under `/api`.
- **Phase 2 — Data intelligence engine (done):** `dip.pipeline.{ingestion, cleaning,
  relevance, clustering, product_resolution, analytics, forecasting, opportunity,
  supplier}` wrap `dmie.engine`; `runner.py` executes 13 tracked stages as a job.
  Added: ingestion report (accepted/rejected + reasons: missing title/price, invalid
  price/ASIN, duplicate, impossible sales), content-hash duplicate-upload detection,
  per-signal relevance explanation (title / category / image), Family → Segment →
  **Model** hierarchy (spec signatures), industry-branch assignment from
  `config/platform/industry_branches.yaml`, server-side 3D galaxy layout.
  Parity with v1 engine on dental_models: 306 products / 27 segments (identical).
- **Phase 3 — Knowledge graph (done):** full model incl. PRODUCT_SIMILAR_TO (vector
  neighbours ≥ 0.55), COMPETES_WITH (price-adjacent in segment), SUPPLIED_BY,
  LOCATED_IN, BELONGS_TO; `/graph/explore`, `/graph/search`, `/graph/stats`.
- **Phase 4 — Analytics (done):** v2 opportunity = Demand + Growth + Customer pain +
  Competition gap + Supplier availability − Difficulty (evidence coverage, unverified
  demand cap, v1 score retained as `v1_score`); forecast horizon labels
  (current / 6-month / 12-month); cross-market DuckDB analytics (`universe_totals`,
  `brand_leaderboard`).
- **Phase 5 — Supplier engine (done):** business-DB suppliers, CSV/XLSX import + single
  create, scoring and segment matching, geography via `config/platform/country_centroids.csv`.
- **Phase 6 — 3D interface (done):** `frontend/` Next.js 16 + React 19 + React Three
  Fiber + Sigma.js + ECharts + Tailwind 4 + shadcn-style components. Screens: Command
  Center (3D globe), Market Universe (3D orbits), Product Galaxy (instanced 3D stars),
  Market Analytics (matrix, segment opportunity, price tiers, forecast), Product Detail,
  Knowledge Graph Explorer, Data Operations (upload → live stages → report → corrections),
  Suppliers, My Portfolio (PM mode), Shopping Mode (need → budget → purpose → experience).
- **Phase 7 — Integration (done):** `scripts/dmis.py {bootstrap,process,import-suppliers,serve}`,
  `run_platform.bat` / `run_platform.sh`, `tests/platform/` (5 smoke tests through the
  API), scripted browser E2E on the production build: upload → processing → market →
  galaxy → universe drill-down → graph search → shopping → product → command center,
  zero console errors. Full suite: 481 passed; the 45 failures are the pre-existing
  v1 integration tests that need a locally built `database/dmie.duckdb`.

- **Phase A — Polars ingestion (done):** `pipeline/ingestion/fast.py` lazy-scans
  CSV/XLSX/JSON with Polars, detects the schema with the v1 detector, parses numbers and
  dates with vectorised expressions, applies the same rejection rules and `record_id`
  hash as v1 (row-for-row parity tests on the real exports), and sinks the raw zone
  straight to Parquet. `DIP_INGEST_ENGINE=pandas` forces the v1 path.
  1M rows: ≈120 s → 10.2 s.
- **Phase B — Pipeline at 10⁶ rows (done):** above 5,000 current listings, discovery fits
  on representatives + a 20k sample and assigns by nearest centroid; product resolution
  collapses exact duplicates, finds candidates with per-segment blocked TF-IDF kNN,
  scores pairs vectorised with the v1 vetoes, and builds the Product Master vectorised
  (column-for-column equal to v1). Relevance scores unique texts only. The runner records
  `peak_mb` per stage and releases frames between stages. 1M rows end to end: 80.7 s,
  3.5 GB peak (100k: 138 s → 22.8 s). Below the threshold the v1 algorithms run unchanged.
- **Phase C — Forecasting runtime (done):** the ARIMA order chosen on the full series is
  reused in the backtest; segments run in a spawn-context process pool (from 8 series,
  `DIP_FORECAST_WORKERS`) with BLAS threads pinned to 1. 13.4 s → 3.8 s, identical output.
- **Phase D — Auth & permissions (done):** bearer tokens (`dmis_…`, stored as SHA-256
  only), every `/api/v2` route declares `require(resource, action)` against the role
  permission table; `/auth/me`, `/auth/tokens`, admin `/auth/users`;
  `python scripts/dmis.py create-user`. Enforcement: `DIP_AUTH=on|off` (default on when
  `DIP_POSTGRES_URL` is set, off for the embedded single-user setup). The UI asks for a
  token on 401.
- **Phase E — Models & map (done):** relevance feedback model uses XGBoost when
  `feedback_model: auto`, ≥200 human labels and `pip install -e ".[ml]"`; otherwise
  logistic regression (evidence names the model that ran). Optional Mapbox 2D layer
  beside the 3D globe when `NEXT_PUBLIC_MAPBOX_TOKEN` is set at build time.

- **Enterprise layer (Master Prompt 2, done):** confidence framework, Model/Variant hierarchy,
  trend detection, competitor intelligence, launch evaluation, analyst agent, employee briefs,
  event log + alerts + connectors, incremental processing and caching, product_manager / customer
  roles, decision pages and 3D upgrades. See `docs/ENTERPRISE_AUDIT_AND_ROADMAP.md` and
  `docs/SYSTEM_ARCHITECTURE_FINAL.md`; validation in `docs/BUSINESS_SIMULATION_REPORT.md`.

### Known gaps / next steps (honest status)

| Item | Status |
|---|---|
| Polars streaming ingestion for 10⁶+ rows | Done (Phase A) |
| Discovery/dedup at 10⁶+ | Done (Phase B). Scale-path dedup agrees with v1 on 96–100% of pairs on the test sets; it merges more when a market has many exact duplicates (v1 starves candidates there) — every extra merge passes the v1 rules |
| Forecasting runtime | Done (Phase C) |
| XGBoost relevance model | Done (Phase E), optional `ml` extra |
| Prophet | Optional (`pip install .[forecast-extra]`); `forecasting.prophet_forecast` is available but not in the default ensemble |
| Auth / permission enforcement | Done (Phase D); multi-tenant data isolation is not implemented |
| Mapbox layer | Done (Phase E), optional; real tiles need a token and were not verified in CI |
| 3D UI at 10⁵+ products | Galaxy is instanced; LOD sampling for very large markets not yet implemented |

| Real supplier / review data | None supplied yet — tables and UI stay empty until imported (never fabricated) |
| Data note | `data/raw/dental_models/dental_models_sellersprite_v1.xlsx` is byte-identical to the denture_base export; v2 flags identical uploads automatically |
