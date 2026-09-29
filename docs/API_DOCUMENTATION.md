# API Documentation — `/api/v2`

- **Base URL:** `http://localhost:8000/api/v2`. The frontend reaches it at `/api/v2` through a Next.js rewrite.
- **Interactive docs:** `http://localhost:8000/docs`; the OpenAPI schema is at `/openapi.json`. This file lists the platform endpoints (retired ones at the end).
- **v1 API:** retired (the read-only `api/` package and the Streamlit `dashboard/` were removed; every view is on `/api/v2`).
- **Request ids:** every response carries `X-Request-ID` (the caller's value when it is a safe token, else a new id); the same id is on every log line of that request (`DIP_LOG_FORMAT=json`).
- **Retired endpoints** answer **410 Gone** with the replacement; see [Retired endpoints](#retired-endpoints).

## Authentication

```http
Authorization: Bearer dmis_...
```

- **When it applies:** when `DIP_AUTH=on`, or by default with PostgreSQL. With auth off (the embedded default), every call is allowed.
- **Tokens:** created with `python scripts/dmis.py create-user`, `POST /auth/users` (admin) or `POST /auth/tokens` (self).
- **Permissions:** each endpoint requires a `resource:action` permission. The levels are read < write < admin, and `admin` holds `*`.

| Role | markets | datasets | suppliers | people | shopping |
|---|---|---|---|---|---|
| admin | all | all | all | all | all |
| manager | write | write | write | write | read |
| product_manager | read (own markets only) | write | read | read | read |
| analyst | read | write | read | read | read |
| viewer | read | — | read | read | read |
| customer | — | — | — | — | read |

**Product-manager scope:** product managers get `403 market '<m>' is not assigned to you` on any route whose path or query names another market. Market lists, trends, events, search and launch evaluation are filtered to their markets.

**Errors:**

| Code | Meaning |
|---|---|
| 400 | invalid input |
| 401 | missing or invalid token |
| 403 | role or market scope |
| 404 | not found |
| 409 | the market was processed before a feature existed; re-process it |
| 502 | a connector's source failed |

## Operations

| Method | Path | Permission | Parameters | Returns |
|---|---|---|---|---|
| GET | `/health` | public | — | version, storage backends, graph and vector stats |
| POST | `/datasets` | datasets:write | multipart: `file`*, `market`*, `snapshot_date`, `marketplace`, `reviews` (file), `force` | `{job_id, market}`; processing runs in the background |
| GET | `/datasets` | datasets:read | `market` | uploads with ingestion reports |
| GET | `/jobs` | datasets:read | `limit` | recent jobs |
| GET | `/jobs/{job_id}` | datasets:read | — | status and stages (`name, status, seconds, peak_mb, summary`). An unchanged input shows one `skipped` stage. Includes the ingestion `report`. |

## Markets and products

| Method | Path | Permission | Parameters | Returns |
|---|---|---|---|---|
| GET | `/markets` | markets:read | — | markets with size, coverage, concentration, top opportunity, horizon, `trend`, `confidence` |
| GET | `/markets/{market}` | markets:read | — | full market summary: ingestion, quality, relevance, discovery, dedup, category, forecast, trend, confidence, competitors, pain, suppliers, opportunity, owners, history periods |
| GET | `/markets/{market}/segments` | markets:read | — | segments with metrics, opportunity factors, `confidence_score`, `trend_label` |
| GET | `/markets/{market}/products` | markets:read | `sort` (opportunity, revenue, sales, price, listings, rating), `segment`, `q`, `limit`≤5000, `offset` | `{total, items}`; items add `confidence_score`, `confidence_level`, `variant_label`, `model_basis` |
| GET | `/products/{product_id}` | markets:read | — | product (incl. confidence components and reasons), listings, segment, competitors, suppliers, customer pain, opportunity, similar |
| GET | `/products/{product_id}/similar` | markets:read | `limit`, `same_market` | vector neighbours |
| GET | `/search/products` | markets:read | `q`*, `limit`, `market` | semantic search |
| GET | `/markets/{market}/records` | markets:read | `status` (excluded…), `limit`, `offset` | records with `excluded_reason` |
| GET | `/markets/{market}/forecast` | markets:read | — | forecasts per subject |
| GET | `/markets/{market}/pain` | markets:read | `scope` | complaints, advantages, missing features |

## Enterprise intelligence

| Method | Path | Permission | Parameters | Returns |
|---|---|---|---|---|
| GET | `/markets/{market}/hierarchy` | markets:read | `segment`, `listings` (bool), `listing_limit` | nested Category → Family → Segment → Model → Variant → Listing tree |
| GET | `/markets/{market}/confidence` | markets:read | `level` (high, medium, low), `limit` | market roll-up, history periods, segments, products with `confidence_components` and `confidence_reasons[{signal, ok, detail}]` |
| GET | `/trends` | markets:read | — | market-level trend of every visible market, growing first |
| GET | `/markets/{market}/trends` | markets:read | — | `{market, segments}`: trend, direction, confidence, expected_growth_12m, price_pressure, seasonality, signals, evidence |
| GET | `/markets/{market}/brief` | markets:read | — | size, products, opportunity level, suggested development, trend, confidence, competitors, suppliers |
| GET | `/employees/{employee_id}/focus` | people:read | — | categories, market briefs, `recommended_actions[{priority, market, action, reason, link}]` |
| GET | `/ai/traces` | datasets:read | `limit` | AI calls: model, prompt_version, input_ref, input_hash, output, status, created_at |

`LaunchRequest` fields:

- `title`*
- `price`* (must be > 0)
- `specs`
- `unit_cost` (defaults to the comparables' median cost field)
- `market` (auto-chosen by similarity if omitted)
- `segment_id`
- `marketplace_fee` (default 0.15)
- `fulfilment_fee` (defaults to the comparables' median FBA)
- `fixed_monthly_cost`

## Events, alerts, connectors

| Method | Path | Permission | Parameters | Returns |
|---|---|---|---|---|
| GET | `/events` | markets:read | `market`, `kind` (`*` wildcard), `severity`, `limit` | event log (scoped for product managers) |
| POST | `/events` | datasets:write | JSON `{kind*, market, subject, severity, payload}` | `{event_ids}`. `kind` is `news.item` (matched to markets by text when no market is given) or `external.signal`. |
| GET | `/alerts` | markets:read | `employee_id`, `status` (new, read, done), `limit` | alerts with their event; signed-in users see their own |
| POST | `/alerts/{alert_id}` | markets:read (own) | JSON `{status}` | `{saved}` |
| GET | `/connectors` | datasets:read | — | `[{name, configured, description, missing}]` |
| POST | `/connectors/{name}/poll` | datasets:write | — | poll result; `not_configured` lists the missing env vars |

Event kinds from change detection:

- `market.created`
- `competitor.new_brand`
- `competitor.share_change`
- `competitor.price_change`
- `competitor.position_change`
- `opportunity.change`
- `opportunity.new_segment`
- `product.new_launch`
- `listings.new`
- `trend.change`
- `dataset.processed`

Connectors emit `dataset.available`, `supplier.added` and `news.item`.

## Universe, graph, geography

| Method | Path | Permission | Parameters | Returns |
|---|---|---|---|---|
| GET | `/universe` | markets:read | — | Industry → branches → markets → segments tree (value, opportunity, growth) |
| GET | `/overview` | markets:read | — | KPIs, per-market totals, graph stats, top brands |
| GET | `/graph/explore` | markets:read | `node`, `depth`≤4, `kinds` (comma list), `limit` | `{nodes, edges}` |
| GET | `/graph/search` | markets:read | `q`*, `kind`, `limit` | nodes |
| GET | `/graph/stats` | markets:read | — | counts by kind |
| GET | `/geo` | markets:read | — | countries from supplier countries and dataset marketplaces |

## Shopping, simulation, feedback, v1 analyst

| Method | Path | Permission | Parameters | Returns |
|---|---|---|---|---|
| POST | `/relevance/feedback` | datasets:write | JSON `{record_id*, text*, is_dental*, market, note}` | saved; applied on the next run |

## People and suppliers

| Method | Path | Permission | Parameters | Returns |
|---|---|---|---|---|
| GET | `/suppliers` | suppliers:read | `q`, `country` | suppliers with score and geo |
| GET | `/suppliers/{supplier_id}` | suppliers:read | — | supplier + segment matches |
| POST | `/suppliers` | suppliers:write | JSON `SupplierIn` (`name`*, country, city, website, business_type, oem, odm, certifications, product_categories, contact, notes) | saved |
| POST | `/suppliers/import` | suppliers:write | multipart `file` (CSV/XLSX) | `{imported}` |
| GET | `/employees` | people:read | — | employees with category and market counts |
| GET | `/employees/{employee_id}/dashboard` | people:read | — | KPIs, categories, potential products |
| POST | `/ownership` | people:write | JSON `{employee*, category_label*, market}` | `{saved, employee_id}` |

## Operations & collaboration (Master Prompt 3)

| Method | Path | Permission | Parameters | Returns |
|---|---|---|---|---|
| GET | `/projects` | projects:read | `market`, `stage`, `status`, `owner_employee_id` | `{stages, projects}` (scoped for product managers) |
| POST | `/projects` | projects:write | JSON `{title*, market, segment_id, owner_employee_id, idea{price, specs, unit_cost}}` | project with `prediction` (launch evaluation) |
| GET | `/projects/{id}` | projects:read | — | project + `history` (decisions) + `comments` |
| POST | `/projects/{id}/stage` | projects:write | JSON `{stage*, note}` | 409 when entering `launched` without approval |
| POST | `/projects/{id}/recommend` | projects:write | JSON `{text}` | status `pending_approval`; approvers notified |
| POST | `/projects/{id}/approve` · `/reject` | projects:admin | JSON `{text}` | status `approved` / `rejected`; owner notified |
| POST | `/projects/{id}/track` | projects:write | JSON `{listing_ids[]}` | tracked ASINs |
| GET | `/projects/{id}/outcome` | projects:read | — | observations per snapshot, checkpoints at 3/6/12 months vs predicted units |
| GET | `/calibration` | projects:read | — | measured checkpoints, share within p10–p90, median actual/p50 |
| GET · POST | `/comments` | markets:read | `target_kind*`, `target_id*` · JSON `{target_kind, target_id, text, kind (comment/note/evaluation), rating 1–5, market}` | comments |
| GET | `/messages` | people:read | `employee_id`, `unread` | internal messages (own for signed-in users) |
| POST | `/messages/{id}/read` | people:read | — | `{saved}` |
| GET | `/employees/{id}/summary` | people:read | `hours` | daily intelligence summary: `headline`, `items`, `projects`, `text` |
| GET · POST | `/employees/{id}/profile` | people:read · people:write | JSON `{title, department, responsibilities, email}` | profile, categories, users, activity history, projects |
| GET | `/audit` | audit:read (admin) | `user`, `action`, `resource`, `limit` | audit log |
| POST | `/scheduler/run` | datasets:write | `send_summaries` | one scheduler tick: inbox, connectors, summaries |

New event kinds: `market.size_change`, `supplier.new`, `project.outcome`.

## Auth

| Method | Path | Permission | Returns |
|---|---|---|---|
| GET | `/auth/me` | any | auth state, user, role, employee_id, `markets` (the product manager's scope) |
| POST | `/auth/tokens` | signed in | a new token (shown once) |
| GET | `/auth/tokens` | signed in | your tokens (no secrets): name, created, last used, `expires_at`, revoked |
| POST | `/auth/tokens/{id}/revoke` | owner or users:admin | revokes the token |
| GET | `/auth/users` | users:admin | users of your organization |
| POST | `/auth/users` | users:admin | JSON `{email*, name*, role, employee_id, org_id}` → user id + token. `org_id` is for platform admins only; returns 402 over the plan's user limit |

`/auth/me` also returns `org_id`. Tokens expire after `token_ttl_days` (`config/platform/plans.yaml`); an expired token gets 401.

## Organizations, plans, sourcing, scenarios (Master Prompt 3, P3b)

| Method | Path | Permission | Parameters | Returns |
|---|---|---|---|---|
| GET | `/org` | signed in | — | your organization: plan, status, limits, features, `usage_this_month`, counts |
| GET · POST | `/orgs` | platform admin (admin of the default org) | JSON `{name*, plan}` | all organizations · creates one |
| POST | `/orgs/{id}/plan` | platform admin | JSON `{plan*, status}` | changes plan / status |
| GET · POST | `/suppliers/{id}/interactions` | suppliers:read · suppliers:write | JSON `{kind*, product, market_name, project_id, unit_price, currency, moq, lead_time_days, rating (1–5), note}` | cooperation history · records one (quotes update price levels) |
| GET | `/sourcing/ranking` | suppliers:read | `market`, `segment_id`, `limit` | suppliers ranked: `rank_score`, `components` (fit, cooperation, price, lead_time, reliability), `missing`, `evidence` |
| POST | `/projects/{id}/suppliers` | projects:write | JSON `{supplier_ids}` | links suppliers to a project (graph `SOURCED_FROM`) |

**Status codes:**
- **402** means a plan limit or a missing feature. The detail names which.
- **409** on upload means the market name belongs to another organization.

**Scoping:** every list and graph, geo or overview view is limited to your organization's markets once more than one organization exists. Product managers are limited further to their categories.

## Pilot: labelling, feedback, usage (Master Prompt 4)

| Method | Path | Permission | Parameters | Returns |
|---|---|---|---|---|
| GET · POST | `/labels/samples` | labels:read · labels:write | `market` · JSON `{market*, n (5–500), n_excluded, seed, name}` | samples · draws a stratified sample (segment × sales tier, plus excluded listings) |
| GET | `/labels/samples/{id}/items` | labels:read | — | items, each with the system's snapshot and its latest label per check |
| POST | `/labels/items/{id}` | labels:write | JSON `{check*, value, correct, notes}`. The check is one of `relevance`, `entity`, `segment`, `best_listing`, `attributes`. | stored label. Relevance labels also go into relevance feedback |
| GET | `/labels/samples/{id}/metrics` | labels:read | `against=snapshot\|current` | precision/recall with 95 % intervals, errors |
| GET · POST | `/labels/samples/{id}/sheet` | labels:read · labels:write | CSV upload | labelling sheet (CSV) · imports a filled sheet |
| POST | `/feedback` | markets:read | JSON `{target_kind*, comment*, target_id, market, field, shown_value, page}` | "This looks wrong" report |
| GET · POST | `/feedback`, `/feedback/{id}` | feedback:write (manager, admin) | `status` · JSON `{status, triage, resolution}` | reports · triage |
| POST | `/telemetry/view` | any | JSON `{path}` (query string dropped) | records a page view |
| GET | `/usage/pages` | feedback:write | `days` | views per page and per user |

## Caching

- **Cached endpoints:** `/universe`, `/overview`, `/geo`, `/markets/{m}/galaxy-v3`, `/markets/{m}/hierarchy` and `/trends`.
- **Cache key:** arguments, the caller's market scope and the data version (latest market update plus supplier, ownership and event counts). Any new data therefore invalidates them.
- **TTL:** 10 minutes.
- **Backend:** in-process by default; with `DIP_REDIS_URL` a Redis server shared by every API worker. If Redis is unreachable, requests fall back to the in-process cache (retried after 30 s).

## Examples

```bash
curl -F file=@export.xlsx -F market=micromotor -F snapshot_date=2026-09-01 localhost:8000/api/v2/datasets
curl localhost:8000/api/v2/jobs/<job_id>
curl localhost:8000/api/v2/trends
curl -H 'Content-Type: application/json' -d '{"title":"Brushless micromotor 60000 rpm","price":320}' localhost:8000/api/v2/launch/simulate
curl -H 'Content-Type: application/json' -d '{"question":"Which markets are growing?","lang":"en"}' localhost:8000/api/v2/analyst/ask-v3
curl -H 'Content-Type: application/json' -d '{"title":"Brushless micromotor","market":"micromotor","scenarios":[{"name":"A","price":250},{"name":"B","price":320}]}' localhost:8000/api/v2/launch/compare-v3
curl "localhost:8000/api/v2/sourcing/ranking?market=micromotor"
```

## Retired endpoints

These v1 endpoints were not used by the frontend and have been replaced by the metrics-v3 endpoints. They still exist
(marked deprecated in OpenAPI) but answer **410 Gone** with `{"detail": {"error": "endpoint retired", "endpoint": ..., "use_instead": ...}}`.

| Retired | Use instead |
|---|---|
| `POST /analyst/ask` | `POST /analyst/ask-v3` |
| `POST /ask` | `POST /analyst/ask-v3` |
| `POST /launch/evaluate` | `POST /launch/simulate` |
| `POST /launch/compare` | `POST /launch/compare-v3` |
| `POST /simulate` | `POST /launch/simulate` |
| `POST /shopping/recommend` | `POST /shopping/recommend-v3` |
| `GET /markets/{market}/galaxy` | `GET /markets/{market}/galaxy-v3` |
| `GET /markets/{market}/competitors` | `GET /markets/{market}/competitors-v3` |
| `GET /competitors/{brand}` | `GET /markets/{market}/competitors-v3` |

The list lives in `src/dip/api/deprecation.py`.
