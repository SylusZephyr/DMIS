# DMIS — Dental Market Intelligence OS

> **Intelligence Platform v2** (`src/dip/`, `frontend/`) wraps **Intelligence Core v1**
> (`src/dmie/`, unchanged). Architecture, migration plan and status: [ARCHITECTURE_V2.md](ARCHITECTURE_V2.md).

## Run it on your laptop (a new folder)

You need **Git**, **Python 3.11 or 3.12** and **Node.js 20+** installed.

```bash
git clone https://github.com/SylusZephyr/DMIS-.git DMIS
cd DMIS
bash run_platform.sh          # Windows: double-click run_platform.bat
```

The first run takes several minutes: it creates `.venv`, installs the Python and Node dependencies, builds the
platform data from the SellerSprite exports in `data/raw/` and builds both web apps. Then open
**http://localhost:3001** (new interface v2). The classic interface stays at http://localhost:3000 and the API
docs at http://localhost:8000/docs. Everything runs on your machine (SQLite, DuckDB, local vector store); no
account or key is needed.

* **Update later:** `git pull`, then run the same script again. It rebuilds the web apps when the code changed.
* **Optional keys** (AI stages, live Amazon data via Keepa, Chinese marketplaces for supplier sourcing): copy
  `.env.example` to `.env`, fill in what you have, and restart the script. `.env` is never committed.
