# Phases 5–8: plan and execution strategy

This document turns `ACCURACY_STRATEGY.md` into build steps. Every step lists its dependencies, design, tests and exit criterion.

**Order of work:** each step only consumes outputs that are already validated.

```
5.1 demand model (done)
 └─ 5.2 metrics engine v3 ──┬─ 5.3 category boundary + segmentation ──┬─ 5.4 gap analysis → recommended product
                            │                                         │
                            └──────────── Phase 6 decision surfaces (API first, then pages) ───────────┘
                                             └─ Phase 7 analyst (Gemini) + Chinese/English
                                                  └─ Phase 8 alerts, workflow, pilot on v3 + acceptance
```

**Rules for every step:**
1. Tests first, using invariants, synthetic ground truth and API contracts.
2. Constants go in `config/`.
3. Methods are documented in `METHODOLOGY.md`.
4. The full suite and ruff must be green before any push. Changed pages are checked in a browser with the real data.
5. Existing tests are only changed when their *premise* changes (for example, the opportunity definition). The commit message states why. Tests are never weakened to pass.

---

## Phase 5 — Analytics core (finish)

### 5.2 Metrics engine v3 (`src/dip/metrics/engine.py`)

- **Pipeline stage `metrics`**, placed after `analytics`:
  - fits the demand model on the market's accepted and relevant listings
  - simulates the listing → product → segment → brand → market totals jointly
  - writes a long-form curated table **`metrics`**, one row per (scope, scope_id, metric), with value, low, high, n, unit, formula, basis and caveat
  - adds v3 columns to `listings`, `products` and `segments`:
    - `units_est`, `units_lo`, `units_hi`, `units_floor`, `units_ceiling`
    - `revenue_est`, `revenue_lo`, `revenue_hi`
- **Metrics.** Each is a function `(inputs) → MetricValue`, with its formula id in `METHODOLOGY.md` §2–§9.

  | Group | Metrics |
  |---|---|
  | Size | market and segment revenue and units: floor, estimate, 95 % interval, ceiling |
  | Structure | brand shares (estimated), HHI and top-3 share with intervals, brand count, effective number of competitors (1/HHI) |
  | Entry (launch dates) | entrant revenue share (≤ 12 months); entrant success rate (entrants whose estimated units ≥ segment median of established products; Beta posterior interval); median months to first badge |
  | Unit economics (per listing) | margin = price × (1 − referral fee) − fulfilment fee − unit cost. Fee and cost fields from `metrics.yaml`; invalid values (e.g. negative cost) quarantined with a reason. Segment median margin, margin rate and share of profitable listings |
  | Price structure | price bands (quantiles), demand per listing by band, price association from the demand model |
  | Quality gap | share of listings rated below `quality_threshold`, weighted by estimated units; rating bar = median rating of the top decile by estimated revenue; review-text pain when reviews exist |
  | Saturation | listings per 1,000 estimated units; share of listings with no badge (< first rung) |
  | Difficulty | reported as its components, not a single number: concentration, rating bar, entrant failure rate, review moat (when review counts exist) |
  | Opportunity (primary) | expected monthly profit for a new entrant = E[entrant units] × unit margin. Entrant units come from the segment's entrants; with < `min_entrants`, they are shrunk to the market's entrants (empirical Bayes); with no entrants, from the lower quartile of established products (stated). Simulated, giving P(profit > 0) and an interval. |
  | Opportunity index (secondary) | 0–100, a geometric mean of absolute component scores (documented transforms in `metrics.yaml`, not ranks), so it is comparable across markets. Each component is shown. |
  | Evidence grade | A–D from n, sales coverage (badged share), snapshot count and the demand model's cross-validation AUC |

- **Replaces:**
  - `opportunity_score` in `segments` and `products` becomes the v3 index; the old v2 score is kept as `opportunity_v2`.
  - The product opportunity is the segment's expected entrant profit, adjusted by the product's position (price band demand and quality gap vs. its segment), **not** "sells well = opportunity".
