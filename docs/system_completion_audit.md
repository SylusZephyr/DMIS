# System Completion Audit — Milestone 0

**Purpose**: honestly measure the current DMIE codebase against the
20-milestone "AI-powered global dental product intelligence, consumer
recommendation, and supply chain discovery platform" vision, and give a
real recommendation on sequencing before any of it is coded.

**Method**: read PRINCIPLES.md, README.md, PROGRESS.md, DECISIONS.md; inspected
every module under `src/dmie/`, `scripts/`, `dashboard/`, `tests/`,
`config/`, `prompts/`, and `database/schema.sql` directly (not sampled).

**Headline finding, stated up front**: this is a real, working, tested
prototype (395 passing tests, a live 6-stage pipeline, a 7-page
dashboard) — but it is a **single-category, single-marketplace, batch,
Amazon-only system with no supplier data, no personnel data, no
consumer-facing surface, and no product hierarchy beyond
category → product → listing**. The 20-milestone vision assumes several
categories of data (supplier/manufacturer records, employee/ownership
records, consumer interaction logs, offline/trade-show intelligence)
that **do not exist anywhere in this repository today**, not even as
stubs. Most of Milestones 8, 12, 13, 15, 16, and 17 are blocked on
**data acquisition**, not on code — no amount of additive engineering
unblocks them without that data existing first. This is flagged
explicitly in the recommended migration plan below, per Rule 5 ("audit
first, understand first, plan first") and PRINCIPLES.md's principle 12
("avoid overengineering — implement only what is required for the
current milestone").

---

## 1. Current system inventory

### 1.1 Data layer

| Area | What exists | File(s) |
|---|---|---|
| Ingestion | SellerSprite XLSX → normalized `listings` rows, deterministic, decision-logged | `src/dmie/ingestion/`, `src/dmie/cleaning/normalize.py` |
| Validation | Row-level structural checks (schema, ASIN, price, rating, image) | `src/dmie/cleaning/validation.py`, `src/dmie/validation/dataset_validator.py` (M16) |
| Upload lifecycle | Staged → validated → approved → ingested, with a dashboard UI | `src/dmie/database/datasets.py`, `dashboard/pages/7_Data_Ingestion_Center.py` (M16) |
| Connectors | One real connector (SellerSprite, wraps the Excel loader); a bare interface for future ones | `src/dmie/connectors/` (M16) |
| Database | DuckDB, 19 tables (see 1.2) | `database/dmie.duckdb`, `src/dmie/database/schema.sql` |

**Gap vs. Milestone 1 (Unified Data Collection Platform)**: CSV upload
is not supported (only XLSX). No API-based connector exists or could
exist yet — no external API has been identified or credentialed. A
"quality score" per dataset is not computed (the validation report has
PASS/WARNING/FAIL per check, but no single aggregate score).

### 1.2 Database schema (19 tables, all in `schema.sql`)

```
categories, listings, listing_classification,
listing_product_type_classification, products, product_listings,
match_candidates, product_market_metrics, category_market_metrics,
opportunity_signals, review_insights, decision_log,
pipeline_runs, classification_runs, product_resolution_runs,
dataset_uploads, dataset_versions, market_snapshots, market_changes
```

This is a **flat relational schema**, not a graph. There is no
`product_family`, `product_model`, `variant`, `company_category`,
`supplier`, `employee`, or `customer_session` concept anywhere in it.

### 1.3 Classification & AI

| Area | What exists | Gap vs. vision |
|---|---|---|
| Relevance classification | Rules + optional AI arbitration, 3-tier confidence routing, calibrated against a 50-row gold set | Works, but confidence is currently bimodal (0.95 rules / 0.0 AI-unavailable) since no `ANTHROPIC_API_KEY` has ever been configured in this environment — `sampling_threshold` is explicitly documented as uncalibrated for this reason |
| Product-type classification | Rules + AI, against a **frozen, hand-authored taxonomy** (`config/taxonomy/denture_base_v1.yaml`) | No AI-driven taxonomy *discovery* — `product_type_discovery.py` exists but is a separate, not-pipeline-wired module (see 1.7) |
| Entity resolution (dedup) | Blocking → similarity scoring → rules → AI arbitration → clustering | Solid; this is the closest existing match to Milestone 5's requirement and needs no rebuild |
| Image analysis | **None.** No listing image is ever sent to a model. | Full gap vs. Milestone 2's "AI analyzes: ... Images" |
| Review analysis | Extraction/aggregation/theme code exists and is tested against **synthetic** review text | **Full gap vs. Milestone 7**: SellerSprite (the only data source) has zero review text (`docs/data_dictionary.md` #12) — this isn't a code gap, it's a missing data source |
| AI infrastructure | One shared, production-safe wrapper (`src/dmie/ai/client.py::call_ai`) — retries, four explicit outcome states, used by 4 call sites | Reusable as-is for any future AI-driven milestone; no rebuild needed |
| Confidence/traceability | Every AI decision stores confidence + reason + version + timestamp (PRINCIPLES.md principle 6) | Already satisfies Milestone 2's traceability requirement — the gap is *input coverage* (no images, no offline sources), not the traceability mechanism |

### 1.4 Market & opportunity engines

Deterministic (Python/DuckDB, no AI) market metrics — sales, revenue,
price distribution, HHI concentration — computed per-product and
per-category (`src/dmie/market/`). Opportunity signals
(`src/dmie/opportunity/`) are evidence-based, closed-taxonomy
(`BUNDLE`, `PRICE_SEGMENT`, `COMPETITIVE_CONCENTRATION`, etc.), every
signal traceable to real numbers/quotes.

**Gap vs. Milestone 9 (Opportunity Scoring Engine)**: no single
"Opportunity Score" exists — signals are qualitative
(`status`/`signal_strength`), not a scored/ranked output. No growth,
offline-demand, entry-difficulty, or supply-availability inputs exist
(the last three require data sources that don't exist — see 2.1).

**Gap vs. Milestone 10 (Competitive Intelligence)**: brand distribution
and HHI concentration exist (Category Overview page); a dedicated
"pricing strategy"/"competitor positioning" analysis does not.

### 1.5 Snapshot & change detection (M16)

`market_snapshots` + `market_changes` — captures product-level
price/sales/listing-count state before and after every pipeline run,
diffs them. This is real, tested (13 tests), and is a genuine, if
narrow, foundation for Milestone 6's "listing age"/trend tracking (no
"listing age" field exists yet — SellerSprite's raw export has no
listing-creation-date column at all, per `docs/data_dictionary.md`, so
this is also a data-source gap, not a code gap).

### 1.6 Category configuration

`config/categories.yaml` already externalizes category-specific rules
(taxonomy, variant policy, description used in the AI relevance prompt)
— this **partially satisfies Milestone 3's "no hardcoded category
logic" requirement already**. Only one category (`denture_base`) has
real data; a second (`micromotor`) exists as an illustrative,
unvalidated placeholder with no raw export ever ingested. Scaling to
"hundreds of categories" is a data-acquisition and per-category
taxonomy-authoring problem (each category's taxonomy today is
hand-authored, e.g. `config/taxonomy/denture_base_v1.yaml` — see
`docs/product_taxonomy.md`), not a schema or pipeline-code blocker.

### 1.7 Dormant/unwired assets (built, never connected)

- `prompts/market_interpretation.md` — a versioned prompt template with
  no caller anywhere in `src/` or `scripts/`. Never wired into a
  pipeline stage.
- `src/dmie/classification/product_type_discovery.py` — exists, tested,
  but not called from `scripts/classify_product_types.py` or
  `run_pipeline.py`. The live taxonomy is hand-frozen, not
  AI-discovered.
- `scripts/build_dashboard_data.py` — an empty stub (documented as such
  in `run_pipeline.py`'s own docstring); the dashboard reads DuckDB
  directly instead.

These are worth knowing about before Milestones 4/18 reference "AI
discovers product segmentation" or an "AI Market Analyst" — there is
prior, unfinished work in this direction that a new build should
extend, not duplicate.

### 1.8 Dashboard

7 pages, Streamlit + Plotly, dark "command-center" theme applied
across all of them (recent work). Read-only except the new Data
Ingestion Center page (M16), which is the one deliberate write
exception. No graph/network visualization exists (Market Map is a 2D
price×sales scatter, not a hierarchical/zoomable product map).

**Gap vs. Milestone 11 (Intelligent Product Map)**: no
category→family→product→listing drill-down UI exists; Market Map
today is flat (one scatter per category, colored by product type, no
hierarchy navigation). Search/filter exist (Market Map's filters);
compare/zoom do not.

### 1.9 Tests & process discipline

395 tests, unit + integration, real-data-grounded where possible (gold
dataset, real ASINs in fixtures). Every milestone in PROGRESS.md/
DECISIONS.md documents real verification evidence, not just "done."
This discipline is the single biggest asset for building the next 20
milestones safely — it should be **extended, not replaced**, by
whatever comes next.

---

## 2. Gap analysis per new milestone

Legend: 🟢 mostly exists (extend) · 🟡 partial foundation (real code to
build, but grounded in something real) · 🔴 full gap (new subsystem,
and/or blocked on data that doesn't exist yet)

| # | Milestone | Status | Real blocker |
|---|---|---|---|
| 1 | Unified Data Collection Platform | 🟡 | CSV support, API connectors, aggregate quality score — all additive to M16's existing upload/validation/versioning flow |
| 2 | Data Accuracy Engine (image AI, confidence scores) | 🟡 | Traceability mechanism exists; image analysis is a full gap; no image data has ever been sent to a model |
| 3 | Universal Category Intelligence | 🟡 | Config layer already supports per-category rules; scaling needs per-category taxonomy authoring + real raw exports, which is a data problem, not a schema problem |
| 4 | Product Knowledge Graph | 🔴 | No hierarchy beyond product→listing exists in the schema; "AI discovers product segmentation" has no wired implementation (`product_type_discovery.py` is dormant) |
| 5 | Advanced Product Deduplication | 🟢 | Already the strongest part of the system (M6/M12/M16 disjointness fixes) — extend, don't rebuild |
| 6 | Market Capacity Engine | 🟢 | Sales/revenue/price/rating/reviews/competition all exist; "listing age" has no source column in the raw data |
| 7 | Customer Review Intelligence | 🔴 (data) | Code exists and is tested against synthetic data; **zero real review text exists in any data source used by this project** |
| 8 | Offline Market Intelligence | 🔴 (data) | No manufacturer/distributor/trade-show/news data source exists or has been identified. This is new data acquisition, not a code milestone |
| 9 | Opportunity Scoring Engine | 🟡 | Evidence-based signals exist; a single numeric score does not; several proposed inputs (offline demand, supply availability) are blocked on Milestones 8/16 |
| 10 | Competitive Intelligence | 🟡 | Brand share + HHI exist; pricing-strategy/positioning analysis does not |
| 11 | Intelligent Product Map | 🟡 | 2D scatter exists with filters; hierarchical drill-down UI does not |
| 12 | Product Manager Ownership System | 🔴 (data) | No employee/category-ownership record exists anywhere. Needs a real org chart as input before any UI can be built |
| 13 | Consumer Shopping Intelligence | 🔴 | No consumer-facing surface, no conversational UI, no budget/preference intake exists |
| 14 | Price-Sales Market Matrix | 🟢 | This is almost exactly Market Map's existing price×sales scatter — "best value/premium/budget" quadrant labeling is the only new piece |
| 15 | AI Conversational Shopping Agent | 🔴 | No chat interface exists; would reuse `call_ai()` but needs an entirely new interaction layer and a defined answer-grounding contract |
| 16 | AI Global Supply Chain Discovery | 🔴 (data) | No supplier/manufacturer data source exists. This is a new connector + a new data-acquisition effort (websites, directories, exhibitions), not additive code alone |
| 17 | Supply Chain Knowledge Graph | 🔴 | Depends entirely on 16 existing first |
| 18 | AI Market Analyst | 🟡 | `call_ai()` is reusable; grounding answers in real DB evidence is exactly this project's existing discipline (every AI decision already cites confidence/reason/evidence) — the pattern exists, the Q&A interface does not |
| 19 | Full Pipeline Validation | 🟢 | `run_pipeline.py` (M15) + this audit's own test discipline already cover this pattern; extend end-to-end tests as new stages are added |
| 20 | Final Enterprise Polish | 🟡 | PROGRESS.md/DECISIONS.md/docs/ are already extensive and disciplined; a deployment guide does not yet exist (no deployment has happened — this is still a local prototype) |

---

## 3. Cross-cutting architectural gaps

These aren't single milestones — they recur across several:

1. **No product hierarchy.** `products` is flat (one row per resolved
   product, one `product_type` string). Milestones 4, 11, and 14 all
   assume a multi-level hierarchy (family → type → model → variant)
   that doesn't exist in the schema. This needs to be designed once,
   carefully, since M5's entity-resolution logic (`variant_policy` in
   `categories.yaml`) already encodes *some* of this distinction
   (pack-quantity variants vs. color variants) — a new hierarchy should
   extend that existing concept, not duplicate it with a second,
   possibly-conflicting notion of "variant."
2. **Single marketplace, single real category.** Every metric, every
   threshold, every taxonomy file today assumes `denture_base` / US
   Amazon. Scaling to "hundreds of categories" and (implicitly, per
   "global" in the vision statement) other marketplaces is a real
   multiplication of config/taxonomy-authoring work, not just code.
3. **No non-Amazon, non-SellerSprite data has ever entered this
   system.** Milestones 8, 16, and 17 all assume it will. No such
   source has been identified, credentialed, or even name-checked
   beyond the milestone list itself.
4. **No consumer- or employee-facing identity/session concept exists.**
   Milestones 12, 13, and 15 all assume a "who is asking" concept
   (employee login + category ownership; a customer session with
   budget/preference state) that has no analog anywhere in the current
   system, which has exactly one implicit "user": whoever runs the
   pipeline scripts or opens the dashboard.
5. **`ANTHROPIC_API_KEY` has never been configured in this
   environment.** Every AI-dependent stage that already exists
   (relevance, product-type, entity-resolution arbitration) currently
   runs rules-only and queues everything uncertain for human review.
   Milestones 2, 7, 15, and 18 all assume real AI calls will happen —
   none of the newly proposed AI work can be *verified end-to-end*
   until this is configured, the same limitation that already applies
   to the existing pipeline today.

---

## 4. Recommended migration plan

Sequencing by **what's actually unblocked today**, not by milestone
number — several milestones in the original list (7, 8, 12, 13, 15, 16,
17) cannot be meaningfully built, only stubbed, until their underlying
data source exists. Building UI or schema against data that doesn't
exist risks exactly what PRINCIPLES.md principle 8 forbids: presenting
something as real when it isn't.

**Tier 1 — buildable now, no new data source needed** (in order):
1. Milestone 1's remaining gaps (CSV upload, aggregate quality score) —
   small, additive to the M16 foundation already in place.
2. Milestone 14 (Price-Sales Market Matrix quadrant labeling) — nearly
   free, direct extension of the existing Market Map.
3. Milestone 5 refinement (already strong; low-risk to harden further).
4. Milestone 6 gaps that don't need a new data source (competition
   metrics beyond HHI).
5. Milestone 10 (competitive/pricing-strategy analysis) — computable
   from data already in the database.

**Tier 2 — buildable now, but needs a real design decision first**
(genuine architecture work, not just more tables):
6. Milestone 4 (Product Knowledge Graph) — needs the variant-policy
   question above resolved *before* schema work starts, since M5's
   existing logic already has an opinion on this.
7. Milestone 3 (multi-category scaling) — mechanically works today for
   a 2nd, 3rd, Nth category; the real cost is taxonomy-authoring time
   per category, which is a content task more than an engineering one.
8. Milestone 9 (Opportunity Scoring) — can score on the inputs that
   already exist (market size, competition, pain points *if/when*
   Milestone 7 has real data) without waiting for offline-demand/
   supply-availability inputs; those two inputs can be added later
   without breaking the score's schema.
9. Milestone 11 (hierarchical product map UI) — buildable once
   Milestone 4's hierarchy exists.
10. Milestone 18 (AI Market Analyst) — the grounding discipline already
    exists project-wide; buildable as a read-only Q&A layer over the
    existing (and Tier 1/2-expanded) database, once `ANTHROPIC_API_KEY`
    is configured for real end-to-end verification.
11. Milestone 2 (image AI, confidence scoring) — buildable once an API
    key is configured; the traceability mechanism to store results
    already exists.
19. Milestone 19 (Full Pipeline Validation) — an ongoing discipline
    across every tier above, not a one-time milestone; already the
    pattern this project follows.
20. Milestone 20 (Enterprise Polish) — mostly documentation; the
    deployment guide specifically requires knowing where this will
    actually be deployed, which hasn't been decided.

**Tier 3 — blocked on data that doesn't exist yet; needs a decision
from the user/product owner before any code is written**:
- Milestone 7 (real review text — does SellerSprite or another source
  actually have this? Needs sourcing.)
- Milestone 8 (offline sources — which specific manufacturers,
  directories, or trade shows? Needs identification.)
- Milestone 12 (personnel ownership — needs a real org chart: who owns
  which categories?)
- Milestone 13 & 15 (consumer-facing — needs a decision on *who* the
  consumer-facing product is for and how they'd access it — a public
  site? An internal tool? This changes the entire design.)
- Milestone 16 & 17 (supply chain — needs a decision on data source and
  legal/ethical scope for e.g. web crawling manufacturer sites).

**Recommendation**: proceed with Tier 1 and the parts of Tier 2 that
don't require new data sources, milestone by milestone, with the same
implementation → test → documentation → verification discipline this
project has used throughout (395 tests, real-browser dashboard
verification, PROGRESS.md/DECISIONS.md entries per change). Do not
start Tier 3 items until the user has supplied — or explicitly decided
how to acquire — the underlying data each one depends on. Building
schema, UI, or "AI analyst" answers on top of data that doesn't exist
would violate this project's own foundational rule against fabricating
what isn't real.

---

## 5. What this audit did NOT do

Per the brief: no code was written or modified. No milestone beyond 0
was started. This document is the complete deliverable for Milestone 0.