* **Windows alternative:** put `setup_local.bat` in an empty folder and double-click it: it clones the repository
  into `DMIS\`, installs everything, runs the engine checks and starts the platform.
* If the repository is private, `git clone` asks you to sign in to GitHub the first time.

## Platform v2 quick start

**Windows:** double-click `run_platform.bat` · **macOS/Linux:** `bash run_platform.sh`

That installs Python + Node dependencies, builds the platform data from `data/raw/`
(first run), starts the API on :8000 and opens the 3D command center at
http://localhost:3000. Requires Python 3.11–3.12 and Node.js 20+.

Manual:

```bash
pip install -e ".[platform]"
python scripts/dmis.py bootstrap          # ownership file + every data/raw/<market>/ export
python scripts/dmis.py serve              # API  -> http://localhost:8000/docs
cd frontend && npm install && npm run dev # UI   -> http://localhost:3000
cd frontend-v2 && npm install && npm run dev # UI v2 -> http://localhost:3001 (docs/FRONTEND_V2.md)
```

Everything runs embedded (SQLite, DuckDB, embedded graph, Qdrant local mode). For
PostgreSQL + Neo4j + Qdrant servers: `docker compose -f infra/docker-compose.yml up -d`,
`pip install -e ".[platform,platform-services]"`, and set the variables in
`infra/.env.example`. After pulling new frontend code, delete `frontend/.next` so the
launcher rebuilds it.

**Enterprise intelligence (Master Prompt 2).** Data confidence, Model/Variant hierarchy, trends,
competitor profiles, launch simulator, market analyst, employee focus, change alerts, connectors and
roles (`product_manager`, `customer`). Docs: [architecture](docs/SYSTEM_ARCHITECTURE_FINAL.md),
[data flow](docs/DATA_FLOW.md), [user manual](docs/USER_MANUAL.md),
[business workflow](docs/BUSINESS_WORKFLOW.md), [API](docs/API_DOCUMENTATION.md). A full walkthrough of a
new dataset: `python scripts/business_simulation.py --file EXPORT --market NAME --context-raw`.
Batch snapshots: `python scripts/dmis.py process-dir FOLDER --market NAME`; live sources:
`python scripts/dmis.py connectors` / `poll`.

**Access control.** With PostgreSQL (or `DIP_AUTH=on`) every `/api/v2` call needs a token:
`python scripts/dmis.py create-user you@example.com "Your Name" --role admin` prints one;
paste it into the sign-in dialog. The embedded setup runs single-user (`DIP_AUTH=off`).

**Large datasets.** Ingestion uses Polars (`DIP_INGEST_ENGINE=pandas` for the v1 path).
Markets above 5,000 current listings use sampled discovery and blocked deduplication.
Benchmark: `python scripts/bench/make_large_dataset.py --rows 1000000 --out data/bench/1m.csv`
then `python scripts/dmis.py process data/bench/1m.csv --market bench` (≈80 s, 3.5 GB peak).

**Optional extras.** `pip install -e ".[ml]"` enables the XGBoost relevance feedback model
(used from 200 human labels). Set `NEXT_PUBLIC_MAPBOX_TOKEN` before `npm run build` for the
2D Mapbox layer next to the 3D globe.

---

# Intelligence Core v1 — Dental Market Intelligence Engine (DMIE)

Amazon US dental-category market intelligence pipeline: SellerSprite exports →
purified listings → product entities → market-capacity metrics → review
insights → visual market maps.

See [PRINCIPLES.md](PRINCIPLES.md) for mission and core principles.

## Setup

**Quickest:** double-click `setup_local.bat` (Windows) or run `bash setup_local.sh`
(macOS/Linux). It checks out the engine branch, creates `.venv`, installs
everything, builds the engine data, runs the smoke tests and opens the dashboard.

Requires Python 3.10+.

```bash
pip install -e ".[dev]"
```

This installs the `dmie` package plus its runtime dependencies (`duckdb`,
`pyyaml`) and dev dependencies (`pytest`).

### Enabling AI stages (optional)

Every AI-using stage (relevance classification, product-type
classification, entity-resolution arbitration, review extraction, the
AI Market Analyst) works without this — it degrades gracefully to
rules-only / "AI unavailable" with no configuration at all. To enable
real AI calls:

```bash
pip install -e ".[ai]"
cp .env.example .env   # then fill in a real ANTHROPIC_API_KEY
```

`.env` is loaded automatically (via `python-dotenv`, see
`src/dmie/ai/client.py`) and is gitignored — never committed.

## Initialize the project

```bash
python scripts/init_project.py
```

Creates the `data/` subdirectories (if missing) and applies the DuckDB
schema at `database/dmie.duckdb` (if not already applied). Safe to re-run.

## Running the complete pipeline

```bash
python scripts/run_pipeline.py --category denture_base
```

Runs the full pipeline for one category in the required order — ingestion
→ normalization → relevance classification → product-type classification
→ entity resolution → Product Master generation → market calculation →
review analysis → opportunity detection → dashboard preparation — instead
of running each `scripts/*.py` file by hand in sequence.

`scripts/run_pipeline.py` only orchestrates: it calls each existing
script's own `run()` function unchanged, in order, and does not
reimplement any calculation. If a stage fails, every stage after it is
skipped rather than run against inconsistent data, and a readable summary
plus a preserved log file (`data/exports/<category>/pipeline_logs/`) are
produced either way.

Optional flags:

```bash
python scripts/run_pipeline.py --category denture_base --skip-reviews --skip-dashboard
```

- `--skip-reviews` — skip review analysis (there is currently no review-text
  source for any category — see `docs/methodology.md`)
- `--skip-dashboard` — skip the dashboard-preparation stage (currently a
  no-op regardless: the dashboard reads DuckDB directly)

Every run is recorded in the `pipeline_runs` table (see
`docs/pipeline_runs.md`), so historical runs stay queryable rather than
silently overwritten.

## Universal Market Intelligence Engine (offline)

The Phase-2 engine (`src/dmie/engine/`, see
`docs/universal_engine_architecture.md`) runs on **any** marketplace
dataset — no fixed column names, no per-category taxonomy, no LLM:

```bash
python scripts/run_engine.py data/raw/dental_models/dental_models_sellersprite.xlsx --market dental_models
python scripts/run_engine.py my_export.csv --market micromotor --reviews reviews.csv --snapshot-date 2026-09-01
python scripts/run_engine.py --all-raw                    # every data/raw/<market>/ folder
python scripts/run_engine.py --import-suppliers suppliers.csv   # template: data/templates/suppliers_template.csv
```

It detects the schema, scores data quality, filters non-dental listings,
discovers product segments (DBSCAN/K-Means), merges duplicate listings into
products, computes market capacity/competition, forecasts (MA, Holt, ARIMA)
when a time series exists, scores opportunities, analyses review pain points,
simulates new products (Monte Carlo), and builds a knowledge graph. Results
land in DuckDB `mi_*` tables; the platform (`src/dip`, web app in `frontend/`) builds on the same
engine. The older Streamlit dashboard and the v1 read-only API were retired in favour of the platform.
Tunables live in `config/engine/`.

## Run tests

```bash
pytest
```

## Project layout

- `config/` — category and threshold configuration
- `data/` — `raw/` (immutable SellerSprite exports) through `exports/`
- `database/` — DuckDB database file
- `docs/` — methodology and data-dictionary documentation
- `prompts/` — versioned AI prompt templates
- `src/dmie/` — pipeline source code (ingestion, cleaning, classification,
  matching, market, reviews, opportunity, database, utils)
- `src/dip/` — the platform (FastAPI API, pipeline runner, knowledge layer, metrics)
- `frontend/` — the web app (Next.js)
- `scripts/` — CLI entry points for each pipeline stage
- `tests/` — unit and integration tests