- **API:**
  - `GET /markets/{m}/metrics?scope=&scope_id=`
  - `GET /markets/{m}/segments/{id}/explain`
  - `GET /products/{id}/explain`
  - `GET /methodology` (served from `METHODOLOGY.md`)
- **Tests:**
  - invariants: segment totals sum to the market; shares sum to 1; floor ≤ estimate ≤ ceiling; intervals contain the estimate
  - synthetic: HHI and segment ranking recovered; planted "easy" segment has the highest expected entrant profit
  - unit economics on hand-computed rows
  - the end-to-end pipeline test still passes
- **Exit:** every metric has formula, interval, n and caveat; the synthetic gates pass.

### 5.3 Category boundary and taxonomy-anchored segmentation

- **Boundary.** Every listing's marketplace sub-category (`category`, e.g. 小类目) is scored against the market's category definition in `categories.yaml`:
  - `description`, plus optional `include` / `exclude` terms and `include_leaf` / `exclude_leaf` lists
  - the score is an ensemble of lexical overlap and embedding similarity (plus an optional LLM vote, traced)
  - result per sub-category: in (confidence), out (confidence) or **needs review**
  - confirmed decisions are stored in the business DB and override the classifier on re-runs
  - excluded listings keep their reason (`excluded_reason = "out of category scope: <leaf>"`)
  - markets without a definition keep everything and report their composition
- **Segmentation:**
  - Family = marketplace sub-category.
  - Segment = attribute/text cluster *within* the sub-category when it has ≥ `split_min_products`; otherwise the sub-category itself.
  - Falls back to pure text discovery when the source has no sub-category.
  - Keywords: class-based TF-IDF over uni- and bi-grams, with a generic marketing stop-list in config (pcs, pack, set, colours, "for", sizes…).
  - Description: generated from statistics ("34 products · mostly 6 pcs acrylic trays · $8–19 · led by BrandX 31 % (est.) · 12 % launched in the last year").
- **Tests:**
  - synthetic contamination scenario: precision ≥ 99 % on accepted listings, and abstentions routed to review
  - segment keywords exclude stop terms
- **Exit:** implants' composition is reported correctly; scope decisions can be reviewed in the UI; segments carry keywords and descriptions.

### 5.4 Gap analysis → recommended product (`src/dip/metrics/gaps.py`)

- **Features per listing:** extracted specs (pack, rpm, watt, ml, g, material/type tokens), recurring title n-grams, price band.
- **Per segment × feature value:**
  - supply share (listings)
  - demand share (estimated units)
  - **lift** = demand per listing with the feature ÷ without it, with a bootstrap interval over listings and demand draws
  - entrant success with the feature
- **Gaps:** lift lower bound > 1, supply share < demand share, n ≥ `min_support`.
- **Recommended spec:** the best non-conflicting gap features, plus the price band with the highest demand per listing whose unit margin is positive. It comes with comparables, expected entrant profit, evidence and caveats.
- **Tests:** a synthetic market with a planted under-supplied feature must be recovered; no recommendation is made when no lift is significant.

---

## Phase 6 — Decision surfaces rebuilt on v3

Built API first, then UI. Every number on screen gets an ⓘ explain popover (formula, inputs, interval, n, caveat) and an evidence grade. Empty states say *why* and *what data would fill them*.

