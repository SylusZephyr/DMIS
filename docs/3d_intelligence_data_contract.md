# 3D Intelligence Data Contract — UI-M1

**Status**: data-contract-only. No API, no frontend code. Written entirely
from live inspection of the real DuckDB database
(`database/dmie.duckdb`, `denture_base` category, the only real data
this project has) — every field/count below was queried directly, not
assumed from memory of earlier milestones' documentation.

**Method**: full schema dump (all 19 tables, every column, every row
count) via `PRAGMA table_info` + `SELECT COUNT(*)` against the live DB,
immediately before writing this document.

---

## 1. What node types the real data actually supports

| Node type | Status | Real source | Notes |
|---|---|---|---|
| `ROOT` | 🟢 Supported (synthetic) | n/a | Not a DB row — a single synthetic UI root node ("Dental Industry" or similar) that the graph API would construct, not fabricate as if it were data. Every real graph in this project has exactly one category hanging off it today. |
| `CATEGORY` | 🟡 Partially supported | `categories` (1 row) | `category_id='denture_base'`, `leaf_category='denture base'`, `parent_category='义齿'` (Chinese, free text — **not a foreign key to another category row**), `marketplace='US'`. There is no table of parent categories; `parent_category` is a label, not a real hierarchy edge. |
| `LEAF_CATEGORY` | 🔴 Not a distinct entity | same `categories` row | The schema does not distinguish "category" from "leaf category" — there is exactly one category level in the database today. Treating `CATEGORY` and `LEAF_CATEGORY` as two different node types would be inventing a hierarchy depth that doesn't exist yet. Until a second, non-leaf category row exists, the graph adapter should render `categories` rows as a single node type. |
| `PRODUCT_FAMILY` | 🔴 Schema exists, zero real data | `products.product_family` | Column added in Tier 2 (Milestone 4), deliberately left `NULL` for every product — **0 of 88** products have a value. See `docs/product_knowledge_graph.md` for why. The graph API can expose this field; it will return `null` for every product in `denture_base` today. |
| `PRODUCT_TYPE` | 🟢 Supported | `products.product_type` | Real, populated: `DB_WAX_PLATE`, `DB_RESIN`, `DB_RELINE`, plus `NULL` for unclassified. Sourced from the frozen taxonomy `config/taxonomy/denture_base_v1.yaml`. |
| `PRODUCT_MODEL` | 🔴 Schema exists, zero real data | `products.model` | Column has existed since Milestone 12. **0 of 88** products have a value — "no model-number extraction exists yet" (unchanged since it was first added). |
| `PRODUCT` | 🟢 Supported | `products` (88 rows) | `product_id`, `product_name`, `product_type`, `brand`, `model` (null), `representative_image`, `confidence` (identity confidence), `product_type_confidence`, `product_type_conflict`, `product_family` (null), `opportunity_score` (4/88 non-null), `created_at`. |
| `LISTING` | 🟢 Supported | `listings` (105 rows) + `listing_classification` | `listing_id`, `asin`, `title`, `brand`, `url`, `image_url`, `price`, `monthly_sales`, `monthly_revenue`, `rating`, `review_count` (**0 of 105 have a value** — the field exists but SellerSprite never populates it), plus `relevance_class`/`confidence`/`review_status` from the join. No listing-date field exists anywhere in the schema (confirmed absent from the raw SellerSprite export too, see `docs/data_dictionary.md`). |
| `BRAND` | 🟡 Derivable, not stored | `products.brand` / `listings.brand` | Brand is a free-text string column, not a normalized entity with its own id — there is no `brands` table. 56 distinct brand strings across 88 products. A `BRAND` node would have to be synthesized by the graph adapter (group-by), not read from a real brand-entity table. Fine to do (it's real data, just not pre-normalized), but the adapter must generate a stable synthetic id (e.g. a hash of the lowercased brand string), not treat brand strings as already-deduplicated identity (two products' brand strings could differ only in case/whitespace). |
| `EMPLOYEE` | 🟢 Supported (real data since 2026-09-23) | `employees` (14 rows) + `employee_category_ownership` (153 rows) | Real 产品经理 (Product Manager) names and raw-category ownership, extracted from a real ~200MB merged multi-category SellerSprite export's own Product Manager column (see `dmie.database.personnel`, `scripts/extract_from_merged_export.py`, PROGRESS.md's "Multi-category robustness pass"). `matched_category_id` is `NULL` for the ~151 raw categories this project hasn't onboarded a pipeline for yet — real ownership breadth honestly recorded, not fabricated for categories with no other intelligence behind them. `GET /api/employees*` now returns real `200`s, not the `501` stub this row previously described. |
| `OPPORTUNITY` | 🟢 Supported | `opportunity_signals` (53 rows) + `products.opportunity_score` | Per-signal: `signal_type`, `status`, `signal_strength`, `confidence`, `evidence` (JSON), `supporting_metrics` (JSON), `supporting_review_themes` (JSON). Per-product aggregate: `opportunity_score` (4/88 non-null, see `docs/opportunity_signals.md`). |
| `SUPPLIER` | 🔴 Not supported at all | — | No table, no column, anywhere. Confirmed the same way as `EMPLOYEE`. Genuinely blocked on a real data source decision (Tier 3, Milestone 16/17). |

