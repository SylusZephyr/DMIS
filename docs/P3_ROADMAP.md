# Master Prompt 3 — Productization: audit, roadmap and status

Milestone P3a ("use it daily") was merged in PR #4. P3b ("sell it") is implemented on branch `p3b-productization`; its status is below.
Audit of every prompt item against the code before P3: **EXISTS** (already built — reused), **EXTEND** (built on), **NEW**.

| Prompt 3 phase | Item | Before P3 | P3a / P3b result |
|---|---|---|---|
| 0 Real data | micromotor export, supplier list, reviews, monthly snapshots | tooling EXISTS (`process-dir`, `business_simulation.py`) | **needs your files** — nothing is invented |
| 1 User system | users, roles, permissions, tokens | EXISTS (`dip/auth.py`) | EXTEND: `supplier_manager` role; employee `department`, `responsibilities`, `email`; `/employees/{id}/profile` with categories, users, **activity history** |
| 2 Ownership workflow | assigned categories, focus actions | EXISTS (`briefs.py`) | NEW: **daily intelligence summary** (`/employees/{id}/summary`, Inbox page, `dmis.py daily-summary`) |
| 3 Real-time updates | snapshots, version tracking, historical comparison, connectors | EXISTS (`history.py`, `connectors.py`, datasets) | NEW: **scheduler** (`dmis.py schedule`, `POST /scheduler/run`) with a **drop-folder inbox** `data/inbox/<market>/` (each file once, oldest first) |
| 4 Change detection | competitors, prices, launches, opportunity, trend | EXISTS (`events.py`) | EXTEND: **market.size_change** (revenue / units ±10 %), **supplier.new** (matched to markets); thresholds moved to `config/platform/operations.yaml` |
| 5 Alerts | dashboard alerts to owners | EXISTS | NEW: **internal messages** + **e-mail** (SMTP via `DIP_SMTP_*`) for important alerts, approvals, summaries |
| 6 Collaboration | comments, notes, evaluations, decisions, approvals | — | NEW: comments / notes / 1–5 evaluations on any entity; recommend → approve / reject; **decision history**; audit |
| 7 Product pipeline | opportunity → evaluation → supplier search → prototype → launch decision → tracking | — | NEW: projects with stages; approval gate before *launched*; prediction stored at decision time; **outcome tracking** of launched ASINs at 3/6/12 months; `/calibration` |
| 8 Simulation centre | scenario comparison | single scenario EXISTS (`launch.py`) | **P3b:** `POST /launch/compare`: 2–6 scenarios side by side, best by fit and by profit (Launch Simulator) |
| 9 Supply chain | supplier history, cooperation, price level, ranking | scoring EXISTS | **P3b:** `supplier_interactions` (cooperation history), price level from quotes, `/sourcing/ranking` with evidence and missing components; supplier page |
| 10 Graph | product ↔ market / pain / supplier / competitor / trend | mostly EXISTS | **P3b:** Trend nodes (`HAS_TREND`, `IN_TREND`), Project nodes (`HAS_PROJECT`, `SOURCED_FROM`) |
| 11 Performance | queue, scheduler, workers | caching/incremental EXISTS; scheduler NEW in P3a | **P3b:** DB-backed queue (`DIP_JOB_MODE=queue`) and `dmis.py worker` (Redis not needed: PostgreSQL already present) |
| 12 Deployment | Docker, env config, migrations, backups | compose EXISTS | EXTEND: **additive schema migration** (`business.migrate`). **P3b:** Dockerfiles, `infra/docker-compose.prod.yml` (Caddy HTTPS, 2 workers, scheduler), `dmis.py backup/restore` |
| 13 Security | authn, authz, audit logs, data isolation | authn/authz EXISTS | NEW: **audit log** (every mutating request + domain actions, `/audit`, Audit page). **P3b:** organization isolation, token expiry, listing and revocation, scoped graph, geo and leaderboard |
| 14 Commercial | subscriptions, accounts, usage, feature flags | — | **P3b:** organizations, plans (`plans.yaml`), feature flags, limits (402), usage metering, Organization page; no payment provider |

## P3a acceptance (what was verified)
- `tests/platform/test_operations.py` (7 tests): migration, audit, market-size and supplier events reaching the
  owner's summary and messages, full project pipeline with approval gate and outcome, comments, role checks
  (product manager cannot approve, supplier manager can add suppliers), scheduler inbox processes a file once.
- Browser: Launch Simulator → *Create project* → recommend → approve → decision history → audit entry.
- Full platform + engine + AI-client suites pass.

## P3b acceptance (what was verified)
- `tests/platform/test_productization.py` (9 tests):
  - tenant isolation
  - plans, feature flags, limits and usage
  - token expiry and revocation
  - supplier cooperation, price level and ranking
  - scenario comparison
  - graph Trend and Project nodes
  - queue and worker
  - backup and restore round trip
  - **Prompt 3 acceptance**: a new company uploads two snapshots. The platform processes them and creates products, market size and opportunities. The owner is alerted, the company's own suppliers are ranked, the graph is updated and the market brief is produced. The market is invisible to other organizations.
- Browser checks:
  - Organization page: plan, limits, usage, tokens, organization admin
  - supplier page: interaction form and history, price level, segment matches
  - Suppliers ranking
  - Launch Simulator scenario comparison
- Not verified here: the Docker images build. The sandbox has no Docker daemon; `docker compose config` validates the compose file.

## Deferred
- Billing provider integration. Usage records and plan changes are the hooks.
- Several organizations per user; per-organization market namespaces (market names are global today).
- Redis/RQ, only if the database queue becomes a bottleneck.
