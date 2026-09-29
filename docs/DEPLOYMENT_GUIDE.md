# Deployment Guide

DMIS runs in three shapes. All three use the same code and the same `scripts/dmis.py` commands.

| Shape | Storage | Jobs | Sign-in | Use it for |
|---|---|---|---|---|
| **Laptop** (`run_platform.bat` / `.sh`) | SQLite + DuckDB + embedded graph and vectors in `data/platform/` | background threads in the API | off | one analyst |
| **Company server** (`infra/docker-compose.prod.yml`) | PostgreSQL, Neo4j, Qdrant, Redis cache, lake volume | API ×2 processes, queue + 2 workers | on | one company, many users |
| **SaaS** (same compose on a cloud VM) | as above | as above | on, plus organizations | several companies (tenants) |

## 1. Production compose

```bash
cp infra/prod.env.example infra/prod.env        # never commit prod.env
# edit: DMIS_DOMAIN, POSTGRES_PASSWORD, NEO4J_PASSWORD, optional ANTHROPIC_API_KEY and SMTP
docker compose -f infra/docker-compose.prod.yml --env-file infra/prod.env up -d --build
docker compose -f infra/docker-compose.prod.yml exec api \
  python scripts/dmis.py create-user admin@company.com "Admin" --role admin
```

Paste the printed token into the sign-in dialog at `https://<DMIS_DOMAIN>`.

**Services:**

| Service | What it does |
|---|---|
| `postgres` | business DB: users, organizations, projects, events, suppliers, job records, the job queue, usage |
| `neo4j` | knowledge graph |
| `qdrant` | product vectors |
| `redis` | response cache shared by the API processes (no persistence; `allkeys-lru`, 256 MB) |
| `api` | FastAPI `/api/v2` as `DIP_API_WORKERS` uvicorn processes (default 2). It queues uploads (`DIP_JOB_MODE=queue`) and does not process them itself; queued inputs are staged under `data/platform/queue/` (a shared volume) so any worker can read them |
| `worker` ×2 | `dmis.py worker`: claims queued jobs and runs the 18-stage pipeline |
| `scheduler` | `dmis.py schedule`: every 60 min it processes `data/inbox/<market>/`, polls connectors and sends daily summaries |
| `web` | Next.js command center |
| `caddy` | HTTPS on 80/443. It gets a certificate for `DMIS_DOMAIN` automatically (`localhost` uses a local CA), and routes `/api`, `/docs` and `/openapi.json` to the API and everything else to the web |

**Volumes:**
- `pgdata`, `neo4jdata`, `qdrantdata`: service data
- `lake`: Parquet
- `platform`: DuckDB analytics and caches
- `inbox`: the drop folder
- `caddydata`: certificates
- `./backups` on the host: archive output

**Scaling:**
- Workers: raise `deploy.replicas` on `worker`. Workers claim jobs with a compare-and-set update, so each job runs once. A job left `running` for more than 6 hours (a worker died) is re-queued, up to 3 attempts.
- API: set `DIP_API_WORKERS` in `prod.env` (uvicorn processes in the `api` container). Everything the processes share lives in PostgreSQL, Neo4j, Qdrant and Redis. `dmis.py serve --workers N` prints a warning for each store that is not shared (SQLite, embedded graph or vectors, no Redis, thread job mode).
- Known limit: the analytics DuckDB file (`data/platform/analytics.duckdb`, relevance corrections and run history) is opened briefly by the pipeline and by the relevance-feedback endpoint. DuckDB allows one writing process at a time, so two of those at the same instant can fail with a lock error; retry the request.

**Smoke test:** `scripts/smoke_docker.sh` builds the image and starts postgres, redis, neo4j, qdrant, the API (2 processes) and one worker. It then creates an admin token, uploads a synthetic dataset (`scripts/smoke_api.py`), waits for the queued job, checks the market endpoints and takes a backup. It tears everything down afterwards (`KEEP=1` keeps it). CI runs it in the `docker-smoke` job. The same client works against any running API: `python scripts/smoke_api.py --base http://host:8000 --token ...`.