## 2. What edge types the real data actually supports

| Edge type | Status | Real source |
|---|---|---|
| `CONTAINS` (category → product) | 🟢 Supported | `products.category_id` |
| `BELONGS_TO` (product → category) | 🟢 Supported | same field, inverse direction |
| `HAS_PRODUCT` | 🟢 Supported | same as `CONTAINS` — the adapter should pick one canonical direction/name per relationship, not expose both `CONTAINS` and `HAS_PRODUCT` for the identical real edge |
| `HAS_LISTING` (product → listing) | 🟢 Supported, with real edge metadata | `product_listings` (105 rows): `match_method`, `match_confidence`, `is_best_listing`, `review_status` — this is genuinely richer than a bare containment edge and should carry that metadata through |
| `SOLD_BY` (listing → seller) | 🔴 Not supported | — | No seller/merchant entity exists (see `SUPPLIER`/`EMPLOYEE` — same root cause: SellerSprite's export has no seller-identity column, confirmed in `docs/data_dictionary.md`) |
| `OWNED_BY` (product/category → employee) | 🔴 Not supported | — | Depends entirely on `EMPLOYEE` existing first |
| `HAS_OPPORTUNITY` (product → opportunity signal) | 🟢 Supported | `opportunity_signals.product_id` (non-null for `PRODUCT_IMPROVEMENT`/`BUNDLE`/`CUSTOMER_PAIN_POINT`; null for the 3 category-scoped signal types, which attach to the category node instead) |
| `SUPPORTED_BY_SUPPLIER` | 🔴 Not supported | — | Depends entirely on `SUPPLIER` existing first |
| `COMPETES_WITH` (product → product) | 🔴 Not supported, and not casually derivable | — | No real computed "competes with" relationship exists anywhere in this codebase. Same `product_type` is not the same claim as "competes with" (that would be inventing a relationship, not reading one) — `category_market_metrics.listing_concentration_hhi` is the closest real, already-computed competitive signal, and it's category-scoped, not a product-to-product edge. Building this for real would need a defined methodology (shared product_type? overlapping price band? both?) decided before any edges get drawn, not assumed here. |

## 3. Concrete example: the real graph for `denture_base` today

```
ROOT (synthetic)
 └─ CATEGORY: denture_base ("denture base", parent label "义齿")
     ├─ PRODUCT_TYPE: DB_WAX_PLATE (real, from products.product_type)
     │   └─ PRODUCT: e.g. Pf598530a9a ("General USE 20PCS 270G ...")
     │       ├─ opportunity_score: null (67 of 88 products) or a real 0-100 value (4 of 88)
     │       ├─ HAS_LISTING → LISTING: B0CXMQ7DFZ (asin, price $42.95, ...)
     │       └─ HAS_OPPORTUNITY → OPPORTUNITY: PRODUCT_IMPROVEMENT (insufficient_data, most products)
     │                                          or BUNDLE (signal_present, HIGH, 1 product)
     ├─ PRODUCT_TYPE: DB_RESIN
     ├─ PRODUCT_TYPE: DB_RELINE
     ├─ PRODUCT_TYPE: null ("unclassified", 1 product)
     └─ OPPORTUNITY (category-scoped): UNDERREPRESENTED_PRODUCT_TYPE (signal_present)
```

No `PRODUCT_FAMILY`, `PRODUCT_MODEL`, `EMPLOYEE`, `SUPPLIER`, `SOLD_BY`,
`OWNED_BY`, `SUPPORTED_BY_SUPPLIER`, or `COMPETES_WITH` nodes/edges exist
in this graph today. A UI-M4 MVP built honestly against real data would
have exactly 4 levels (Category → Type → Product → Listing/Opportunity),
not the 8-9 level example hierarchy in the original brief — that example
(`Laboratory → Laboratory Equipment → Micromotor → Brushed Micromotor →
N3 + H37L1 → Product → Listings → Seller`) describes the *micromotor*
category, which has zero real rows anywhere in this database (see the
Tier 3 conversation — still blocked on a SellerSprite export).

## 4. Node/edge JSON shape (as specified in the brief, annotated)

```jsonc
{
  "id": "string, stable across requests",
  "node_type": "CATEGORY | PRODUCT_TYPE | PRODUCT | LISTING | OPPORTUNITY | BRAND | ROOT",
  // EMPLOYEE, SUPPLIER, PRODUCT_FAMILY, PRODUCT_MODEL, LEAF_CATEGORY
  // are valid values but will not appear in any real denture_base
  // response today -- the enum stays open per the brief ("supported
  // node types should be extensible"), the DATA does not fabricate them.
  "label": "human-readable name",
  "parent_id": "string | null",
  "category_id": "string | null",
  "product_id": "string | null",
  "image_url": "string | null",         // real for PRODUCT (representative_image) and LISTING (image_url); null elsewhere
  "primary_metric": "number | null",     // e.g. representative_price for PRODUCT, confidence for OPPORTUNITY
  "secondary_metric": "number | null",   // e.g. best_listing_observed_monthly_sales for PRODUCT
  "confidence": "number | null",         // real per-node-type meaning, never a single reused number
  "opportunity_score": "number | null",  // real only on PRODUCT nodes; 4/88 populated today, null elsewhere -- never 0
  "metadata": { "...": "node-type-specific real fields, e.g. taxonomy_version, review_status, signal_type" }
}
```

```jsonc
{
  "id": "string",
  "source": "node id",
  "target": "node id",
  "relationship_type": "CONTAINS | HAS_LISTING | HAS_OPPORTUNITY",
  // SOLD_BY, OWNED_BY, SUPPORTED_BY_SUPPLIER, COMPETES_WITH are valid
  // enum values that will never appear in a real response until their
  // underlying entities/methodology exist.
  "confidence": "number | null",  // real for HAS_LISTING (match_confidence); null for structural containment edges
  "metadata": { "...": "e.g. match_method, is_best_listing for HAS_LISTING" }
}
```

## 5. Proposed API endpoints (design only — not implemented in UI-M1)

Every endpoint below is a **read** over existing tables — no endpoint
recalculates a market metric, a classification, or an opportunity
score. All of that stays exactly where it is (`src/dmie/market/`,
`src/dmie/opportunity/`, `src/dmie/classification/`, run via
`scripts/run_pipeline.py`), unchanged.

| Endpoint | Backs | Real today? |
|---|---|---|
| `GET /api/categories` | `categories` | 🟢 (1 row) |
| `GET /api/categories/{category_id}` | `categories` + `category_market_metrics` | 🟢 |
| `GET /api/categories/{category_id}/graph` | lazy first level: category → product_type nodes only, not the whole subtree | 🟢 |
| `GET /api/categories/{category_id}/products` | `product_market_metrics` join `products` (same query `load_product_metrics()` already uses in `dashboard/components/data.py`) | 🟢 |
| `GET /api/products/{product_id}` | `products` + `product_market_metrics` | 🟢 |
| `GET /api/products/{product_id}/listings` | `product_listings` join `listings` join `listing_classification` | 🟢 |
| `GET /api/products/{product_id}/market` | `product_market_metrics` | 🟢 |
| `GET /api/products/{product_id}/reviews` | `review_insights` | 🟡 real endpoint, always returns empty (0 rows exist) |
| `GET /api/products/{product_id}/opportunities` | `opportunity_signals WHERE product_id = ?` | 🟢 |
| `GET /api/employees` | `employees` join `employee_category_ownership` | 🟢 real data since 2026-09-23 |
| `GET /api/employees/{employee_id}` | same | 🟢 404 (not fabricated) for an unknown id |
| `GET /api/employees/{employee_id}/categories` | `employee_category_ownership` | 🟢 |
| `GET /api/employees/{employee_id}/intelligence` | composite of the two above | 🟢 |
| `GET /api/employees/by-category/{category_id}` | `employee_category_ownership WHERE matched_category_id = ?` | 🟢 empty `200` is valid (category exists but has no recorded owner) |
| `GET /api/graph/root` | synthetic root + real category nodes | 🟢 |
| `GET /api/graph/node/{node_id}` | dispatches by node_type prefix to the real underlying table | 🟢 for `CATEGORY`/`PRODUCT_TYPE`/`PRODUCT`/`LISTING`/`OPPORTUNITY`/`BRAND`/`EMPLOYEE` ids, 🔴 (404, not fabricated) for a `SUPPLIER` id -- `EMPLOYEE` not yet wired into the graph adapter itself (api/graph.py), only the flat `/api/employees*` routes |

Every endpoint reuses an existing query pattern from
`dashboard/components/data.py` or a repository function in
`src/dmie/database/` rather than writing new SQL from scratch — this is
the same "reuse, don't duplicate" discipline the Data Ingestion Center
(Milestone 16) and AI Market Analyst (Tier 2) were built with.

## 6. Proposed frontend repository structure (design only)

```
frontend/                    # new, sibling to dashboard/ -- never inside it
├── package.json
├── tsconfig.json
├── next.config.js
├── app/                     # Next.js app router
│   ├── layout.tsx
│   ├── page.tsx             # UNIVERSE mode (default)
│   ├── matrix/page.tsx      # MARKET MATRIX mode
│   ├── supermarket/page.tsx
│   ├── people/page.tsx      # renders "not collected yet" until EMPLOYEE data exists
│   ├── opportunities/page.tsx
│   ├── suppliers/page.tsx   # renders "not collected yet" until SUPPLIER data exists
│   └── data-quality/page.tsx
├── components/
│   ├── universe/            # React Three Fiber scene, camera, node meshes
│   ├── inspector/           # right-panel intelligence inspector
│   ├── matrix/               # ECharts price x sales scatter
│   └── shell/                # nav, search, activity bar
├── lib/
│   ├── api-client.ts         # typed fetch wrappers over the FastAPI routes above
│   └── graph-store.ts         # Zustand: selected node, expanded branches, filters
└── styles/
```

`api/` (the FastAPI layer) would similarly be a new top-level sibling
directory — `api/main.py`, `api/routers/`, read-only DB access reusing
`src/dmie/database/connection.py`'s `get_connection()` — not a
reimplementation of it.

## 7. How `dashboard/` (Streamlit) stays untouched

Nothing above modifies a single file under `dashboard/`, `src/dmie/`,
`scripts/`, or `database/dmie.duckdb`'s schema. `frontend/` and `api/`
would be new, additive, sibling directories reading the same DuckDB
file the Streamlit app already reads (`src/dmie/database/connection.py`).
Both UIs can run simultaneously against the same real data — verified
as architecturally sound (not yet built) since the Streamlit dashboard
already tolerates concurrent external readers (this is exactly the
same DuckDB single-writer/multi-reader model the M16 Data Ingestion
Center's write path had to work around).

## 8. What UI-M1 explicitly does NOT do

No FastAPI code, no Next.js scaffold, no 3D rendering. Per the brief's
own instruction ("Do not implement UI-M2 until UI-M1 is verified") and
this project's established discipline (Tier 1/Tier 2's "audit before
code" pattern), this document is the complete UI-M1 deliverable.
