# Accuracy strategy — a universal market-analysis tool

**Goal:** a category-agnostic, dataset-agnostic tool whose every number is correct where correctness is attainable, calibrated where it is an estimate, and never shown with more confidence than the evidence supports.

## 1. What "99.99 %" can and cannot mean

The system produces six kinds of output. Each gets its own accuracy definition and target. One blended "accuracy" figure would hide the parts that are weak.

| Output type | Examples | Attainable target | How it is proven |
|---|---|---|---|
| **A. Computation** | sums, shares, prices, counts, rankings of given numbers | **100 % exact** | Invariants, property-based tests, two independent implementations (pandas and DuckDB SQL) that must agree, and reconciliation to source totals |
| **B. Data parsing** | column mapping, units, currency, dates, sales buckets | **≥ 99.9 % field-level**, with every exception quarantined (never silently dropped) | Schema contracts, validators, labelled audit of ingestion |
| **C. Classification** | in-category or not, product type, same-product grouping, segment | **≥ 99 % precision on accepted decisions (99.9 % where review capacity allows)** | Selective prediction: an ensemble (rules + embeddings + LLM) with calibrated confidence. Decisions below the threshold **go to a human** instead of being guessed. Labelled audit samples report 95 % intervals. |
| **D. Estimates** | sales of unbadged products, market size, brand shares | **Calibrated**: 95 % intervals contain the truth 95 ± 2 % of the time; point error reported | Interval-censored statistics, synthetic ground-truth simulation, and hold-out validation on known values |
| **E. Forecasts** | growth, 6/12-month demand | **Calibrated intervals**, and skill must beat a naive forecast | Rolling-origin backtests and conformal prediction intervals |
| **F. Recommendations** | best segment or product to launch, supplier ranking | **Decision quality measured by outcomes** | Launched products tracked against their predictions, with calibration feedback |

**99.99 % is only reachable for A**, and for **B and C on the decisions the system accepts**. Accepting only confident decisions and routing the rest to review is how systems that must be right, such as medical coding and fraud review, reach that level. For D and E, no method can be 99.99 % accurate about quantities nobody observed. Claiming it would be false. The correct standard is intervals that are honest, verified by calibration tests.

## 2. The biggest accuracy risks, and how each is mitigated

| # | Risk (found in the current system or data) | Impact | Mitigation |
|---|---|---|---|
| R1 | **Sales are censored**: SellerSprite values are Amazon "bought in past month" badges (50+, 100+, … 1K+, 2K+). "Missing" means fewer than 50/month; "200" means 200–299. | Market size, shares and demand were treated as exact or missing, so they were biased. | Detect the badge ladder automatically (config ladder; verified on the data). Model sales with **interval-censored maximum-likelihood regression**. Every product gets an estimate and an interval. Market size = floor, estimate and ceiling. |
| R2 | **Category contamination**: keyword-built exports mix in unrelated products (e.g. water flossers in "implants"). | Every metric describes the wrong market. | Anchor on the marketplace taxonomy (browse node / 小类目). Category include/exclude rules live in config. Report the composition. An ensemble classifier with abstention goes to review. |
| R3 | **Single snapshot**: no history. | Growth and forecasts cannot be measured. | Never extrapolate. Use launch-cohort analysis ("entry momentum"), labelled as such. The scheduler ingests monthly exports, and forecasts switch on at ≥ 3 periods with backtests. |
| R4 | **Arbitrary constants and weights** (4.7, 1.2, 0.25 …) and scores that are *ranks within a market*. | Scores cannot be compared across markets and have no validated meaning. | Metrics defined in **absolute, interpretable units** ($, units, %, HHI). Every transform in config with its rationale. The composite index is secondary to the primary metric, **expected entrant profit ($) with an interval**. Weights calibrated against outcomes once outcomes exist. |
| R5 | **Selection bias of exports** (keyword search, top-N). | The market looks bigger or smaller than it is. | Record how each export was built (search terms, filters, limits). Show "coverage of the category" when a total is known. Sensitivity analysis. |
| R6 | **Entity resolution errors** (listings ≠ products). | Double counting, wrong best listing. | Pairwise audit with labels. Spec vetoes. Abstention on low-confidence merges. Stable IDs (done). |
| R7 | **LLM hallucination** in the analyst. | False statements. | Numbers only come from computed tables. The LLM only phrases, and every sentence cites its source row. Answers are rejected if they contain a number not present in the evidence. |
| R8 | **Data errors** (negative cost, non-date launch column, impossible prices). | Garbage in. | Validators with quarantine and reasons. Robust statistics (medians, winsorising). Anomaly flags visible in the UI. |
| R9 | **Drift**: marketplace changes (badge ladder, fee schedule), category shifts. | Silent degradation. | The ladder and fees are configurable and checked on every upload. Drift monitors on feature distributions, and alerts when calibration tests fail. |
| R10 | **Small samples** (segments with 3 products). | Unstable, overconfident metrics. | Minimum-n rules in config. Empirical-Bayes shrinkage toward the parent level. Intervals widen automatically. "Insufficient evidence" is shown instead of a number. |

## 3. Accuracy engine: methods per metric (Phase 5)

All methods are category-agnostic and driven by config. There is no code path specific to a dataset.

- **Demand.**
  - Interval-censored log-normal regression, fitted by maximum likelihood with `scipy`. Covariates: log price, rating, listing age, marketplace sub-category, size tier, brand scale.
  - The segment level is shrunk toward the market level.
  - Per product: conditional expected units, and 80 % and 95 % intervals.
  - Validated by synthetic ground truth (§4) and by bucket-hit rate on held-out badged products.
