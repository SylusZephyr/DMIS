# Enterprise Architecture (Master Prompt 3)

This document covers how DMIS runs as a product for companies: tenants, plans, security, jobs, deployment and the workflow layer. The data pipeline and intelligence modules are described in `SYSTEM_ARCHITECTURE_FINAL.md` and `DATA_FLOW.md`.

```
 browser ──HTTPS──▶ Caddy ──▶ web (Next.js)          /api/* ──▶ api (FastAPI /api/v2)
                                                                     │  auth → Principal(org, role, scope)
                                                                     │  AuditMiddleware (every mutation)
                                                                     │  tenancy: plan limits (402), feature flags, usage
                                                     upload ─▶ queued_jobs ─▶ worker ×N ─▶ 18-stage pipeline
                                                                     │                         │
          scheduler (inbox, connectors, daily summaries) ────────────┘                         ▼
   PostgreSQL: users · organizations · tokens · markets · datasets · jobs · queue · events · alerts · messages ·
               projects · comments · suppliers · supplier_interactions · usage · audit
   Lake (Parquet via DuckDB): listings, products, segments, trends, competitors, supplier_matches, pain …
   Graph (Neo4j | embedded): Industry → Category → Family → Segment → Product → Listing; Brand, Supplier,
               Pain, **Trend**, **Project**      Vectors (Qdrant | embedded): products
```

## 1. Tenancy (organizations)

**Model:**
- An **Organization** has a `name`, a `plan` and a `status`.
- `org_id` is stored on `users`, `markets`, `datasets` and `suppliers`.
- `NULL` means the **default organization**, so single-company installs and existing databases need no change.

**Ownership:**
- A market belongs to the organization that first uploads it.
- Another organization that uses the same market name gets 409.
- Datasets and suppliers take the uploader's organization.

**Scoping:**
- `Principal.market_scope()` returns the markets of the user's organization. For a product manager, it returns only the markets of their categories within it.
- The scope is applied to:
  - every route that names a market
  - market, dataset and job lists
  - `/overview` top brands
  - `/geo`
  - `/graph/explore` and `/graph/search`
  - suppliers, supplier ranking and interactions
  - users
- Industry nodes are shared across organizations and hold no company data.
- Scoping switches on only once a non-default organization exists. Until then the platform behaves as before.

**Platform administration:**
- The admins of the default organization are the platform operators.
- They list and create organizations and change plans at `/orgs`.
- They create users in any organization with `POST /auth/users {org_id}` or `dmis.py create-user --org`.

## 2. Plans, feature flags and usage (commercial readiness)

**Plans** are defined in `config/platform/plans.yaml`:

| Plan | markets | users | records / month | features |
|---|---|---|---|---|
| starter | 3 | 5 | 50 000 | projects |
| professional | 25 | 25 | 1 000 000 | projects, connectors, analyst_ai, reports |
| enterprise | unlimited | unlimited | unlimited | projects, connectors, analyst_ai, reports, api |

- **Limits:**
  - A new market, user or upload over the plan returns **HTTP 402** with the reason.
  - A missing feature also returns 402: `analyst_ai` (AI phrasing), `connectors` (polling), `projects` (pipeline).
- **Usage metering** writes to the `usage_records` table:
  - `records_ingested`
  - `datasets_processed`
  - `analyst_questions`
  - `ai_calls`
- `GET /org` shows the plan, limits, counts and usage for this month.
- There is no billing provider. Plans are set by the platform admin. A billing integration would read `usage_records` and call `POST /orgs/{id}/plan`.

## 3. Security

| Control | Implementation |
|---|---|
| Authentication | Bearer tokens, stored as SHA-256 only and shown once. **Expiry**: `token_ttl_days` (90) in `plans.yaml`; an expired token gets 401 "token expired". Tokens can be listed (`GET /auth/tokens`) and revoked (`POST /auth/tokens/{id}/revoke`). |
| Authorization | roles × resources × levels (`permissions` table), plus category scoping for product managers and organization scoping (§1) |
| Audit | `AuditMiddleware` records every mutating request (user, role, action, resource, status). Domain actions are also recorded: approvals, stage changes, interactions, plan changes, token revocations. Admins read them at `/audit`. |
| Data isolation | organization scoping (§1). Tests check that one tenant cannot see or open another's markets, suppliers, graph nodes or overview, or write to another's market. |
| Secrets | environment only (`infra/prod.env`, gitignored); no secret in the repo |
| Transport | Caddy HTTPS with automatic certificates |

