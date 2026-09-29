# Methodology: metrics engine v3

Every number the platform shows is defined here: its formula, inputs, assumptions and uncertainty, and how its accuracy was measured. Constants live in `config/platform/metrics.yaml`. Code lives in `src/dip/metrics/`. The accuracy targets per output type are in `docs/ACCURACY_STRATEGY.md`.

Status: §1–§8 are implemented. Each metric row the API returns carries its formula id (`M…`), which points to the section below.

---

## 1. Demand (units per listing, per month)

### 1.1 How sales were observed (`observation.py`)

Marketplace exports often carry Amazon's **"N+ bought in past month" badge**, not a sales figure. Such a column only contains values on a fixed ladder (50, 100, 200 … 900, 1K, 2K … 9K, 10K, 20K … 100K), and never a value below the first rung.

**Detection** is made from the data alone, for every upload. The column is treated as badges when:
- at least `detect_min_share` (98 %) of observed values are ladder rungs,
- none is below the first rung, and
- at least `detect_min_observed` values exist.

Otherwise the values are treated as exact figures. The reason is recorded and shown.

| Source value | Badge column | Exact column |
|---|---|---|
| `v` | `v ≤ units < next rung` (top rung: `< v × top_rung_multiplier`) | `units = v` |
| missing | `0 ≤ units < first rung` (no badge = below the threshold) | unknown (no information) |

All three reference exports (implants, dental_models, denture_base) are 100 % on the ladder, with a minimum of exactly 50. Their "missing" sales are therefore **censored below 50**, not unknown. The previous engine treated them as unknown, which biased market size down by about 40 % (§1.5).

### 1.2 Model (`demand.py`)

    log units_i = b0 + b · z_i + sigma · e_i,     e_i ~ F  (normal, or Student-t with df in {10, 5, 3})

- **Covariates `z`:** standardised log price (with a spline, below), rating and log listing age, plus one indicator per marketplace sub-category.
  - **Curved price response (`price_spline`).** Log price enters linearly *plus* a restricted (natural) cubic spline with knots at the 5 / 35 / 65 / 95 % quantiles of log price (2 curvature terms). The curve is cubic between knots and linear beyond the end knots, so a launch price outside the data extrapolates linearly. The spline terms are standardised and ridge-penalised like every coefficient, which shrinks toward the linear fit. With fewer than `min_informative` (60) badged listings (badge data) or observed values (exact data), or too few distinct prices, the price term stays linear. Both reference exports are below that threshold (dental_models 32, denture_base 19 badged in scope), so their fits are unchanged.
  - **Review count (`log_reviews`, off by default).** log(1 + reviews) with a missing indicator is implemented and used when added to `covariates` and the source has a reviews column. It is off because it made market totals worse on synthetic ground truth (§1.5): review counts are small Poisson counts for slow sellers, so log(1 + reviews) is a noisy, floored proxy exactly in the region below the first badge where the model must extrapolate.
  - Sub-categories with fewer than `category_min_listings` listings are pooled.
  - Missing covariates are median-imputed, with a missing-indicator term.
  - The listing's age is measured from the snapshot date, or from the latest launch date in the data when no snapshot date is declared.
- **Estimation:** maximum likelihood over the *intervals*. Each listing contributes `F((log U − mu)/sigma) − F((log L − mu)/sigma)`, or the density for exact values.
  - A ridge penalty (`ridge`) on the standardised coefficients keeps small samples stable.
  - This is interval regression, a generalised Tobit model, and the standard estimator for censored and interval data.
- **Error family:** every family in `error_families` is fitted and the lowest AIC is kept, so heavy-tailed markets get heavy-tailed errors. Too few informative observations (`min_observed`) means an intercept-only model.
- **Parameter uncertainty — parametric bootstrap** (`bootstrap` refits):
  1. Simulate units from the fitted model.
  2. Re-apply the badge censoring.
  3. Re-select the error family and refit.
  - A case bootstrap was measured and rejected: it under-states the uncertainty of extrapolating below the first rung in small samples.
- **Bias correction:** `theta_bc = theta_hat − (mean(theta*) − theta_hat)`. This removes the small-sample bias of the censored MLE, which over-estimated sub-threshold sales by about 0.4 standard errors.

### 1.3 Per-listing estimate

Given the fit, a listing's units follow the model **truncated to its observed interval**:
- a "200+" listing is placed inside 200–299,
- an unbadged listing inside 0–49,
- in each case according to its price, rating, age and sub-category.

The reported values are:
- the conditional mean (closed form for normal errors; midpoint quadrature over `quadrature_nodes` conditional quantiles otherwise)
- the central 95 % interval

Estimates never leave the observed interval.

### 1.4 Aggregates: market, segment, brand (M1.4)

Totals come from `simulations` joint draws. Each draw takes one bootstrap parameter set, draws every listing inside its interval, and sums (units, or revenue with price weights). The platform reports three things:
- **Floor:** the sum of the observation lower bounds. This is certain.
- **Estimate:** the sum of the listings' conditional means (M1.3), with a 95 % interval from the draws. Each group's draws (market, segment, product, brand) are rescaled so their mean equals that sum. The rescaling (and the interval inflation below) acts on each group's **excess over its floor**, never on the floor itself, so no draw, low or estimate can fall below what was certainly sold; groups whose listings all have finite ceilings are also held at or under the sum of their ceilings, and the other draws are rescaled to keep the mean exact. The factor is close to 1: it absorbs Monte Carlo noise, parameter averaging, and the mean lift of those clips for small groups. As a result, point estimates are deterministic and **add up exactly** from listing to product, product to segment, and segment to market, and every interval, share and rank computed from the draws is consistent with them. Shares are taken per draw over all members (segments of the market, brands of a segment), so they sum to exactly 1. On synthetic ground truth the two point estimates are equally accurate: median absolute error 2.0 % (sum of means) vs 1.9 % (draw mean) on 24 markets, with bias below 0.1 %. Market 95 % coverage stays at 100 % after anchoring.
- **Ceiling:** the sum of the upper bounds, where every listing is bounded.
- **Headline:** the market page leads with the floor (observed). The estimate and its interval are shown beside it with `model_validated`: the demand model must pass the hold-out thresholds in `integrity` (metrics.yaml) -- enough observed sales, AUC, and 80 %/95 % interval coverage -- otherwise the estimate is labelled *not validated* with the reasons. Units follow the same rule (`units_month_floor` is the headline, the modelled units beside it). Every other number computed from the model's draws -- concentration, entrant success, product and segment estimates, the recommended product's expected units, the analyst's size answer -- carries the same *model-based · not validated* label while the model fails.
- **Checked against the user's own sales:** once a Seller Central Business Report is loaded (N7), the integrity check `model_vs_own_sales` compares the model with it. It takes the user's listings present in the market export whose report period midpoint lies within `own_sales_max_days_apart` days of the snapshot, and measures how often the listing's 95 % interval holds the actual monthly units (it must reach `min_coverage_95`) and the median actual / estimate ratio. Fewer than `own_sales_min_listings` such listings give a warning, not a verdict; without own sales the check is skipped.

The aggregate interval width is multiplied by `interval_inflation` (1.3). That factor was **calibrated on synthetic ground truth**:
- 1.29 was needed for normal markets and 1.30 for heavy-tailed markets, on 80 tuning markets (linear price model).
- It was then verified on 80 different markets (§1.5).
- It was kept after the price spline was added: with the spline, market 95 % coverage is 100 % in every scenario except dead listings (16 markets each, §1.5), and listing coverage stays at 94–95 %, so there was no evidence for a different value.

### 1.5 Measured accuracy

**Synthetic ground truth** (`synthetic.py`). Markets have known true sales:
- sub-categories with different demand
- price elasticity −0.9, plus rating and age effects
- normal or heavy-tailed (Student-t 3) noise
- 10 % missing covariates