- **Market size:** Σ lower bounds (floor) · Σ conditional expectations (estimate, with a simulated 95 % interval) · Σ upper bounds (ceiling).
- **Concentration:** HHI and top-3 share on simulated revenue draws, giving 95 % intervals.
- **Entry analysis (from launch dates):**
  - new-entrant share of revenue (last 12 months)
  - entrant success rate (share of entrants reaching the segment median)
  - time to reach median sales
  - Each has an interval.
- **Growth:** forecasts only with ≥ 3 periods — an ETS/Theta/naive ensemble chosen by rolling-origin CV, with conformal intervals. Otherwise the entry analysis stands in, labelled as such.
- **Unit economics:**
  - margin = price × (1 − referral fee) − fulfilment fee − unit cost
  - Fees and cost fields come from config and the data. Invalid values are quarantined.
- **Price structure:**
  - demand elasticity from the demand model's price coefficient (with SE)
  - price bands with demand per listing
- **Quality gap (customer-pain proxy when no review text):** share of meaningful sellers rated below the config threshold, and the rating bar of top sellers. Review-text themes when reviews are supplied.
- **Saturation:** listings per 1,000 units of demand, and the share of listings with no badge (under 50/month).
- **Difficulty:** concentration, incumbent rating bar, entrant failure rate and review moat (when review counts exist). Each is reported in its own unit.
- **Opportunity (primary):** *expected monthly profit for a new entrant* = entrant units (from the segment's entrant outcomes, shrunk to the market) × unit margin, simulated, with P(profit > 0) and an interval.
  - A secondary 0–100 index uses absolute, documented transforms from config.
- **Product recommendation:** attribute and price-band **gap analysis**.
  - For each extracted attribute value and price band: demand per listing, supply share and lift vs. the segment, with bootstrap intervals.
  - A recommended spec combines the values whose lift's lower bound is above 1, checked against unit economics and comparables.
- **Segments:**
  - The marketplace sub-category is the anchor. Attribute/text clustering runs within it when it is large enough.
  - Keywords come from class-based TF-IDF.
  - Descriptions are generated from the segment's statistics, not free text.
- **Every metric** carries: value, interval, n, unit, formula id, inputs used, coverage and caveats (API `explain` payload plus `docs/METHODOLOGY.md`).

## 4. Proving accuracy without ground truth: validation harness

1. **Synthetic ground-truth markets.** A generator creates markets with known true sales from configurable distributions: category mixes, contamination, price elasticity, entrants, brand concentration. It applies the real-world observation process (badge censoring, missing fields, duplicate listings, noise), runs the full pipeline and compares against the truth:
   - interval coverage
   - point-estimate error
   - classification precision and recall
   - ranking correctness (does the best true segment come out on top?)

   This runs in CI, across many seeds and scenario types, so accuracy is **measured continuously on every change**, independent of any particular dataset.
2. **Hold-out validation on real data:** hide known badges, predict them, and check the bucket-hit rate and interval coverage.
3. **Human-labelled golden sets** per category (the labelling workflow from Phase 4), used for regression tests.
4. **Backtests** on real history as monthly snapshots accumulate.
5. **Outcome tracking** of launched products, the ultimate test of recommendations.
6. **Accuracy dashboard:** for each metric — target, measured value, interval, sample size, trend. A **CI gate fails the build** if any measured accuracy drops below its target.

## 5. Data strategy: accuracy grows with evidence

The largest accuracy gains come from better inputs, not cleverer formulas. In order of value:

1. **Monthly snapshots** of the same categories: history turns guesses into measured growth.
2. **BSR (best-seller rank)** and its history (Keepa or SellerSprite BSR fields): turns the badge interval into near-continuous sales estimates.
3. **Review counts and review text:** the real review moat and real customer pain.
4. **Own sales data** (SP-API or Seller Central): exact ground truth to calibrate the estimators.
5. **A category-total reference** (SellerSprite category report or Brand Analytics): anchors market coverage.

Every source is added through the adapter layer. Nothing is specific to a dataset.

## 6. Governance

- The review queue for every abstained decision; monthly label audits.
- Model cards for each estimator: data, assumptions, validation results, known failure modes.
- Every AI decision is traced (model, prompt version, input, output, confidence, time, status).
- Recommendations display an evidence grade. They never show a single number without its interval and sample size.

## 7. Roadmap and exit criteria

| Step | Deliverable | Exit criterion |
|---|---|---|
| 5.1 | Badge-ladder detection and interval-censored demand model; synthetic ground-truth harness | On synthetic markets: 95 % interval coverage within 93–97 %; market-size estimate error reported; CI gate |
| 5.2 | Metrics engine v3 (market size, concentration, entry, unit economics, saturation, difficulty, opportunity as expected entrant profit) with `explain` payloads and `METHODOLOGY.md` | Every metric has formula, interval, n; invariants tested; synthetic ranking accuracy reported |
| 5.3 | Category boundary (taxonomy anchor + config rules + abstention) and taxonomy-anchored segmentation with class-based TF-IDF keywords | Contamination scenarios: precision ≥ 99 % on accepted listings in synthetic tests; implants composition reported correctly |
| 5.4 | Attribute and price-band gap analysis → recommended product spec | Synthetic: planted gaps recovered; each recommendation cites lift with an interval |
| 5.5 | Pages rebuilt on v3: Market analysis (time control, segment descriptions, explainers), product breakdown, competitors and trends, launch simulator, shopping mode, galaxy (trait colours, structured links), knowledge graph (evidence-weighted ontology, "why" panels) | Every number on screen has an explanation and interval |
| 5.6 | Market Analyst with Gemini (free tier): computed answers, cited, with the number-hallucination guard | Every number in an answer traceable to a table row |
| 5.7 | Chinese / English interface | All pages switchable, including metric explanations |
| 5.8 | Workflow, alerts and pilot sections fixed on v3 | End-to-end flow tests with real inputs |
