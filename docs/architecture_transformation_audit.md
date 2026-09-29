# Architecture Transformation Audit (2026-09-24)

Per the user's explicit request: "Do not modify existing achievements. Perform
an architecture transformation audit." This document maps the target
architecture (Data Sources → Ingestion → Purification → Product Intelligence
→ Market Analysis → Opportunity → Simulation → Visual Intelligence) against
what genuinely exists in this codebase today, module by module, real name by
real name. Nothing below is aspirational — every "EXISTS" claim names the
actual file; every "MISSING" claim is stated plainly, not hedged.

No existing code was changed to write this document.

## Stage 1 — Data Purification

| Target capability | Status | Real implementation |
|---|---|---|
| Universal ingestion from multiple source formats | **PARTIAL** | `scripts/ingest.py` + `dmie.ingestion.excel_loader` handles SellerSprite XLSX only. `scripts/extract_from_merged_export.py` is a one-off script for the specific ~200MB merged export, not a general adapter. No CSV/API/review/supplier adapter exists. |
| Canonical internal schema | **EXISTS, but not source-agnostic yet** | `listings` table + `dmie.cleaning.normalize.normalize_dataframe()` — but the normalizer's input contract is SellerSprite's own column names, not a source-agnostic canonical schema with a translation layer in front of it. A second source format (Amazon API, a different scraper) would need a new normalizer, not just a new adapter. |
| Missing/invalid-data detection | **EXISTS** | `dmie.cleaning.validation.find_violations()` |
| Duplicate detection | **EXISTS** | ASIN-level dedup in `normalize_dataframe`; cross-category listing_id collision detection added tonight (`upsert_listings`'s conflict check) |
| Dental relevance engine (binary + confidence) | **EXISTS** | `dmie.classification.classifier.classify_listing()` — rules → (as of tonight) nothing → AI. Two-stage, not the target's rules → ML/similarity → AI three-stage cascade for *relevance* specifically (see Stage-cascade gap below). |
| Category re-routing (irrelevant to category X ≠ deleted, may belong to category Y) | **MISSING** | Today a listing is either RELEVANT/IRRELEVANT/UNCERTAIN *for the category it was ingested into*. There is no mechanism that takes an IRRELEVANT `denture_base` listing and asks "does this actually belong to `dental_models` instead?" This is a real, named gap — the user's own "Base Plate Wax listed under Denture Base but is actually Waxes" example. |
| Dynamic taxonomy discovery (Family → Type → Model) | **PARTIAL** | `dmie.classification.product_type_discovery.py` does flat, single-level discovery (product_type only), not a multi-level family/type/model hierarchy. `products.product_family`/`products.model` columns exist in schema but are always NULL — no extraction logic was ever built (documented, not hidden — see schema.sql's own comments). Found live tonight: the discovery algorithm produces a useless single supercluster on heterogeneous categories (dental_models) and needs direct human/manual review to actually segment — it does not reliably "automatically discover" a hierarchy the way the target architecture assumes. |

## Stage 2 — Product Intelligence (Entity Resolution)

| Target capability | Status | Real implementation |
|---|---|---|
| Listing ≠ Product, N listings → 1 product | **EXISTS, real, tested** | `dmie.matching.resolution` — blocking (`dmie.matching.candidates`) + composite scoring + AI arbitration for ambiguous pairs. Verified tonight at real scale: 387 dental_models listings → 307 real products. |
| Best-selling listing per product | **EXISTS** | `dmie.market.sales.best_selling_listing()`, surfaced via `product_listings.is_best_listing` |
| Product Family / Type / Model / Variant hierarchy | **PARTIAL** — see Stage 1 row above | Only Type exists as real, populated data. Family and Model are schema-only. |

## Stage 3 — Market Analysis Engine

| Target capability | Status | Real implementation |
|---|---|---|
| Market size (listings/products/sales/revenue, monthly+annual) | **EXISTS** | `dmie.market.aggregation` → `product_market_metrics` / `category_market_metrics` |
| Competitive intelligence (brand share, concentration) | **EXISTS** | Same module; HHI via `listing_concentration_hhi` |
| Customer intelligence (review pain points) | **EXISTS as infrastructure, effectively empty on real data** | `dmie.reviews.extraction`/`themes` — real code, but this project's actual data source (SellerSprite) has never contained review text, so `review_insights` has 0 rows for every category. This is not a code gap, it's a data-source gap. |
| Bundle analysis | **EXISTS** | `detect_bundle_signal` in `dmie.opportunity.signals` |
| **Trend Engine (historical snapshots → growth rates)** | **BUILT TONIGHT** | `dmie.market.trends` — new module, reads the *existing* `market_snapshots` table (which already existed, M16) and computes real growth rates across N snapshots. Verified against real denture_base history (9 real snapshots): 8,634% revenue growth, exactly matching PROGRESS.md's independently-documented number. Tested (4 unit tests). |

## Stage 4 — Opportunity Engine

| Target capability | Status | Real implementation |
|---|---|---|
| Evidence-based signals, not AI opinion | **EXISTS** | `dmie.opportunity.signals` — 6 named signal types, each with real conditions/thresholds |
| Single opportunity score | **EXISTS** | `dmie.opportunity.scoring.compute_opportunity_score()` |
| **Opportunity Vector (multi-dimension, configurable weights)** | **BUILT TONIGHT** | `dmie.opportunity.vector` — 11 dimensions computed from real data (demand/revenue percentile rank, real HHI, real review rating, real trend growth, real market-change momentum). 3 dimensions (offline-market strength, supplier availability, entry difficulty) are explicitly `status="not_available"`, never fabricated — no real data source exists for them anywhere in this project. `weighted_score()` supports configurable per-dimension weights. Tested (7 unit tests) and verified against a real dental_models product. |
| Star-rating-style evidence display (★★★★★) | **MISSING (presentation only)** | The real numbers exist (Opportunity Vector above); no dashboard page renders them as star ratings yet — this is a pure UI task once the vector is wired into a page. |

## Stage 5 — Product Simulation Engine

| Target capability | Status | Real implementation |
|---|---|---|
| Hypothetical product vs. real market comparison | **BUILT TONIGHT** | `dmie.opportunity.simulation.simulate_product()` — bootstrap resampling from real comparable products' observed sales (filtered to a real price band around the target price). |
| Monte Carlo scenario probabilities | **BUILT TONIGHT** | Same module: 10,000-scenario bootstrap (numpy, fixed seed for reproducibility), real threshold probabilities (e.g. `P(sales >= 100/mo)`), real percentile intervals, not fabricated. |
| Model confidence tied to real sample size | **BUILT TONIGHT** | `model_confidence` (LOW/MEDIUM/HIGH) is directly a function of how many real comparable products exist — never a flat/fabricated confidence number. |
| Expected margin / breakeven | **BUILT TONIGHT, optional inputs only** | Computed only when the caller supplies real `manufacturing_cost`/`fixed_launch_cost` — never guessed. |
| Forecast self-evaluation (store predictions, score against later reality) | **MISSING** | No `predictions` table, no scoring-against-actuals logic exists yet. This *cannot* be meaningfully tested yet regardless — it requires waiting for real future snapshots to compare against, which don't exist yet (denture_base's real history only spans ~22 hours). Building the storage schema now would be speculative infrastructure for data that doesn't exist — deferred, not silently dropped. |

## Stage 6 — Visual Intelligence

| Target capability | Status | Real implementation |
|---|---|---|
| Streamlit dashboard (category overview, market map, product detail, data quality, personnel) | **EXISTS** | `dashboard/pages/*.py`, 9 pages, all reading real data |
| Read-only REST API | **EXISTS** | `api/` (FastAPI) — categories, products, listings, opportunities, employees (wired to real data tonight), graph |
| 1. Global Dental Market Universe (knowledge graph, fully clickable) | **PARTIAL** | `api/graph.py` implements CATEGORY → PRODUCT_TYPE → PRODUCT → LISTING/OPPORTUNITY node traversal — a real graph API exists. EMPLOYEE nodes are not yet wired into the graph adapter (flat `/api/employees` routes exist and are real; graph traversal to them doesn't). No 3D rendering frontend exists — this is API-only today, no visualization layer consumes it as a 3D graph. |
| 2. Market Matrix (price × sales bubble chart) | **EXISTS in 2D** | `dashboard/pages/2_Market_Map.py` — price/sales scatter with real data. Not the specific bubble-size=revenue/color=opportunity encoding described, but the same underlying real data already supports it (a chart-config change, not new data plumbing). |
| 3. Product Manager Dashboard | **BUILT (earlier tonight, before the audit request)** | `dashboard/pages/9_Personnel_Intelligence.py` + `api/routers/employees.py` — real data (14 employees, 153 ownership rows), verified live. Doesn't yet drill from an employee into "their categories' market size / opportunity products" in one click — the underlying data joins exist (`employee_category_ownership.matched_category_id` → `category_market_metrics`), the UI just doesn't chain them yet. |
| 4. Customer Shopping Mode (recommendation engine) | **MISSING** | No code exists. Given real product/price/sales/rating data already exists per category, this is a real, buildable filter-and-rank feature — not blocked on new data, just not built. |
| 5. Supply Chain Intelligence (supplier discovery/matching) | **MISSING, and cannot be built honestly right now** | No supplier data source exists anywhere — not in SellerSprite, not supplied by the user, not discoverable without a real external data source (a supplier directory, Alibaba/Global Sources data, or similar). Schema-only stubs would just be more surface area for the same "never fabricate" discipline to have to defend. This is blocked on a real data source decision, not on engineering effort. |

## Cross-cutting: the "rules → ML/similarity → LLM → human" cascade

The target architecture's single most-repeated idea is a 4-stage cascade for
every classification decision. Real status per decision type:

| Decision | Rules stage | ML/similarity stage | LLM stage | Human review |
|---|---|---|---|---|
| Relevance classification | EXISTS (`rules.py`, denture_base only) | **MISSING** | EXISTS | EXISTS (`needs_review`/`sample_for_qa` tiers) |
| Product-type classification | EXISTS (`product_type_classifier.apply_rules`) | **BUILT TONIGHT, additive** — `dmie.classification.similarity_tier` + `scripts/apply_similarity_tier.py`, run as a post-hoc pass (not wired into the core function, to avoid touching already-tested code). Verified on real data: resolved 28/107 previously-UNCERTAIN dental_models listings using only TF-IDF/cosine similarity to rule-matched examples, all spot-checked correct. Raised rules+ML coverage from 64% to 74% without any AI call. | EXISTS | EXISTS |
| Entity resolution | EXISTS (blocking + composite score) | **MISSING as a distinct stage** — composite scoring already *is* a lightweight statistical method, arguably satisfies this stage already, just not framed as "ML/embeddings" | EXISTS (`ai_arbitrate`) | EXISTS (`needs_review`) |

The similarity tier is real and proven at real scale for product-type
classification. It has **not** been extended to relevance classification or
entity resolution yet — those would follow the identical pattern (build a
reference index from already-labeled real examples, resolve near-matches,
defer the rest) but weren't built tonight, to keep this pass reviewable
rather than touching three cascades at once.

## Storage layering (Bronze/Silver/Gold)

Already true today, just not formally named:

- **Bronze** = `data/raw/` (immutable, per PRINCIPLES.md principle 3) + `listings`/`listing_classification` before purification finishes
- **Silver** = purified `listings` (RELEVANT-scoped), `listing_product_type_classification`, `product_listings`
- **Gold** = `products`, `product_market_metrics`, `category_market_metrics`, `opportunity_signals`, and (as of tonight) the Opportunity Vector / Trend Engine / Simulation Engine outputs

The dashboard and API already read only Gold-equivalent tables (never
recomputing market metrics on page load) — the target architecture's
performance goal ("complex calculations do not rerun every time someone
opens a page") is already true, just not documented with these names.
**Renaming is cheap; the separation already exists.** Not prioritized
tonight since it's documentation, not capability.

## Incremental / hash-based reprocessing

**MISSING.** Every `scripts/classify.py` / `resolve_products.py` /
`calculate_market.py` run reprocesses the *entire* category from scratch,
every time — there is no row/content hashing anywhere, no "only process
NEW/CHANGED/UNRESOLVED" logic. At today's real scale (dental_models: 387
listings, denture_base: 22,135) this is why a denture_base resolution run
takes ~4-5 minutes even after tonight's performance fixes — most of that
work is genuinely wasted on unchanged data every single run. This is a real,
concrete, currently-unaddressed cost, and the most mechanically
straightforward item on this whole list to build (a content hash column +
a `WHERE hash != last_processed_hash` filter).

## What this audit recommends as the actual next step

Not "build the knowledge graph" and not "build supply chain intelligence" —
both are either presentation-layer work on data that already exists (3D
graph) or blocked on a real external data source that doesn't exist yet
(supplier matching). In order of real leverage:

1. **Incremental/hash-based reprocessing** — the biggest real performance
   problem this project currently has, fully solvable with existing data,
   no new external dependency.
2. **Route the similarity-tier pattern into relevance classification** —
   proven tonight on product-type; the same technique would let a
   denture_base-scale category (22,135 raw listings) get relevance-classified
   without 22,135 AI calls, which is the literal problem statement that
   started tonight's "go full dev mode" request.
3. **Category re-routing** (Stage 1's "IRRELEVANT here, but actually belongs
   in category Y") — a real, named, currently-completely-missing capability,
   and the clearest single gap between today's binary relevance filter and
   the target's stated requirement.
4. Everything else in this document, roughly in the order it's listed above.

No code changes are proposed or made by this document. It is the audit the
user asked for, not a build plan executed without confirmation.