The real badge censoring is then applied. The results below are on 80 markets **not used for calibration** (seeds 101–180):

| Measure | Normal markets | Heavy-tailed markets | Target |
|---|---|---|---|
| Market size bias (mean relative error) | +1.5 % | +0.9 % | ≈ 0 |
| Median absolute market-size error | 1.8 % | 3.2 % | — |
| Market 95 % interval contains the truth | 97.5 % | 100 % | 93–97 % (conservative is acceptable) |
| Sub-category 95 % intervals contain the truth | 98.0 % | 98.0 % | 93–97 % |
| Listing 95 % intervals contain the truth | 94.6 % | 94.1 % | 93–97 % |
| Largest sub-category identified | 92.5 % | 95.0 % | — |
| Price elasticity recovered (true −0.9) | −0.894 (both families) | | — |
| *Old method: observed sales only* | *−41 %* | *−35 %* | — |

`tests/platform/test_metrics_demand.py` re-measures this on every build, with 16 markets per family, and **fails the build** if bias, coverage or sub-category identification regresses.

**Misspecified worlds (best case is not enough).** The markets above are drawn from exactly the model's functional form, so they measure the best case. `synthetic.SCENARIOS` adds worlds the model does not describe:

| Scenario | What differs from the model |
|---|---|
| specified | nothing (the model's own form) |
| heavy_tails | Student-t(3) noise |
| brand_effects | brand random effects, sd 0.7 log units, 30 brands with skewed sizes (P ∝ 1/rank); brand is not a covariate |
| curved_price | price response −0.9·(log p − log 25) − 0.6·(log p − log 25)² |
| curved_price_brands | both of the above |
| dead_listings | 30 % of listings sell nothing (zero-inflation) |
| brand_noise | 30 % of the noise variance shared within a brand (total variance unchanged) |
| reviews | a review-count column: Poisson(1.5 % of cumulative units × lognormal(0, 0.5)) |
| dead_listings_reviews | dead listings plus reviews (dead listings have no reviews) |

Measured on 16 markets per scenario (400 listings, seeds 101–116, 30 bootstrap refits, 300 draws), before and after the price spline:

| Scenario | Median \|error\| before → after | Bias before → after | Market 95 % coverage before → after | Listing 95 % coverage after | Top sub-category after |
|---|---|---|---|---|---|
| specified | 1.8 % → 1.5 % | +1.9 % → +1.7 % | 100 % → 100 % | 94.5 % | 93.8 % |
| heavy_tails | 3.4 % → 3.3 % | +0.4 % → +0.4 % | 100 % → 100 % | 94.9 % | 100 % |
| brand_effects | 1.6 % → 1.6 % | +0.1 % → +0.0 % | 100 % → 100 % | 95.0 % | 100 % |
| **curved_price** | **3.5 % → 1.9 %** | **+3.7 % → +1.1 %** | 100 % → 100 % | 93.8 % | 93.8 % |
| curved_price_brands | 1.5 % → 1.6 % | +2.1 % → +0.3 % | 100 % → 100 % | 94.8 % | 93.8 % |
| **dead_listings** | **8.7 % → 8.5 %** | **+9.0 % → +8.9 %** | **56 % → 62.5 %** | **66.7 %** | 100 % |
| brand_noise | 2.3 % → 2.3 % | +0.9 % → +0.7 % | 100 % → 100 % | 94.8 % | 100 % |

Price association recovered (true ≈ −0.9 on average over the listings): curved_price −0.79 → −0.91, specified −0.87 → −0.88.

Reading:
- The spline removes most of the curved-price bias (+3.7 % → +1.1 %) and does not hurt the correctly specified case (the differences there are within 16-market noise).
- Unmodelled brand effects and brand-correlated noise barely matter for market totals: the brand effects average out over 30 brands, and the aggregate intervals already carry enough width.
- **Dead listings are the model's blind spot.** With badge data a listing that sells nothing looks exactly like a slow seller (both are "below 50"), so the log-normal extrapolation below the first rung credits it with the sub-threshold sales of a slow seller. With 30 % dead listings the market size is over-stated by ~9 % and the 95 % interval missed the truth in 6 of 16 markets. The certain **floor** is unaffected; read the estimate as an upper-leaning value in markets with many listings that look abandoned. This cannot be fixed from badges alone; a second signal (review velocity, BSR, monthly history) is needed.
- **Best-sellers rank (BSR)** (12 synthetic markets per scenario; BSR = rank of a noisy daily sales reading in a category ~20x the export):

  | Scenario | Term | Median market error | Bias | Market 95 % coverage | Listing coverage | Hold-out AUC | Hold-out 80 % / 95 % |
  |---|---|---|---|---|---|---|---|
  | standard | none | 1.3 % | −1.3 % | 83 % | 94 % | 0.77 | 0.64 / 0.88 |
  | standard | `log_bsr` | 12.1 % | +13.7 % | 0 % | 68 % | 0.97 | 0.63 / 0.84 |
  | standard | `bsr_score` | 1.7 % | −0.1 % | 92 % | 84 % | 0.97 | 0.67 / 0.87 |
  | brand effects | none | 1.1 % | +0.4 % | 100 % | 95 % | 0.73 | 0.64 / 0.88 |
  | brand effects | `bsr_score` | 1.4 % | +0.4 % | 100 % | 84 % | 0.98 | 0.70 / 0.88 |
  | dead listings | none | 5.2 % | +6.6 % | 67 % | 66 % | 0.74 | 0.51 / 0.83 |
  | dead listings | `bsr_score` | 8.4 % | +9.9 % | 50 % | 56 % | 0.98 | 0.64 / 0.82 |

  - Log rank separates sellers almost perfectly, but it flattens for slow sellers, so a linear term extrapolated below the first badge overstates the market.
  - The normal score of the rank within the export (`bsr_score`) removes that bias and passes hold-out validation where the log-normal form holds.
  - `bsr_score` narrows listing intervals too far (84 % coverage for 95 % nominal) and does worse with many dead listings.
  - Both terms are therefore implemented and **off by default**. The `bsr` column is ingested (header match only: 小类BSR preferred over 大类BSR) and kept.

- **Review counts did not help** (with the spline in both fits): reviews 1.5 % → 3.1 % median error, bias +1.7 % → +3.9 %, market coverage 100 % → 81 %, listing coverage 94.5 % → 86.9 %, price association −0.88 → −0.22 (reviews absorb the price effect, since low-priced listings that sell more also collect more reviews). An extra zero-reviews indicator was also tried (3.1 %, +3.4 %, 87.5 % coverage) and was not better. With dead listings, reviews made it worse still (bias +11.4 %, coverage 12.5 %). `log_reviews` therefore stays off by default (§1.2).

The accuracy dashboard (`pilot/accuracy.py`) runs the scenarios in `accuracy.synthetic_scenarios` (all except the two review scenarios, which equal `specified` while the covariate is off) with `synthetic_markets` markets each. It reports every scenario and scores the pass/watch/fail checks on the **worst scenario per measure**, which today is dead_listings. `test_metrics_demand.py::test_accuracy_gate_misspecified_markets` gates curved_price and brand_effects on every build.

**Real reference data (cross-validation).** The badges are hidden and predicted from covariates only. This is harder than production, where a badged listing is already pinned to its rung:

| Market | Listings | Badged | AUC badged vs. unbadged | 95 % interval holds a hidden badged listing |
|---|---|---|---|---|
| implants | 279 | 79 | 0.88 | 92 % |
| denture_base | 104 | 30 | 0.67 | 90 % |
| dental_models | 428 | 37 | 0.75 | 68 % |

Reading:
- Price, rating, age and sub-category separate sellers from non-sellers well in implants. They separate them only moderately in the other two markets, and poorly place the few top sellers of dental_models.
- Per-listing estimates there are wide and should be read as ranges.
- Market totals remain bounded by the certain floor and ceiling.
- More covariates, such as BSR, review counts or monthly history, are the lever for accuracy (`ACCURACY_STRATEGY.md` §5).

**Where the model fails (calibration diagnostic, 29 Sep 2026).** The hold-out coverage above is scored on hidden *badged* listings only. Those listings were selected for selling above the first rung, so even a correct model covers them less often than nominal. On well-specified synthetic markets (specified and heavy-tailed scenarios) the badged-only 80 % coverage is 0.66–0.68, which is why `integrity.min_coverage_80` is 65 %. A second measure scores every hidden listing: the randomized probability integral transform, drawn uniformly between the predictive CDF at the listing's observed lower and upper bound. It is nominal for a calibrated model (synthetic: 0.80 / 0.95 for 80 % / 95 %).

| Market | All listings, 80 % / 95 % (randomized PIT) | Badged listings, 80 % (same draws) | Correct model, badged 80 % (synthetic) |
|---|---|---|---|
| dental_models | 0.82 / 0.93 | 0.22 | 0.66–0.68 |
| denture_base | 0.75 / 0.91 | 0.58 | |
| implants | 0.70 / 0.91 | 0.00 (2 badged) | |

On dental models the model is calibrated on the many listings that sell below the first badge, and badly under-predicts the few top sellers that carry most of the revenue. The validation check therefore keeps scoring badged listings. A pass on all listings would hide the failure that matters for market size.

### 1.6 Assumptions and limits (shown with every result)

- Units are log-normal or log-Student-t given the covariates. The price response may curve (spline); other covariates act linearly on log units.
- Listings are independent given the covariates; brand effects are not modelled (measured to be harmless for market totals, §1.5).
- A listing that sells nothing is indistinguishable from a slow seller in badge data; many such listings bias the estimate up (§1.5).
- A listing without a badge sells below the first rung. This holds only for badge data; exact data makes no such assumption.
- The price coefficient is a cross-sectional association, not a causal price elasticity.
- One snapshot measures a level, not a trend. Growth needs monthly snapshots (§2).


---

## 2. Competition structure (M2.1)

- **Brand shares.** Each brand's share is its fraction of *estimated* revenue. It is computed in every joint simulation draw, so each share has a 95 % interval.
- **HHI** = Σ share² × 10,000, computed per draw. Its intervals therefore carry the demand uncertainty.
- **Effective competitors** = 10,000 / HHI.
- **Top-brand and top-3 shares** are reported per draw, in the same way.
- **Why estimated revenue, not observed:**
  - Observed revenue exists only for badged listings.
  - Unbadged sellers' combined weight is invisible there, so concentration comes out biased upward.
- **Scopes:** market (all listings), segment (the segment's listings) and brand (market-wide share).

## 3. Entry analysis (M3.1, M3.2)

- **Entrant:** a listing launched within `entrant_window_days` (365) before the snapshot date.
  - When no snapshot date is declared, the latest launch date in the data is used, and the basis is stated.
- **M3.1 Entrant revenue share:** entrants' estimated revenue ÷ the segment's, per draw.
- **M3.2 Entrant success rate:** the share of entrants whose estimated units reach the median estimated units of the segment's *established* listings.
  - It is computed per draw, giving an interval.
  - With fewer than `min_entrants` entrants, it is shrunk toward the market's rate with `prior_strength` pseudo-entrants. This is empirical-Bayes shrinkage.
  - With no entrants, the market rate is shown, and labelled as such.
- **Without launch dates** (e.g. a non-date launch column), entry metrics are *not available*. The opportunity index then shows reduced evidence coverage; it does not guess.

## 4. Unit economics (M4.1)

`unit margin = price × (1 − referral_fee) − fulfilment fee − unit cost`, per listing.

- Fee and cost fields come from `metrics.yaml`.
- **Quarantine:** a cost ≤ 0 or ≥ price is invalid. It is quarantined, and its reason is shown on the listing.
- **Derived-field detection:** when `(cost + fulfilment fee) / price` is nearly constant (coefficient of variation < `derived_cost_max_cv`, n ≥ 20), the "cost" is **computed from price**. It is a target cost ceiling, not an observed cost.
  - Using it would make every margin identical, which is circular.
  - It is kept as `cost_ceiling`, the maximum affordable unit cost for the company's target margin, which is useful for sourcing. It is not used for margins.
  - Both reference exports with a cost column (`保底生产成本`) are of this kind: (cost + FBA) / price = 0.53 ± 0.02.
- **Reported per segment:** the median margin rate, the median unit margin, and the share of profitable listings. Genuine unit costs, whether in the data or entered in the Launch Simulator, switch these on.

## 5. Quality gap (M5.1, M5.2)

- **M5.1 Quality gap:** the share of the segment's *estimated units* sold by listings rated below `quality.threshold` (4.0).
  - This is demand currently served by poorly rated products, which a better product can win.
  - It is a rating-based proxy. When review text exists, review-theme pain (`customer_pain`) is shown alongside it.
- **M5.2 Rating bar:** the median rating of the top `rating_bar_top_share` (10 %) of listings by estimated revenue. A new product must match it.

## 6. Saturation (M6.1)

- **Unbadged share:** the share of the segment's listings selling below the first badge rung.
  - With badge data, a high share means most listings barely sell, i.e. a crowded long tail.
- **Listings per 1,000 estimated units:** supply relative to demand.

## 7. Opportunity (M7.1, M7.2)

### 7.1 Expected entrant value (primary)

The question it answers: *what does a new listing in this segment realistically earn per month?*

1. **Entrant units:** the estimated units of the segment's entrants, drawn jointly.
   - With fewer than `min_entrants`, the market's entrants are pooled in.
   - With no entrants at all, the lower quartile of established listings is used, and stated.
2. **Value:**
   - **Profit** = entrant units × unit margins sampled from the segment's listings, when genuine costs exist.
   - Otherwise **revenue** = entrant units × prices of the segment's listings. The row says which one was used.
3. **Reported:** the mean with a 95 % interval, and the probability of reaching `target_monthly_profit` (500 USD) or `target_monthly_revenue` (5,000 USD).

### 7.2 Opportunity index (secondary, 0–100)

The index is a weighted **geometric** mean of absolute component scores in [0, 1]:

| Component | Metric | Transform (config) | Weight | Reasoning |
|---|---|---|---|---|
| demand | segment estimated revenue | log10: $1k → 0, $1M/month → 1 | 0.25 | a market must exist |
| entry | entrant success rate (M3.2) | 0 → 0, 60 % → 1 | 0.25 | newcomers can win here |
| margin | median margin rate (M4.1) | 0 → 0, 40 % → 1 | 0.20 | winning pays |
| competition | HHI (M2.1) | 5,000 → 0, 1,000 → 1 | 0.15 | demand is not locked by one brand |
| quality gap | M5.1 | 0 → 0.5 (neutral), 40 % → 1 | 0.10 | upside only: no gap is not a weakness |
| saturation | unbadged share (M6.1) | 95 % → 0, 50 % → 1 | 0.05 | fewer dead listings |

**Why this form:**
- **Geometric mean:** a segment with no demand or no winnable entry cannot be rescued by a good margin. The mean is non-compensatory.
- **Absolute transforms:** the index means the same thing in every market, which ranks within a market did not.
- **Missing components count as neutral.** A component the source cannot measure (margin without unit costs, entry without launch dates) enters the geometric mean as `missing_component_score` (0.5), and the weight **coverage** (share of the weight actually measured) is reported next to the index. Before, missing components were left out, so a segment was scored on fewer components and ranked beside fully scored ones: a segment with only strong components measured looked better than one where a weak component was measured. With a fixed neutral value every index is on the same scale; a segment is neither rewarded nor punished for evidence the source lacks, and a missing component pulls an index toward the middle. When no component is measured there is no index. The product index (§7.3) uses the same rule. On the reference exports the within-market ranking was unchanged (every segment of a market has the same coverage: dental_models 80 %, denture_base 55 %); low indices moved toward the middle (e.g. denture_base's lowest segment 8.4 → 18.8), which makes the two markets comparable.
- **Levels:** Low < 40 ≤ Moderate < 55 ≤ High < 70 ≤ Very high. Dashboards count "High and above" (`high_from`).
- The transforms and weights are judgment, documented and configurable. They are calibrated against outcomes once launched products are tracked (Phase 8). The planted-answer tests check the direction: an open segment must beat a locked one.

### 7.3 Product opportunity (M7.3)

A product's index is the weighted geometric mean of three things:
- its segment's index (0.7)
- its **price-band position** (0.2): demand per listing in its price band ÷ the segment's, log2, −1..+1 mapped to 0..1
- its **quality room** (0.1): the segment rating bar minus the product's rating, 0..1 star

It describes the opportunity of *making a product like this one*. It is not "this product sells well".

## 8. Evidence grade

| Grade | Listings | Badged share | Cross-validation AUC |
|---|---|---|---|
| A | ≥ 50 | ≥ 25 % | ≥ 0.75 |
| B | ≥ 20 | ≥ 10 % | ≥ 0.65 |
| C | ≥ 10 | ≥ 5 % | — |
| D | otherwise | | |

A measured cross-validation AUC below `evidence_grade.min_auc_any` (0.60, the same level as `accuracy.auc_watch`) caps any grade at **D**: the demand model then ranks sellers no better than a weak guess on held-out listings, whatever the listing count and badged share. The market summary records why in `evidence_grade_reason` (e.g. "capped at D: hold-out AUC 0.46 is below 0.60; …").

The grade is shown with every market and segment. On the reference data after the category boundary (§9):
- dental_models **C**: 9 % badged
- denture_base **C**: 29 % badged, cross-validation AUC below 0.65
- implants **D**: of the ~105 in-scope listings only 2 % carry a badge

The implants export was built from a keyword search. Its revenue came from water flossers, which are out of scope.


## 9. Category boundary and segmentation (M9.1, M9.2)

### 9.1 Category boundary (M9.1)

Each marketplace sub-category (`category`, e.g. 小类目) in the export is scored against the market's definition in `config/categories.yaml`:

- **Include terms:** the definition text before "Excludes".
- **Exclude terms:** the text after it. "Boundary cases" onwards is neutral.
- **No evidence:** terms that appear in both lists, generic domain words, and words found in most sub-category names.

**Evidence** = `name_weight` × (name hits) + the share of the sub-category's titles containing the terms.

| Decision | When |
|---|---|
| **out** | exclude evidence ≥ include evidence + `out_margin`, **and** the titles agree (exclude-term title share strictly higher), **and** the sub-category holds ≤ `max_auto_exclude_share` (25 %) of the listings |
| **in** | include evidence ≥ exclude evidence + `in_margin` |
| **review** | anything else, including contradictions and large sub-categories. Kept in and flagged. |

Further rules:
- Out-of-scope listings are marked `out_of_scope` with the reason. They are never deleted.
- A person's decision (Scope panel, `POST /markets/{m}/scope`) overrides the classifier on every run.
- A person's relevance label on a listing is never overridden.
- Markets without a definition keep all sub-categories and report their composition.

Why the safeguards exist:
- dental_models' main sub-category (277 listings) has "educational" in its name, which is an excluded term. Its titles, however, match the include terms more (71 % vs 46 %), so it goes to review instead of being excluded.
- For implants the boundary removes 174 of 279 listings: floss, flossers, brushes, toothpicks and cold packs. This agrees with the definition's own exclusion list.

### 9.2 Segmentation (M9.2)

- **Family** = the marketplace sub-category. **Segment** = the sub-category itself, unless a split is accepted.
- **When a split is considered:** the sub-category has ≥ `split_min_listings` (12) listings. The existing text/spec discovery is then run inside it.
- **Small clusters** (< 5 % of the sub-category, clipped to 3..10 listings) are merged into their most similar sibling by title TF-IDF centroid.
- **The split is kept only if the clusters are distinct:** title TF-IDF cosine silhouette ≥ `min_split_silhouette` (0.12).
  - Measured on the reference data, variants of one product type scored 0.03–0.08 (elevators/periotomes, instrument cassettes).
  - Distinct product types scored 0.16–0.58 (teaching models vs. implant models vs. typodonts vs. phantom heads; floss types; repair-kit types).
- **Keywords:** class-based TF-IDF over uni- and bi-grams of all segments. Removed first: English stop words, the generic stop-list (config), brand names, the segment's own sub-category words, tokens containing digits, and terms shared by most sibling segments.
- **Labels:** "sub-category · two distinctive keywords" for split segments; the sub-category name otherwise.
- **Stable ids** (`taxonomy.stabilize`). Segments are rediscovered on every run and split clusters are numbered in discovery order, so the same segment could come back under another id. After discovery each new segment inherits the previous run's id of the same sub-category when it holds the majority of that previous segment's still-present listings; each previous id is used at most once (on a split, the larger part keeps it). A segment with no predecessor gets an id the previous run never used, so an old id never returns with a different meaning. Clustering is unchanged; the counts are reported in the discovery stage (`stable_ids`). This mirrors product ids (`product_resolution/stable_ids.py`).
- **Descriptions** (English and Chinese) are generated from computed statistics only: listings, price range, estimated revenue with interval, concentration, entrants and keywords.
- Sources without a sub-category fall back to pure text discovery.


## 10. Gap analysis and recommended product (M10.1, M10.2)

### 10.1 Gaps (M10.1)

**Candidate features.** For every segment with ≥ `min_segment_listings`:
- recurring title terms (uni- and bi-grams, without stop words, brands or the sub-category's own words)
- bucketed specs (rpm, watt, pack, ml, gram)
- price bands

A feature needs ≥ `min_support` (5) listings with it *and* without it. Features present on exactly the same listings are one signal; they are merged and reported as aliases.

**Per feature:**
- **Supply share** = listings with the feature ÷ listings.
- **Demand share** = their estimated units ÷ the segment's.
- **Lift** = estimated units per listing with ÷ without, with a 95 % listing-bootstrap interval (`bootstrap` resamples).
- **p-value** (one-sided, lift > 1): a normal approximation of the bootstrap log-lift distribution.
  - Counting resamples cannot resolve p below 1/B, which makes false-discovery control over many features impossible.
  - Zero bootstrap variance gives p = 1.
- **Multiple testing:** Benjamini–Hochberg q-values over *all* features tested in the market.

A feature is a **gap** when all three hold:
- q ≤ `fdr_q` (0.10)
- the lift's lower bound > 1
- demand share > supply share: buyers reward it more than sellers supply it

Caveat: for unbadged listings the estimated units come from the demand model (price, rating, age, sub-category), so the evidence for a *term* comes mainly from badged listings. Markets with very few badges (e.g. implants in scope) correctly show no gaps.

### 10.2 Recommended spec (M10.2)

For each analysed segment:
1. **Features:** up to `max_features` gap features, strongest lower bound first, skipping overlapping n-grams. They must co-occur on ≥ `min_support` listings.
2. **Price band:** the band with the highest estimated units per listing, restricted to bands with positive median unit margin when margins are known.
3. **Expected units:** the mean of the listings that already have all recommended features, with a bootstrap interval. These listings are also returned as **comparables**.
4. **No significant gap:** the recommendation is the best price band, and says so.

The market's headline recommendation is the segment with the highest opportunity index (§7) that has a recommendation.

**Reference data:**
- **dental_models:** "teaching" models in the teaching/demonstration segment. Lift 3.0×, 95 % CI 1.4–5.1, q = 0.097. They are 72 % of listings but 89 % of demand.
- **implants:** none. There is a single badge in scope.

## 11. Competitor intelligence (M11.1–M11.4)

Source: `src/dip/metrics/brands.py`. Thresholds: `brands` in `config/platform/metrics.yaml`.

### 11.1 Brand shares, ranks and positions (M11.1)

- **Draws:** the joint demand simulation (§1, 400 draws) gives every brand's monthly revenue in every draw.
- **Share:** brand revenue ÷ market revenue, per draw. The point estimate is the mean over draws; the interval is the 2.5–97.5 % quantiles.
- **Rank interval** and **P(#1)**: the brand's rank in each draw. P(#1) is the share of draws in which it ranks first.
- **HHI** and **effective competitors** (10,000 ÷ HHI) are computed per draw, so they have intervals too.
- **Position** is read from the share interval against the **equal-share benchmark**, 1 ÷ effective competitors:
  - leader: P(#1) ≥ `leader_p_top` (0.5)
  - above par: the whole interval is above the benchmark
  - below par: the whole interval is below the benchmark
  - par: otherwise, meaning the data cannot tell it apart from an average brand

### 11.2 Price index and Bayesian rating (M11.2)

- **Price index:** the median, over the brand's listings, of listing price ÷ its segment's median price.
- **Rating:** a Bayesian average. `(C·m + Σ wᵢ·rᵢ) ÷ (C + Σ wᵢ)`, where:
  - wᵢ is the review count when the source has one, otherwise 1 per listing
  - m is the market's weighted mean rating
  - C is the median weight of a rated listing, so the prior is data-driven
  - A brand with little evidence is pulled toward the market mean.
- **Signals** are rule-coded from these values, with thresholds in config. Each one carries its value:
  - premium price with a below-market rating, meaning an opening for a better-value alternative
  - low rating, meaning an opening for a better-quality alternative
  - no launches in the entrant window
  - revenue driven by entrants
  - value pricing
  - high rating

### 11.3 Launch cohorts and momentum (M11.3)

- **Cohorts:** listings grouped by launch quarter, with their estimated revenue today.
- **Momentum:** a = launches in the last `entrant_window_days`, b = launches in the window before.
  - Under equal launch rates, a | a+b ~ Binomial(a+b, ½). The p-value comes from the exact two-sided test.
  - The test runs for the market and for each segment with at least `min_launches_for_test` launches.
  - Segments are adjusted with Benjamini–Hochberg (q ≤ 0.10).
- **Caveat:** a snapshot only shows listings that are still live, so older cohorts are survivors. Momentum measures surviving launches.
- **Reference data:** dental_models has 63 launches in the last year against 67 the year before (p = 0.79), which is no significant change. denture_base has no launch dates, so momentum is not measurable.

### 11.4 Share changes between snapshots (M11.4)

- Brand shares are stored per snapshot period (`brand_history`).
- For the two latest periods: z = (s₂ − s₁) ÷ √(se₁² + se₂²), where se is the interval width ÷ (2 × 1.96).
- Two-sided p-values are Benjamini–Hochberg adjusted over brands. A change is reported as significant at q ≤ 0.10. Anything else is "within noise".
- With one snapshot, the page says two are needed rather than showing a trend.

## 12. Launch simulator (M12.1–M12.3)

Source: `src/dip/metrics/launch.py`. Settings: `launch` in `config/platform/metrics.yaml`.

### 12.1 Predictive demand for a new listing (M12.1)

- **Model card.** When a market is processed, its fitted demand model (§1) is stored as `demand_model` in the lake. The card holds:
  - the design: covariates, standardisation and sub-category levels
  - the coefficients, σ and the error family
  - the bootstrap draws
- **The hypothetical listing** has:
  - the idea's price
  - the segment's sub-category
  - an assumed rating: the given one, otherwise the segment median (Opportunity Board concepts: the segment's rating bar, §17)
  - when the model uses review counts (`log_reviews`, off by default): the median reviews of the segment's entrants, else of the market's entrants
  - an age of `default_age_days` (180)
- **Each draw** proceeds in four steps:
  1. Pick a bootstrap parameter set.
  2. Compute log units = xβ + segment offset + σ·ε, with ε drawn from the fitted family.
  3. Redraw any value above the top rung's upper bound. The model's support ends there.
  4. Exponentiate.
- **Segment offset.** Segments within one sub-category differ. The offset is the segment's mean log-residual relative to the market, shrunk with `segment_offset_prior` = 5 pseudo-listings (empirical Bayes).
- **Calibration** on synthetic markets: fit on an observed market, then predict fresh listings from the same process.
  - The p10–p90 interval covers 77–84 % of true units with normal errors, and 74–75 % with heavy-tailed t(3) errors.
  - The predictive median is within ±17 % of the true median.
  - This is tested in CI.
- **Headline:** the median units, not the mean. The mean of a heavy-tailed distribution is unstable.

### 12.2 Economics (M12.2)

- **Per draw:**
  - profit = units × unit margin − fixed monthly cost
  - unit margin = price × (1 − referral fee) − fulfilment fee − unit cost
- **Unit cost** is the user's. A source cost field is used only when the engine found it genuine (§4). A price-derived "cost" is a ceiling and is never used as a cost. Without a cost, only revenue is simulated.
- **Fulfilment fee** comes from the user, otherwise the segment median of the source's fee field.
- **Outputs:**
  - P(profit > 0) and P(profit ≥ target)
  - break-even units = fixed cost ÷ unit margin
  - P(units ≥ break-even)
  - payback = launch cost ÷ expected monthly profit

### 12.3 Price curve, scenarios, checks (M12.3)

- **Price curve.** The same listing is simulated over a grid inside the segment's observed prices (p5–p95), using one shared noise draw per simulation. Each point is the expected value, with a p10–p90 band.
  - The price effect is the market's cross-sectional association, not a causal elasticity.
  - With the price spline (§1.2) the curve may bend. `price_association` then reports the **average elasticity** (the mean over the market's listings of d log units / d log price, with a bootstrap 95 % interval) and the **local elasticity** at the 25th / 50th / 75th price percentiles; `form` says whether the price term is linear or a spline.
  - An optimum at the edge of the grid is flagged.
- **Scenarios.** They run on the same segment with the same random numbers, which is why the table can report:
  - P(best): the share of draws in which a scenario is best
  - P(beats the first scenario)
- **Empirical check.** The segment's actual entrants' units (median and IQR) are shown next to the prediction. A prediction outside their p10–p90 raises the `model_vs_entrants` risk.
- **Gaps covered.** The idea's words, specs and price band are extracted with the same functions as §10 and matched against the segment's significant gaps.
- **Risks** are rule-coded from computed values, and each carries its value. The rules are:
  - HHI ≥ 2,500
  - entrant success below 20 %
  - rating below the rating bar: medium when the user gave the rating; when the rating was assumed (segment median) it is only a low-severity reminder (`assumed: true`), since an assumption is not a plan
  - similarity ≥ 0.8 to an existing listing (low differentiation), or below 0.08 (unreliable placement)
  - price outside the segment's 2.5–97.5 % range
  - negative margin
  - evidence grade C or D
- **Reference data (dental_models).** A $24.99 teaching model at a unit cost of $6, a fixed cost of $150 and a fulfilment fee of $4.35 (the segment median of 95 listings) gives:
  - median 6.3 units a month, p10–p90 0.8–37
  - the segment's 14 entrants sell a median of 9.1 (IQR 5–11)
  - it covers the "teaching" gap
  - P(profit > 0) = 28 %

## 13. Shopping mode (M13.1)

Source: `src/dip/metrics/shopping.py`. Settings: `shopping` in `config/platform/metrics.yaml`.

**Candidates.** A product is a candidate when:
- its title relevance (the maximum of word and character TF-IDF cosine) is at least `relevance_min`, and at least `relevance_floor` × the best match
- it is inside the budget and meets the minimum rating (hard filters)

Every exclusion is counted and reported.

**Criteria.** Each one is computed, never hand-scored:
- **Match:** need coverage, meaning the share of the buyer's distinguishing words present in the title. A word counts only when some candidates have it and others don't. Relevance breaks ties.
- **Quality:** a Bayesian-average rating, as in §11.2. A 5.0 on 3 reviews does not beat a 4.8 on 3,000.
- **Proof:** the lower 95 % bound of estimated monthly units. This is demand we are confident exists.
- **Price:** lower is better.

**Pareto fronts.** Product *i* dominates *j* when it is ≥ on every criterion and > on one. Non-dominated sorting assigns fronts, and each dominated product names one dominator.

**Ranking.** Products are ordered by front, then by utility. Utility is the weighted mean of the criteria's percentiles among the candidates, with weights from the profile presets (beginner, professional, budget, balanced) or the buyer's sliders. A dominated product therefore never outranks its dominator. This property is tested.

**Explanations.** A criterion is reported as a strength when the product is in the top quartile of candidates, and as a trade-off when it is in the bottom quartile. When the Bayesian adjustment changes the shown rating, the page says so.

## 14. Knowledge graph (M14.1)

Source: `src/dip/intelligence/ontology.py`. The graph is a typed ontology.

**Structure:** Industry → Category (market) → ProductFamily (marketplace sub-category) → Segment → Product → Listing.

**Around it:**
- Brand
- Seller
- Feature: a statistically significant demand gap (§10)
- Recommendation
- Momentum: significant launch momentum (§11.3)
- Trend: the trend detector's label
- Supplier and Country
- CustomerProblem: from review text, when present

**Every edge carries its evidence.** Each edge records:
- `evidence`: one readable line
- `metric`, `value`, `low`, `high`: the number and its 95 % interval
- `n`: the sample size
- `strength`, from 0 to 1: comparable within an edge type
- `basis`, one of:
  - **fact:** a source field
  - **estimate:** the demand model
  - **test:** a statistical test
  - **similarity:** an embedding or text match

**What `strength` means per edge type:**

| Edge | Strength |
|---|---|
| containment (HAS_FAMILY, HAS_SEGMENT, HAS_MODEL, BELONGS_TO) | share of the parent's estimated revenue |
| BRAND_SHARE (segment → brand) | brand share from the joint simulation; the evidence also shows the interval and P(#1) |
| HAS_GAP | 1 − q |
| HAS_MOMENTUM | 1 − q |
| PRODUCT_SIMILAR_TO | cosine similarity |
| COMPETES_WITH | 1 − relative price gap |
| HAS_TREND | the detector's confidence, so a label resting on one snapshot is visibly weak |

**Edges that are never drawn:**
- a gap edge for a feature that is not significant
- a momentum edge without a significant test
- a supplier edge below the match threshold

**Path explanation** (`/graph/path`) takes the shortest undirected path between two nodes. Edge weights are 1 + (1 − strength), so among equally short paths the one with the strongest evidence wins. Every hop is returned with its evidence.

**Why** (`/graph/why`) returns every direct edge between two nodes.

**Robustness:** a node or edge property can never shadow the store's structural attributes (id, kind, label, market, rel). A property with one of those names is renamed `prop_<name>`.

## 15. Growth and forecast (M15.1)

Code: `src/dip/metrics/forecast.py`. Settings: `forecast` in `config/platform/metrics.yaml`. Endpoint: `/markets/{m}/forecast-v3`.

**Input.** Each upload writes one row per snapshot period to the `revenue_history` table: the v3 monthly revenue estimate with its 95% interval, for the market and for every sub-category. The period is the declared snapshot date, or else the latest data month. A re-upload of the same period replaces that row; it is not counted twice.

**Model: a random-effects log-linear trend.**

    log y_i = a + b·t_i + u_i + e_i
    e_i ~ N(0, s_i²)   measurement error; s_i = (log hi_i − log lo_i) / (2 · 1.96), floored at min_measurement_sd
    u_i ~ N(0, τ²)     real month-to-month variation beyond the trend

- **τ²** is estimated by the method of moments (DerSimonian–Laird, generalised to regression) and floored at 0.
- **a, b** are fitted by weighted least squares with weights 1 / (s_i² + τ²).
- **Growth per month** is exp(b) − 1, with a t interval on n − 2 degrees of freedom. The table also shows the 12-month change at that rate.
- **Trend test:** a two-sided t test of b = 0. `direction` is growing or declining only when p ≤ `alpha`; otherwise it is "no significant trend".
- **Forecast h months ahead:** the estimate is exp(a + b·(t_last + h)). The 95% prediction interval uses the variance of the fitted line plus τ², with t quantiles. It covers the true level of that month, not just the line.

**Honesty rules.**
- With fewer than `min_periods` (3) snapshots nothing is fitted. The page says how many snapshots exist and shows entry momentum instead.
- A horizon longer than the observed history is flagged `beyond_span` (⚠). It assumes the trend continues, which the data cannot confirm.
- Seasonality is not modelled until 24 monthly snapshots exist.
- Segments are matched across snapshots by their **stable id** (§9.2), so a segment whose generated label drifts keeps its history. History rows whose id does not exist in the latest snapshot (written before ids were stable, or a segment that was split or dissolved) fall back to the label: they join the latest segment with the same label when exactly one exists. Each segment gets its own fit.

**Validation** (`tests/platform/test_metrics_engine.py::test_forecast_*`). On simulated series with known growth, measurement noise and real variation, the growth interval covered the truth 96–100% of the time and the 3-month forecast interval 96–100% (nominal 95%). Coverage was checked across n = 4…12 snapshots and τ = 0…0.15.

## 16. Inside a sub-category (M16.1)

Code: `src/dip/metrics/bands.py` and the segment explain endpoint (`/markets/{m}/segments/{id}/explain`). Settings: `segment_detail` in `config/platform/metrics.yaml`.

**Brands.** These are the top five brands of the sub-category by share of its estimated revenue. They come from the same joint simulation as the market shares (M11.1): the mean share per brand, its 95% interval, and P(#1), the share of draws in which the brand ranks first. Bounds are clamped so the estimate always lies inside its interval; with exact data the interval collapses to the point.

**Demand per listing by price band.** The engine's price bands are quantiles of price within the sub-category. For each band the endpoint shows:
- the number of listings, and the price range and median
- the mean estimated units per listing, with a 95% percentile-bootstrap interval over listings (2,000 resamples, fixed seed)
- the band's share of the sub-category's estimated revenue, with its bootstrap interval

The interval measures the sampling variation of "a listing in this band". Each listing's own model interval is on the listings table.

**Launch momentum.** The number of launches in the last year is compared with the year before (M11.3). An exact binomial test gives a p-value, which is BH-adjusted across sub-categories. The result is "accelerating" or "slowing" only when q ≤ `brands.fdr_q`.

**Product position (M16.2).** The product page places a product among the products of its own sub-category. For price, estimated units, estimated revenue, rating and reviews it shows:
- the mid-rank percentile: the share of products below the product, with ties counted half
- the sub-category median
- the product's share of the sub-category's estimated revenue

A scatter plot draws price against estimated units for up to `segment_detail.max_peers_plotted` peers (the largest by revenue), with the product highlighted. "Simulate a launch like this" opens the launch simulator with the title, price, market and sub-category filled in. The unit cost is left for the user to enter.

## 17. Opportunity board (M17.1)

Code: `src/dip/metrics/board.py`. Endpoint: `/opportunities-v3`. Settings: `board` in `config/platform/metrics.yaml`.

**One row per concept.** Each row is the product concept of one of a market's top `max_per_market` sub-categories, ranked by opportunity index (M3). The concept depends on the sub-category's size:
- **Large enough for gap analysis:** the gaps engine's recommendation (M10.2), meaning its statistically significant features, priced at the geometric middle of the recommended price range.
- **Too small:** the sub-category's typical product at its median price. The row says so in `basis`.

**What the row shows.** The row joins numbers that are already computed elsewhere. Nothing is re-scored.
- a launch simulation of the concept (M12.1) as a **competitive entrant: at the sub-category's rating bar** (M5.2, the median rating of its top sellers), with the rating and its basis stated. Simulating at the median listing's rating put every concept below the rating bar by construction, so the below-rating-bar risk fired on almost every row (6 of 8 dental_models concepts and 3 of 8 denture_base concepts on the reference exports; 0 of 16 after the change). A rating the user enters in the Launch Simulator is always used as given. It gives units and revenue (median and 80% range), P(revenue ≥ target), P(profit > 0) only with genuine unit costs, and the simulator's risks.
- the sub-category's growth per month with its 95% interval (M15.1), or how many snapshots exist so far
- the leading brand's share interval and P(#1) (M16.1), HHI, entrant success, and launch momentum

**The explainable score.** Every row also carries the knowledge layer's opportunity for the same segment (`engine`: score, evidence coverage, status, risks, numbered reasons; §18 K7). A segment either engine ranks in its top `max_per_market` is on the board. The page sorts by this score by default and shows the older index next to it.

**Ordering and caching.** The API returns rows ordered by opportunity index, and every column can be sorted. Results are cached per market until it is re-processed.

## 18. Product knowledge layer (K1–K8)

Settings: `config/platform/knowledge.yaml`. Code: `src/dip/knowledge/`. Stage names in the job log: `identity`,
`knowledge`, `taxonomy_discovery`, `capacity`, `opportunity_engine`.

**K1 Value kinds and provenance.** Every value the platform shows is an observation with a kind:
*observed* (read from the source as-is: a listed price, a rating), *estimated* (an external estimate taken
as-is — SellerSprite sales and revenue are estimates, not observed counts), *modeled* (the demand model, with
a 95 % interval) or *derived* (a median, sum or count). Observations are appended per upload
(`observation_history`), never overwritten. Evidence records keep the text behind every judgment.
`GET /markets/{m}/why?entity_type=&entity_id=` returns both.

**K2 Dental Confidence (0–100).** `100 × Σ wᵢcᵢ / Σ wᵢ` over the components that have evidence:
text 0.30 (dental vs non-dental vocabulary in title / sub-category / description, averaged with the trained
relevance model), application 0.25 (the dental application classes named), image 0.20 (readable words in the
image reference), specification 0.15 (dental-schema attributes found), industry 0.10 (brand in an offline
registry). A component without evidence is dropped — missing evidence never counts as non-dental — and the
score is capped at 80 with one component, 90 with two. Bands: strong ≥ 85, probable ≥ 70, review ≥ 45,
probably non-dental ≥ 20, non-dental. A person's relevance decision overrides the band.

**K3 Attributes.** Schema per category (generic + category). Numbers are converted to canonical units
(`50k RPM` → 50,000 rpm; `0.028 N·m` → 2.8 N·cm) and the largest plausible value is kept (spec sheets state
maxima); a pack size is the first count stated. Enums report every value found; two different values are a
conflict for review, never resolved silently. Every value carries its confidence, field and text span.
Component role: accessory (replacement / compatible-with cues), bundle (bundle / combo / with-free cues —
“A + B” is a configuration, not a bundle), configuration (a component set such as control unit + handpiece,
or per-category configuration attributes such as implant count and tooth count), else base.

**K4 Identity.** After resolution, a product whose listings name different configurations is split; listings
without a configuration join the majority and are flagged. People’s merge / keep-separate decisions are applied
on the next run. Identity confidence = mean of the measured agreement components (model number, specification,
image, pairwise match score, brand). Duplicate candidates are the pairs within the review band of the match
threshold. Resolver precision and recall are computed against people’s decisions (merge / keep separate).

**K5 Taxonomy discovery.** A commercial attribute becomes a taxonomy level only if it covers ≥ 30 % of products,
forms ≥ 2 groups of ≥ 3 products, and separates price: η² of log price ≥ 0.05 with a permutation p ≤ 0.10
(F-test above 2,000 products). Numeric attributes: the split into 2–4 groups (each ≥ 3 products) with the largest
η², boundaries at midpoints rounded to two significant figures. Ranking: η² × coverage² (a level should classify
most products), preferring fewer groups when within 10 %. Greedy tree to depth 3. Nodes are machine-proposed
until approved, rejected or renamed.

**K6 Capacity.** Per category, segment and taxonomy node, aggregated over canonical products (one product sold
through 20 listings counts once). Modeled units / revenue intervals are combined as the square root of summed
squared half-widths (independent products — stated). The source’s own revenue is summed separately (kind
estimated). Offline-adjusted revenue stays empty until offline evidence exists. Best listing = highest valid
monthly unit-sales metric (source sales, else modeled units); review count is not a criterion.

**K7 Opportunity.** Nine dimensions on 0–100 scales (config): demand (log modeled revenue), offline strength,
growth (trend engine only), customer pain (rating-based proxy until review text exists), competition gap (HHI),
pricing / margin (genuine unit cost only), supplier availability, entry ease (segment entrants only — a borrowed
market rate is not segment evidence), data confidence. Score = weighted mean over **measured** dimensions;
`evidence_coverage` = measured weight share. Below 50 % coverage or 3 products: *insufficient evidence*, no rank.
Patterns A–E fire only when every dimension they need was measured. Risks: regulatory (FDA-regulated application
classes), hazmat terms, concentration, low demand, low or unknown margin, low data confidence, offline
unobserved, few suppliers.

**K8 Import preview.** The pipeline’s own detector proposes a column for every field with a confidence
(confident ≥ 0.85, confirm ≥ 0.6, else uncertain) and alternatives; quality checks report missing shares and
invalid values per field. The column profile ignores the lowest / highest max(1, 2 %) values when there are
20+ values, so one bad value cannot hide a column.

**K9 Landed cost.** Per listing: referral fee = price × `referral_fee`; fulfilment fee = the export's FBA fee
(observed); freight = package weight (observed) × `freight_usd_per_kg` (assumed). Fee headroom = (price − referral
− FBA − freight) / price. Max FOB = (price × (1 − target margin) − referral − FBA − freight) / (1 + duty), the
highest factory price that still leaves the target margin. It is a sourcing ceiling, not a measured cost. It is
reported before duty until `duty_rate` is set. A listing without an observed FBA fee or weight gets no headroom.
A scope needs `min_listings` such listings. When no genuine unit cost exists, fee headroom stands in for the
pricing / margin dimension (scale `pricing_margin_fallback`), stated in the evidence matrix. A genuine margin
always wins. Headroom below `thin_fee_headroom` is a high-severity risk.

**K10 Gates.** Each risk code can be `flag` (default: shown with the row) or `block` in `opportunity.gates`. A
blocked scope has status `gated`, no score and no rank. Its raw score is kept, and the first reason names the
gate. Hazmat terms match whole words and ignore negations ("alcohol-free").

**K11 LLM attribute tier.** Only rows the rules could not read (`needs_llm`), up to `max_rows_per_run` and
`budget_usd_per_run`. The prompt lists the schema's allowed values and units. A proposed value is kept only when:
- the attribute exists in the schema;
- the value is allowed or plausible;
- its evidence occurs in the listing text.

Everything else is rejected and traced. Accepted values fill only attributes the rules left empty
(`source: llm`, confidence `llm_confidence`, below the rule tier). Every call is an AI trace with model, prompt
version, record id, input hash, output, tokens and cost. A prompt already answered is reused from its trace.

**K12 Review pain.** When review text is uploaded with a dataset, the review stage computes each segment's and
product's pain report (complaint share of the top aspects and negative sentiment). A segment uses its own
report. A taxonomy node uses the review-weighted mean of its products' reports. That score replaces the
rating-based proxy for the customer-pain dimension (scale `customer_pain_reviews`), and the evidence matrix
names the review count. The reasons list the three most frequent complaints with their share of reviews.

**K13 Offline evidence.** People load offline evidence as a table: exhibitions, distributor catalogs,
manufacturers, trade data, clinic surveys. A row is rejected, with its row number and reason, when:
- its source type is unknown;
- its source name is missing;
- its value is negative or not a number.

A scope's offline evidence score (0–100, capped) = Σ over matching rows of `source_weights[type]` × (1 + log10(1 +
value)), with category-wide rows (empty `applies_to`) counted at `category_factor`. A row matches a scope when
its `applies_to` occurs in the scope's label or applications. A scope without matching evidence stays
unmeasured; it is never scored zero. The score fills the offline-strength dimension, and its sources are named in
the basis and reasons.

**K14 Data anomalies.** Every run writes an `anomalies` table for the review queue; nothing is dropped or
corrected. The checks:
- **Invalid values:** price ≤ 0, rating outside 0–5, negative sales or revenue.
- **Price outliers within a canonical product:** robust z of log price above `price_z` and more than
  `price_ratio` from the product median; needs 3+ priced listings.
- **Revenue mismatch:** source revenue more than `revenue_tolerance` away from price × units.
- **Jumps:** price or sales jumps against the listing's previous observation in `observation_history`, beyond
  `jump_ratio` / `sales_jump_ratio`. The current run never counts as "previous".

**K15 Currency.** The marketplace of an upload decides its currency (`currency.marketplaces`; SellerSprite
exports default to US). Non-USD prices and revenue are converted at `currency.rates_to_usd`, dated by
`rates_as_of`, before anything else runs. The local values are kept, and the conversion is recorded in the
run summary and the ingestion warnings. A missing rate stops the run: currencies are never mixed or guessed.

**K16 Requirements brief.** For one segment or taxonomy node, assembled from computed tables only (no model
writes text):
- **Target:** capacity and the opportunity score.
- **Price band:** the gaps engine's significant band when the segment has one, else the interquartile range.
  Entry and premium tiers are shown too.
- **Must-have attributes:** values stated by at least `must_have_share` of the top `top_n` products by modeled
  revenue.
- **Differentiators:** significant demand gaps.
- **Configuration:** the most common component set of the top sellers.
- **Pain to fix:** top review complaints, else the rating-based quality gap.
- **Sourcing:** fee headroom and max FOB.
- **Compliance:** regulatory and hazmat flags.
- **Evidence:** the engine's reasons and the dimensions that were not measured.

A section without data says so. It is never filled in.

**K17 Shopping requirements.** The buyer's words are parsed with the category's attribute schema and the unit
parser:
- enum, list and code attributes become equality or "includes" requirements;
- numbers become ≥ / ≤ / ≈ requirements, read from the surrounding words ("at least", "50k+", "up to");
  ≈ allows ± `about_tolerance`;
- money becomes the budget;
- stars become the minimum rating.

Explicit budget or rating fields always win. A product whose stated attributes contradict a requirement is
filtered out and counted. A product that does not state the attribute is kept, and its status is `unknown`.
The share of stated requirements met counts toward the match criterion, equally with the buyer's words.

**K18 Category boundary: enforce mode and impact.** A sub-category holding more than `max_auto_exclude_share`
of a market's listings is never excluded automatically: it goes to review. A category can instead set
`boundary: enforce` in `config/categories.yaml`, and then such a sub-category is excluded like any other, with
the reason stated. `GET /markets/{m}/scope` reports the share of listings and of source revenue under each
decision, and what enforcing would remove. On the reference exports, the implants boundary already excludes
the oral-hygiene sub-categories: 62 % of the listings and 100 % of the export's source revenue, all of which
belonged to water flossers and similar products. The in-scope implant listings carry no source sales at all.

**K19 Researched inputs.** Three inputs were researched from public sources on 2026-09-27; none is a quote or
a customs ruling.
- **Freight:** the LCL China→US average of about $2.1/kg.
- **Duty:** HTS 9018.49.80 has an MFN rate of Free. China-origin goods may add Section 301 duty: 7.5 % on List
  4A, 25 % on Lists 1–3. Every sourcing ceiling is therefore shown under each duty scenario
  (`landed_cost.duty_scenarios`).
- **Offline evidence:** distributor catalogs, manufacturers and a trade show, curated per market in
  `data/reference/offline_evidence/<market>.csv`, each row with its URL. The file applies until people
  upload their own evidence. A trade show's exhibitor count covers all of dentistry, so it is recorded in the
  notes and does not add volume to a scope's score.

Exchange rates were not stored: every current export is Amazon US, and a stored rate would be stale before
its first use.

**K20 Knowledge-layer accuracy.** `GET /markets/{m}/knowledge/accuracy` and the Accuracy page report the
following.
- **Dental Confidence against two label sources, kept apart:**
  - the gold benchmark in `data/validated/<m>/gold_labels.csv`, labelled by a model and flagged as such.
    Its reasons EXACT_MATCH, WRONG_CATEGORY and ACCESSORY_ONLY count as dental; UNRELATED counts as not
    dental; AMBIGUOUS rows are left out;
  - people's new `dental` labels.
- **How a prediction is read:** a band of strong / probable / verified counts as dental, a non-dental band
  as not dental, and a review band is counted apart rather than as an error.
- **Reported:** dental recall among decided rows, precision where negatives exist, the review share and n.
  Rates appear only above `evaluation.min_labels`.
- **Also reported:** the duplicate resolver's precision and recall against people's decisions, and the
  taxonomy decisions.

On the reference `denture_base` export, the gold benchmark matched 40 listings:
- all 36 dental listings that got a decision are classified dental, and 3 go to review;
- the single non-dental row is misclassified, so precision rests on one negative and is not yet
  meaningful.

**K21 Ranking stability.** Every weight of the opportunity score is multiplied by U(1 − spread, 1 + spread)
(300 draws, fixed seed), and the ranked scopes are re-scored from their stored dimension scores. For each scope,
the page shows the rank range and the share of draws in which the scope stays in the top 3. Overall, it shows
the median Spearman correlation with the published ranking. On the reference exports that correlation is
0.99–1.0 at ±30 %. The top scopes are robust to the weights, but scopes ranked 3–4 can swap places.

**K22 Freshness.** `GET /freshness` and the Data page report, for each market:
- the latest snapshot and its age;
- whether it is stale (older than `freshness.stale_after_days`); "date unknown" when no upload carries a snapshot date;
- how many snapshot periods exist, how many of them carry a date (undated uploads count by upload date);
- the snapshots still needed for a growth trend (`forecast.min_periods`) and for seasonality
  (`freshness.seasonality_periods`).

New exports dropped into `data/inbox/<market>/`, with the date in the file name, are processed oldest-first
as the next snapshot by the scheduler (`dmis.py schedule`). Content already uploaded is never processed again.
