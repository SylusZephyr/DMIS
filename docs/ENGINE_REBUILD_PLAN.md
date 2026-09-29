# Engine accuracy and backend rebuild plan

Status (29 Sep 2026): E0–E5 and E6a shipped (PRs #25–#34). Open: removing the v1 path (E6b) and model inputs that only richer exports can supply (see *Acceptance on the real exports*).
Every phase is accepted only when the harness shows its checks passing on the three real exports in `data/raw/`.

## What the audit found (September 2026, on the real SellerSprite exports)

| Market | Badged listings | 80% interval coverage (hold-out) | AUC | Headline | Observed floor |
|---|---|---|---|---|---|
| Dental models | 32 / 376 | 22% | 0.72 | $171.5K/mo | $64.9K |
| Denture base | 19 / 65 | 63% | 0.46 | $113.6K/mo | $81.6K |
| Implants | 2 / 104 | 0% | 0.20 | $11.5K/mo | $2.7K |

- **Most sales are unobserved.** SellerSprite sales are "bought in past month" badge floors, and 71–91% of listings have no sales value. The headline is mostly a model extrapolation, and the model fails its own hold-out checks.
- **One quantity, several values:**
  - the market revenue appears as $171K (the v3 metrics store) and as $63.7K (the forecast history and the older segment table, which sums badge floors under the same name);
  - the same estimate has two intervals (the capacity table and the metrics table);
  - three opportunity scores coexist on the board.
- **Claims without evidence:**
  - a "Mature" trend is stated from one snapshot;
  - for implants, the interval's lower end ($2.1K) lies below the certain observed floor ($2.7K).
- **Inputs that pass unnoticed:**
  - the implants export is mostly floss, orthodontic supplies and periotomes;
  - denture base's listing-date column holds fractions, so no launch date is readable;
  - with no declared snapshot date, "as of" falls back to the newest launch date in the file;
  - 63–77% of observed revenue comes from listings whose category scope is still "review".

## Root causes

1. **No data contract.** What each column means (badge floor or exact sales), its unit, and whether a file is fit to use are not declared or checked.
2. **No single source of truth.** The v1 engine (`src/dmie`), the knowledge layer, metrics v3 and the board each compute their own revenue and opportunity numbers. Opportunity is computed in 9 files and revenue in 7.
3. **Estimates are shown as facts.** No abstain rule applies when validation fails.
4. **No accuracy gate.** Tests check that code runs, not that numbers agree with each other or with ground truth.

## Phases

- **E0: Truth harness (done).**
  - `src/dip/metrics/integrity.py` runs after every processing and is exposed at `GET /markets/{m}/integrity`.
  - It is shown on the market page. Thresholds are under `integrity` in `config/platform/metrics.yaml`.
  - Checks:
    - one value per quantity across stores;
    - segment additivity;
    - floor ≤ low ≤ estimate ≤ high ≤ ceiling;
    - one interval per estimate;
    - hold-out validation of the demand model;
    - undecided scope;
    - readable dates;
    - a declared snapshot date;
    - trends only with ≥ 3 snapshots;
    - brand shares add up;
    - one opportunity score across the segment table, the market headline and the engine (added in E2b).
- **E1: Data contract at ingestion.**
  - A per-source column spec: meaning, unit, and badge-floor vs exact.
  - A snapshot date required at import (from the file name or the upload form).
  - Fitness gates, e.g. "under 10 badged listings" or "category mix off", produce *insufficient data* instead of numbers.
  - Date columns that cannot be read are reported, not blanked.
- **E2: One metric layer.** One versioned set of listing, product, segment and market facts per snapshot. Every reader uses it: API, board, exports, analyst, forecast history. The parallel calculators in `src/dmie` and the duplicate capacity and segment revenue are removed, and one opportunity score is kept.
  - *Done (E2a):*
    - capacity takes every scope's interval from the engine's joint simulation (`MetricsOutput.interval_for`): market and segments reuse the stored rows, and taxonomy nodes sum the same product draws. The independence approximation is only a fallback, and it is labelled.
    - the market summary's `forecast` is the v3 fit over the v3 revenue history.
    - `segments.monthly_revenue` is the v3 estimate; the floor sum is kept as `observed_revenue_floor`.
    - maps and trend lists read market revenue through `dip.metrics.facts.market_revenue`.
  - *Done (E2b):* one opportunity score.
    - `segments.opportunity_score` is the explainable engine's, null with `opportunity_status` where it has too little evidence.
    - `opportunity_index` (metrics v3) stays as a named input.
    - The v1 score, computed on badge floors, is no longer stored.
    - The market headline (`summary.opportunity`, `metrics_v3.top_segment`), compare, galaxy, portfolio, market picker and universe map read that one score.
    - Harness check `one_opportunity_score`.
  - *Done (E3b):*
    - every market-size surface leads with the observed headline, as the market page does: home KPI, portfolio, market cards, market picker, market context menu and workspace hub.
    - each shows the modelled estimate beside it, flagged *not validated* where hold-out validation failed.
    - the bars mark the observed floor next to the model's range.
    - the API exposes `revenue_headline`, `revenue_basis` and `model_validated` (markets list) and `revenue_headline` plus validation counts (overview), all read through `dip.metrics.facts`.
- **E3: Honest estimation.**
  - Three tiers are always shown separately:
    - **observed:** the badge floors;
    - **bounded:** observed plus the maximum each unbadged listing could sell below the first rung;
    - **modelled.**
  - Calibrated (conformal) intervals, abstaining when hold-out validation fails.
  - Calibration against your own Seller Central sales (`/own-sales/estimate-check`).
  - *Done (first step):* the headline is the observed floor; `model_validated` + reasons in the summary (same thresholds as the truth harness); simulated ranges can no longer start below the floor or end above the ceiling. Since fixed: bootstrap extremes could put a small product's mean above its 95 % high. The anchoring now acts on the excess over the floor, with held draws at the ceiling, and the `estimates_within_bounds` check passes on all three real markets: no listing, product or segment estimate lies outside its own interval.
- **E4: Scope, relevance and identity.** Listings with scope "review" are excluded from totals until decided, with the uncertainty they add shown. Parent/child ASIN is used when present, so variations are never double counted.
  - *Done (first step): disclosure, not silent exclusion.*
    - Listings whose sub-category scope is still "review" stay in every total. Dropping them before a person decides would be a silent exclusion, and would shrink markets by 63–77 % on the real exports.
    - `revenue_month.undecided` records how much of the observed floor and of the estimate they carry, and the floor from decided sub-categories.
    - The market-size tile shows it, linking to the scope decisions.
    - Next: once decided, the totals follow the decisions (already true: out-of-scope listings are excluded as not relevant). Parent/child ASIN de-duplication is still open.
- **Model inputs (real-data finding).**
  - The three exports carry price, star rating, launch date and category only: no review count (评分数) and no best-sellers rank.
  - With those inputs the model cannot tell a 70-unit listing from a 5-unit one. On dental models, badged listings were predicted at 12/month against 71 true, so hold-out validation fails in every market.
  - BSR is now ingested (header match only). Its two model terms are measured on synthetic ground truth and off by default (METHODOLOGY §1.5).
  - The next accuracy step is exports that include 评分数 and 小类BSR, checked with the market's hold-out validation.
- **E5: Claims policy.** Trends and growth need ≥ 3 dated snapshots. Concentration and brand shares are computed on observed data or shown with their range. Every number is labelled observed, derived or modelled, with its n.
  - *Done (first step):* trend labels need `trends.min_periods_for_label` (3) dated snapshots.
    - Below that, the trend is *Insufficient evidence*, with the reason ("needs 3 dated snapshots; this market has 1").
    - Launch-date signals are reported separately as `launch_activity` (Rising / Steady / Falling): how many listings arrived, not whether demand grew.
    - The analyst's growth answer says so instead of stating a label.
- **E6: Backend architecture.**
  - An immutable raw layer, then validated staging, then versioned facts per snapshot (DuckDB/Parquet), then one metric layer, then a read-only API.
  - Caching keyed on data and config version.
  - Processing as a job graph, so a failed stage never leaves half-updated numbers.
  - The v1 path is deleted once the metric layer covers it.
  - *Done (E6a):* `lake.batch()` makes a run's ~25 curated writes all-or-nothing. A failed run keeps every table's previous version.
  - *Open (E6b):* delete the v1 calculators (`dmie.engine` market / forecast / opportunity) still run inside processing. No displayed number reads them any more; they feed only the older `forecasts` table and the `category` summary used by change alerts.

## Acceptance on the real exports (re-processed on main after #37, 29 Sep 2026)

| Market | Checks (pass / warn / fail) before | After | Observed (headline) | Model estimate | Remaining fail |
|---|---|---|---|---|---|
| Dental models | 5 fails | 11 / 0 / 1 | $64.9K | $174.2K (130–225K), not validated | hold-out coverage 25 % (80 % nominal) |
| Denture base | 4 fails | 9 / 2 / 1 | $81.6K | $113.6K (100–131K), not validated | hold-out AUC 0.46 |
| Implants | 6 fails | 11 / 0 / 1 | $2.7K | $11.5K (2.7–26.4K), not validated | only 2 badged listings |

- Every remaining failure is the demand model's hold-out validation. It is disclosed on every surface that shows a model-based number: market size, units, concentration, entrant success, product estimates, the opportunity board and the compare page. Units and revenue lead with the observed floor.
- A 13th check, `model_vs_own_sales`, compares the model with the user's own Seller Central sales once those are loaded (it is skipped until then).
- The warnings are data the platform cannot fix alone:
  - sub-category scope decisions are still pending on denture base (63 % of observed revenue). On dental models the scope fix in #37 (negated phrases and meta words are no longer exclusion evidence) cut this from 77 % to 3 %;
  - denture base's launch dates are unreadable.
- Making the model pass needs exports with 评分数 (ratings count) and 小类BSR. Both are ingested now; see METHODOLOGY §1.5 for how BSR enters the model.