## 2. Environment variables

| Variable | Meaning |
|---|---|
| `DIP_POSTGRES_URL` | business DB (SQLite in `data/platform` when unset) |
| `DIP_NEO4J_URI` / `_USER` / `_PASSWORD` | Neo4j graph (embedded when unset) |
| `DIP_QDRANT_URL` | Qdrant (embedded when unset) |
| `DIP_JOB_MODE` | `thread` (default: jobs run inside the API) or `queue` (jobs wait for `dmis.py worker`) |
| `DIP_REDIS_URL` | response cache shared by all API processes, e.g. `redis://redis:6379/0` (in-process when unset; falls back to in-process while Redis is unreachable) |
| `DIP_API_WORKERS` | uvicorn processes for `dmis.py serve` (default 1; the prod compose uses 2) |
| `DIP_LOG_FORMAT` | `text` (default) or `json`: one JSON object per line with `request_id`, `job_id` and `stage` when set |
| `DIP_LOG_LEVEL` | default `INFO` |
| `SENTRY_DSN` | optional error reporting; needs `pip install -e .[observability]` (the Docker image has it). `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE` (default 0) |
| `DIP_AUTH` | `on` / `off`. It is on by default with PostgreSQL |
| `DIP_DATA_DIR`, `DIP_LAKE_DIR`, `DIP_INBOX_DIR` | storage folders |
| `DIP_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_FROM` | e-mail for alerts, approvals and summaries |
| `ANTHROPIC_API_KEY` | optional AI phrasing and classification; every call is traced |
| `DIP_CORS_ORIGINS` | allowed browser origins when the web runs on another origin |

Plans, limits and token lifetime are in `config/platform/plans.yaml`. Supplier ranking weights are in `config/platform/sourcing.yaml`. Alert thresholds and the scheduler are in `config/platform/operations.yaml`.

## 3. Without Docker (company workstation)

```bash
pip install --require-hashes -r requirements/prod.txt && pip install --no-deps -e .   # or: pip install -e ".[platform,platform-services,ml]"
export DIP_POSTGRES_URL=postgresql+psycopg://dip:pw@localhost:5432/dip DIP_JOB_MODE=queue DIP_AUTH=on
export DIP_REDIS_URL=redis://localhost:6379/0 DIP_LOG_FORMAT=json          # optional
python scripts/dmis.py serve --host 0.0.0.0 --port 8000 --workers 2        # API
python scripts/dmis.py worker                               # one or more
python scripts/dmis.py schedule                             # one
cd frontend && npm ci && npm run build && DIP_API_URL=http://127.0.0.1:8000 npx next start -p 3000
```

Run each process as a service: systemd on Linux, Task Scheduler or NSSM on Windows. Put any HTTPS reverse proxy in front.

## 4. Backups and restore

```bash
python scripts/dmis.py backup --out backups          # → backups/dmis-backup-<UTC time>.tar.gz
python scripts/dmis.py restore backups/dmis-backup-....tar.gz            # into empty data folders
python scripts/dmis.py restore backups/dmis-backup-....tar.gz --force    # moves current folders aside first
```

**What an archive holds:**
- `platform/`: SQLite business DB, DuckDB, embedded graph and vectors
- `lake/`: Parquet
- `manifest.json`: a sha256 of every file
- with PostgreSQL, a `pg_dump` of the business DB as `postgres.sql`

**What restore does:**
- verifies every checksum
- refuses paths that escape the target
- refuses non-empty targets unless `--force`; with `--force`, it moves them to `*.before-restore-<time>`, so nothing is deleted

**Load the dump into PostgreSQL yourself:** `psql "$URL" < postgres.sql`, into an empty database.

**Qdrant server (`DIP_QDRANT_URL`):** `backup` takes a snapshot of every collection through the Qdrant snapshots API and stores it in the archive as `qdrant/<collection>.snapshot`, with its checksum in `manifest.json` (`"qdrant"`). If Qdrant is unreachable, the manifest records `NOT included: ...` and the rest of the backup still completes. To restore a collection (it is replaced):