| Page | Plan |
|---|---|
| **Market Analysis** | Header: size floor / estimate (interval) / ceiling, evidence grade, composition by sub-category. **Price × demand matrix with controls**: snapshot selector and play-through when history exists, colour metric, size metric, segment filter, log/linear. Segment cards: keywords, generated description, opportunity (expected entrant profit), difficulty components, saturation, entry, margin. Forecast panel (≥ 3 snapshots; otherwise the entry analysis with an explicit "needs monthly snapshots"). Quality/pain panel (rating-based proxy, or review themes when reviews exist). Recommended products (5.4). |
| **Product page** | Estimated units and revenue with intervals (floor/ceiling); unit economics; position in segment (price percentile, quality vs. rating bar); product opportunity breakdown with each component's formula and value. |
| **Competitors & Trends** | Brand table on estimated shares with intervals; HHI; entrants; brand price and quality position; changes between snapshots (tested for significance against intervals); segment momentum. |
| **Launch Simulator** | Inputs: spec, price, unit cost, fees. Output: segment match, expected entrant units (entrant distribution), profit distribution, P(profit > 0), break-even units, comparables, gaps the idea fills, risks. Scenario comparison on the same engine. |
| **Shopping Mode** | Multi-criteria utility (value for money = estimated quality ÷ price, popularity from estimated units, rating with a Bayesian average, feature match). A Pareto front of non-dominated products. Explanations per recommendation. |
| **Product Galaxy** | Fix broken interactions. Colour by any trait (segment, sub-category, brand, price band, margin, opportunity, entrant, evidence grade) with a legend. Size by estimated revenue. Structured links (same product, same segment, same brand, similar spec) toggled by type. Filters and search. |
| **Knowledge Graph** | A typed ontology: Market → SubCategory → Segment → Product → Listing; Brand, Feature, PriceBand, Supplier, Trend, Gap, Recommendation. Every edge carries **evidence** (metric, value, n) and a **strength**. A "why is this connected" panel; path explanation ("Segment X → Gap: brushless (lift 1.8×, CI 1.3–2.4) → Recommendation"); filters by edge type and strength. |

- **Tests:** API contract tests per endpoint; the frontend lint, type check and build; browser checks on the real markets.

## Phase 7 — Intelligence and language

- **Market Analyst (Gemini free tier, `GEMINI_API_KEY`):**
  - tool-grounded: intent → computed answer from the v3 tables → Gemini phrases it
  - every number in the reply must appear in the evidence rows, otherwise the reply is rejected and the computed answer is shown
  - the reply cites its rows; every call is traced
  - offline mode: the computed answer only
  - intents cover: market size, best segment, recommended product, competitors, entry, margin, what-if (launch), comparisons, "why"
- **Chinese / English:**
  - a `next-intl`-style dictionary (`frontend/messages/{en,zh}.json`) for every UI string, metric name and explanation
  - a language switch stored per viewer
  - API explanations carry `en` and `zh` variants
  - data values (titles, brands) are never translated
- **In-app Methodology page** rendered from `METHODOLOGY.md`.

## Phase 8 — Operations and trust on v3

- **Alerts & Events:** a change becomes an event only when it is **statistically significant**, i.e. the intervals of two snapshots do not overlap, or a permutation test passes. Each event shows its evidence. Noise events are removed.
- **Workflow:**
  - projects are created from v3 recommendations and store the v3 prediction (entrant units, profit interval)
  - the approval flow is kept
  - inputs are validated (no free-form numbers without units), fixing the "false inputs" found in review
  - the inbox and daily summary are rebuilt on v3 events
  - each flaw is fixed with a regression test
- **Pilot:**
  - the accuracy dashboard shows the synthetic gates, the demand model's cross-validation per market, and label-based accuracy when labels exist
  - feedback triage is kept
- **Acceptance test:** a fresh market goes through upload → boundary → segments → v3 metrics → recommendation → launch simulation → project → event, all in both languages.
- **Docs:** `METHODOLOGY.md` complete, `PROGRESS.md`, `PROGRESS.md`, user manual.

## Risks during execution

| Risk | Handling |
|---|---|
| Changing segment definitions breaks tests and pages that assume text clusters | Keep the `segments` schema; new columns are additive; pure text discovery remains the fallback |
| The demand model is slow on 10⁶ listings | The bootstrap uses a capped subset (`max_bootstrap_rows`); simulations aggregate on the fly; measured on the benchmark data |
| Gemini unavailable (no key, quota) | Offline computed answers always work; the phrasing layer is optional |
| Scope too large for one pass | Commit and push per step, so every step is usable on its own |