## 4. Jobs, queue and workers

- **`DIP_JOB_MODE=thread`** (laptop): an upload runs in a background thread inside the API.
- **`DIP_JOB_MODE=queue`** (server):
  - The upload is written to `queued_jobs`. Reviews go to a Parquet file beside it.
  - `dmis.py worker` processes claim jobs with an atomic `queued → running` update, run the same `process_dataset`, and update the same `jobs` record, so the UI does not change.
  - A job left `running` for 6 h is re-queued (at most 3 attempts).
  - `worker.stats()` gives queue depth.
- The queue is database-backed on purpose. PostgreSQL is already there, so Redis would add a service without a need (PRINCIPLES.md §12). The worker interface (`enqueue`, `claim`, `run_one`) is where Redis/RQ would slot in if throughput required it.
- The **scheduler** (`dmis.py schedule`) processes the inbox, polls connectors and sends daily summaries. It runs as one process.

## 5. Workflow layer (P3a + P3b)

- **Projects:** opportunity → evaluation → supplier search → prototype → launch decision → launched.
  - Approval gate.
  - The prediction is stored at decision time.
  - Outcome tracking at 3, 6 and 12 months, with calibration.
  - Linked suppliers.
  - Each project is mirrored in the graph as a **Project** node: `Segment -HAS_PROJECT-> Project -SOURCED_FROM-> Supplier`.
- **Scenario comparison:** `POST /launch/compare` evaluates 2–6 price or variant scenarios in the same market and segment.
  - It returns attractiveness, positioning, Monte-Carlo p50 units, revenue and profit, P(profit), unit margin and main risk.
  - It names the best scenario by fit and the best by profit.
- **Supplier intelligence:**
  - Cooperation history (`supplier_interactions`: inquiry, quote, sample, order, audit, issue).
  - **Price level** comes from quotes relative to the market's median quote.
  - **Ranking** (`/sourcing/ranking`) combines fit, cooperation, price, lead time and reliability, with weights in `sourcing.yaml`.
  - Components without evidence are left out and listed as missing, never guessed.
- **Graph Trend nodes:** `Category/Segment -HAS_TREND-> Trend`, `Product -IN_TREND-> Trend`, with confidence and expected growth.
- **Alerts, summaries and messages:** events come from change detection. Owners get alerts, internal messages and optionally e-mail, plus a daily summary.

## 6. Deployment and operations

See `DEPLOYMENT_GUIDE.md` for:
- the production compose: Caddy, api, 2 workers, scheduler, web, PostgreSQL, Neo4j and Qdrant
- environment variables
- backup and restore: a tar.gz with a sha256 manifest and `pg_dump`
- upgrades: additive migrations

See `ADMIN_GUIDE.md` for daily administration.

## 7. Verification

- `tests/platform/test_productization.py` covers:
  - tenant isolation
  - plans, feature flags, limits and usage
  - token expiry and revocation
  - supplier cooperation, price level and ranking
  - scenario comparison
  - graph Trend and Project nodes
  - queue and worker
  - backup and restore
  - the **acceptance flow**: a new company uploads two snapshots. The platform processes them, creates products, sizes the market, scores opportunities, alerts the owner, ranks the company's own suppliers, updates the graph and produces the market brief. That company only is able to see the result.
- `tests/platform/test_operations.py` covers P3a.

## 8. Known limits

- The Docker images were not built in the development sandbox because it has no Docker daemon. Build and smoke-test on the target host.
- Neo4j and Qdrant data are not in the DMIS backup archive. Use their native dumps, or rebuild them by re-processing, since they are derived data.
- One organization per user. Market names are unique across organizations.
- The embedded graph and vector stores are single-process. Use Neo4j and Qdrant with several workers.