```bash
tar -xzf dmis-backup-....tar.gz qdrant/products.snapshot
python -c "from dip.backup import upload_qdrant_snapshot as u; print(u('qdrant/products.snapshot', 'products'))"
# or: curl -X POST "$DIP_QDRANT_URL/collections/products/snapshots/upload?priority=snapshot" -F snapshot=@qdrant/products.snapshot
```

**Neo4j server (`DIP_NEO4J_URI`):** not included; the manifest says so. Community Edition can dump only a stopped database:

```bash
docker compose -f infra/docker-compose.prod.yml stop neo4j
docker compose -f infra/docker-compose.prod.yml run --rm neo4j neo4j-admin database dump neo4j --to-path=/data/dumps
docker compose -f infra/docker-compose.prod.yml start neo4j        # the dump is in the neo4jdata volume, /data/dumps
# restore: neo4j-admin database load neo4j --from-path=/data/dumps --overwrite-destination=true (database stopped)
```

The graph is derived data: instead of dumping it you can rebuild it (and the vectors) by re-processing the datasets in the lake.

**Schedule:** run `docker compose ... exec api python scripts/dmis.py backup --out /app/backups` from cron, daily. Copy `./backups` off the machine.

**Test a restore:** do this once per quarter into a scratch `DIP_DATA_DIR` / `DIP_LAKE_DIR`. `tests/platform/test_productization.py::test_backup_and_restore_roundtrip` exercises it in CI.

## 5. Upgrades

```bash
git pull && docker compose -f infra/docker-compose.prod.yml --env-file infra/prod.env up -d --build
```

- Take a backup before every upgrade.
- Today the start-up still creates missing tables and `business.migrate` adds new nullable columns (it never drops anything). Alembic (section 6) is the path forward for every other change.

## 6. Schema migrations (Alembic)

`alembic.ini` and `alembic/` manage the business database (`dip.storage.business.Base.metadata`). The URL comes from `-x url=...`, else `DIP_POSTGRES_URL`, else the embedded SQLite file.

```bash
alembic upgrade head                                   # a new, empty database
alembic stamp 0001_baseline                            # once, on a database created before Alembic (schema already there)
alembic current                                        # which revision a database is at
```

To change the schema:
1. Edit the models in `src/dip/storage/business.py`.
2. `alembic revision --autogenerate -m "what changed"`, then read and fix the generated file in `alembic/versions/` (autogenerate misses renames and some type changes; SQLite needs batch mode, which `env.py` enables).
3. `alembic upgrade head` on a copy of production data; `alembic downgrade -1` must work too.
4. Commit the revision with the model change. CI runs `alembic check` (non-blocking for now) and `tests/platform/test_ops.py::test_alembic_upgrade_head_on_fresh_sqlite`.

In Docker: `docker compose -f infra/docker-compose.prod.yml exec api alembic upgrade head`.

## 7. Dependencies and the lockfile

- `pyproject.toml` states the allowed ranges. `requirements/ci.txt` (CI, full dev set) and `requirements/prod.txt` (the Docker image) pin every package with hashes. They are universal lockfiles for Python 3.11 on any OS and architecture.
- Install exactly what CI tests: `pip install --require-hashes -r requirements/ci.txt && pip install --no-deps -e .`. `pip install -e ".[dev,platform,api,dashboard,ml]"` still works for day-to-day development.
- After changing dependencies in `pyproject.toml`: `pip install uv && scripts/lock_deps.sh`, then commit `requirements/`. To take newer versions within the ranges: `scripts/lock_deps.sh --upgrade`. CI fails when the lockfiles do not match `pyproject.toml` (`scripts/lock_deps.sh --check`).
- CI also runs `pip-audit` on both lockfiles (blocking; accept a finding only with `--ignore-vuln <ID>` and a comment in `.github/workflows/ci.yml`) and `npm audit --audit-level=high` (blocking for runtime dependencies, advisory for dev tooling).
