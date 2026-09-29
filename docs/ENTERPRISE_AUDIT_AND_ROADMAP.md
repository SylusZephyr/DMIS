# Enterprise Intelligence — Audit & Roadmap (Master Prompt 2)

Status of Platform v2 (`src/dip`, `frontend/`) measured on `main` at the merge of PR #2,
rebuilt from `data/raw/` (3 markets, 572 products). Everything listed as "exists" stays;
new work is added beside it. Any change to an existing behaviour is recorded in §4.

## 1. What the data actually contains (drives every design choice)

| Fact (measured) | Consequence |
|---|---|
| 3 SellerSprite exports, **one snapshot each** | Time-series forecasting reports "insufficient history"; trends must also use *within-snapshot* evidence (launch dates) and grow stronger with each new upload |
| Monthly sales known for **9–34 %** of products | Market size is a lower bound; every metric needs an explicit confidence |
| **No review counts / review text** in any export | Review-based confidence, review velocity and customer pain report "unavailable" — never estimated |
| `保底生产成本` (floor production cost) and `FBA($)` present | Launch evaluation can estimate unit economics from real comparables |
| `上架时间` (launch date) 76–92 % present | New-launch tracking and listing-growth trends are possible from one snapshot |
| **0 suppliers** imported | Supplier availability is "no evidence" until a supplier list is imported (never fabricated) |
| No seller country field | "Chinese competitors"-style statements cannot be made from this data; the system says so |
| Model level: **67–88 % of products fall into "other"** | Phase 2 must add a text-based model/variant discovery path, not only spec signatures |
| No micromotor dataset in `data/raw/` | The final business simulation runs on a real export; a micromotor export runs through the identical path when supplied |

## 2. Capability audit against the 12 phases

| Phase | Exists in v2 | Missing for enterprise use |
|---|---|---|
| 1 Confidence framework | Per-listing `data_confidence` (quality checks), segment `coverage`, `demand_verified` | One score per listing/product/segment/market built from **source reliability + completeness + verification + historical consistency**, with human-readable reasons |
| 2 Product understanding | Family → Segment → Model (spec signature) → Product → Listing | **Variant** level; model discovery when titles carry no numeric specs; hierarchy API |
| 3 Market simulation | Monte-Carlo P&L (`/simulate`) from comparables | Market-fit (demand, competition, price, reviews, suppliers), **risks**, attractiveness 0–100, positioning, **recommended strategy**, cost estimate from real comparables |
| 4 Predictive intelligence | Ensemble forecast when ≥3 periods; launch cohort | **Trend detection** (rising / declining / emerging / seasonal) from all available signals with confidence; listing growth and price movement; per segment and market |
| 5 Competitor intelligence | Brand leaderboard, HHI, top brand share | **Brand profiles** (position, weakness, opportunity), snapshot-to-snapshot **change tracking** (price, share, launches, rating, supplier moves) |
| 6 Analyst agent | Single-market keyword Q&A, optional LLM | Analyst over the **whole database** (markets, branches, products, suppliers, employees, opportunities, launches) with structured, cited answers; LLM optional and **traced** |
| 7 3D dashboard | Globe, Universe, Galaxy (size = sales, colour = opportunity, distance = similarity), graph explorer | Galaxy **brightness = growth**, filters and **compare**; customer-problem nodes in the graph; decision pages (launch, competitors, analyst, alerts) |
| 8 Employee intelligence | Employee KPIs + potential products | Per-category **market status, competitors, suppliers, recommended actions**, alerts inbox |
| 9 Shopping | Need/budget/purpose/experience ranking | **Recommendation map** (price × value), use-case/performance explanation, customer-only role |
| 10 Performance | Polars ingestion, 10⁶-row pipeline, parallel forecasts | **Incremental** processing (skip unchanged uploads, cache relevance by text), **API caching** keyed on market version, batch folder processing |
| 11 Real-time architecture | Connector base class (v1) | **Event log + bus**, change detection → graph/opportunity refresh → **alerts to the responsible employee**; connector registry for SellerSprite / Amazon / supplier / news APIs; inbound event endpoint |
| 12 Productization | Users, tokens, roles admin/manager/analyst/viewer | **product_manager** scoped to assigned categories, **customer** (shopping only), market scoping enforced in the API |

## 3. Roadmap (executed in this order; one commit per step, CI on every push)

1. **Confidence framework** — `dip.intelligence.confidence`; weights and source reliability in `config/platform/confidence.yaml`; listing → product → segment → market roll-up; reasons list.
2. **Product hierarchy** — variant discovery (distinguishing attributes inside a model), text-based model discovery fallback, `/markets/{m}/hierarchy`.
3. **Trend detection** — `dip.intelligence.trends`; signals: snapshot history, launch cohort / listing growth, price movement, review velocity (when present); seasonality only with ≥24 months.
4. **Competitor intelligence** — `dip.intelligence.competitors`; brand history table; diffs between runs.
5. **Launch evaluation** — `dip.intelligence.launch`; fit, risks, positioning, strategy, plus the existing Monte-Carlo.
6. **Events & alerts** — `events`/`alerts` tables, in-process bus, change detection after each run, owner routing, connector registry, `/events`, `/alerts`.
7. **Employee intelligence** — focus endpoint with recommended actions.
8. **Analyst agent** — `dip.intelligence.analyst`; intent routing + tools over all data; optional LLM with a trace table.
9. **Shopping map** — map coordinates + explanations.
10. **Performance** — incremental skip, relevance cache, API cache, batch processing; measured.
11. **Productization** — new roles and market scoping.
12. **Frontend** — decision pages and 3D upgrades.
13. **Final validation** — business simulation through the API on a real export; the five required documents.

## 4. Changes to existing behaviour (documented reasons)

| Change | Reason |
|---|---|
| Model level: products with no shared spec signature are grouped into text models (`model_basis` = spec / text / none) | 67–88 % of products sat in "other"; spec models are kept exactly as before |
| `/shopping/recommend` requires the new `shopping` permission (granted to every existing role) | a `customer` role must reach shopping only |
| Shopping results are ordered by coverage of the customer's distinguishing words, then score | "brushless micromotor" returned brushed motors first (price headroom outweighed the match); v1 recommender unchanged |
| `require()` also checks the market named in a route for scoped roles | product managers see only their categories' markets |
| Pipeline gains stages history, confidence, trends, competitors, events (events before the graph stages) | new intelligence; the pipeline still ends with the knowledge graph |
| Identical inputs are skipped unless `force` | incremental processing; the result would be identical |
| Shared AI client reads the first text block instead of `content[0]` | current models may return a thinking block first |
| Listing history keeps only accepted, relevant, usable records | a rejected listing appeared as a competitor's "new launch" |

## 5. Status

Phases 1–12 implemented and tested (`tests/platform/test_enterprise.py`); final validation in
`docs/BUSINESS_SIMULATION_REPORT.md`. Remaining work is listed in `PROGRESS.md`.
