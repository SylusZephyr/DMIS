# Opportunity Signals — Evidence, Not Verdicts

## The rule this milestone exists to enforce

This system never asks an LLM "is this a good opportunity" and reports
the answer back as if it were a fact. Every signal below is a
deterministic combination of already-computed, already-explainable
metrics — market engine (`docs/market_metrics.md`) and review
intelligence (`docs/methodology.md`) — expressed as specific, inspectable
conditions. Each condition carries its own real value and threshold.
Nothing here produces a single opaque score. Implementation:
`src/dmie/opportunity/{signals,evidence}.py`. Orchestration:
`scripts/detect_opportunities.py`.

## Schema

`opportunity_signals` (see `src/dmie/database/schema.sql`):

| Field | Meaning |
|---|---|
| `product_id` | the product this signal is about — `NULL` for category-level signal types (see below) |
| `signal_type` | one of the 6 closed taxonomy values below — never free text |
| `status` | `signal_present` / `signal_absent` / `insufficient_data` (tri-state, see below) |
| `signal_strength` | `LOW`/`MEDIUM`/`HIGH`, how far past its threshold each met condition is — only meaningful when `status = signal_present` |
| `confidence` | how much data backs the finding (sample size), a **distinct concept** from `signal_strength` |
| `evidence` | narrative summary: what was checked, what happened, a product-metrics snapshot |
| `supporting_metrics` | JSON array of the market-engine-derived conditions (demand, listings, price, concentration) |
| `supporting_review_themes` | JSON: the complaint-frequency condition(s) paired with the actual taxonomy theme breakdown and quoted review evidence — not just a count |

`signal_strength` and `confidence` answer two different questions and are
computed independently — a signal can have high confidence (lots of
supporting data) but low strength (barely over its threshold), or the
reverse (`tests/unit/test_signals.py::test_confidence_and_strength_are_independent_concepts`).

## The six signal types

```
PRODUCT_IMPROVEMENT            strong demand + many listings + high review complaints
BUNDLE                         strong core-product demand + accessory activity + limited bundled offerings
PRICE_SEGMENT                  a price band with ~no products, while the category overall has plenty
UNDERREPRESENTED_PRODUCT_TYPE  a product_type with disproportionately few products for its demand
CUSTOMER_PAIN_POINT            a single taxonomy theme with high frequency/severity, standalone
COMPETITIVE_CONCENTRATION      category listing concentration (HHI) at or above a standard threshold
```

A closed taxonomy, enforced in code
(`OpportunitySignal.__post_init__` asserts `signal_type in SIGNAL_TYPES`)
— the same discipline as the review pain-point taxonomy (Milestone 8):
this system does not invent an open-ended list of opportunity labels.

**`PRODUCT_IMPROVEMENT`, `BUNDLE`, and `CUSTOMER_PAIN_POINT`** are
product-level (`product_id` is set). **`PRICE_SEGMENT`,
`UNDERREPRESENTED_PRODUCT_TYPE`, and `COMPETITIVE_CONCENTRATION`** are
category-level (`product_id` is `NULL` by design) — they describe a gap
or structural property of the category as a whole, not an existing
product.

### Status is tri-state, never a boolean

- `signal_present` — every condition evaluated true
- `signal_absent` — at least one condition evaluated false, none unknown
- `insufficient_data` — at least one condition couldn't be evaluated at all

`insufficient_data` and `signal_absent` are never conflated — a product
the system can't evaluate must never look identical to one it evaluated
and found nothing for
(`tests/unit/test_signals.py::test_insufficient_data_is_distinguishable_from_signal_absent`).

## How each condition is operationalized

| Condition | Operational definition | Source |
|---|---|---|
| High observed demand | `best_listing_observed_monthly_sales` (or `observed_monthly_revenue` if sales data is missing) is in the top 25% of the category's products with demand data | `product_market_metrics`, Milestone 7 |
| Many listings | `total_listing_count >= 3` | `product_market_metrics` |
| High complaint frequency | `>= 3` extracted review insights for the theme, OR mean severity `>= 3.5` | `review_insights`, Milestone 8 |
| Low product differentiation | Price coefficient of variation across the category's products `<= 0.15` | `product_market_metrics.representative_price` |
| Accessory activity (proxy) | `>= 2` listings in the category classified `reason = 'ACCESSORY_ONLY'` | `listing_classification`, Milestone 5 |
| Limited bundled offerings | `<= 20%` of the category's listing titles read as a bundle/combo (keyword heuristic) | `listings.title` |
| Empty price band | a price band has 0 (or ~0) products while the category has `>= 4` priced products overall | `category_market_metrics.price_distribution` |
| Underrepresented product type | `< 50%` of category products are `unclassified` (i.e. real type data exists to compare against) | `category_market_metrics.product_type_distribution` |
| High listing concentration | category `listing_concentration_hhi >= 2500` (US DOJ/FTC Horizontal Merger Guidelines "highly concentrated" threshold — an external, defensible reference point, not an invented number) | `category_market_metrics.listing_concentration_hhi` |

Every threshold lives in `config/thresholds.yaml`'s `opportunity` block —
adjustable, not hidden constants.

## Known limitations (stated plainly, not glossed over)

- **"High demand" needs a real comparison population.** With fewer than
  `min_products_for_percentile` (default 4) products having any
  sales/revenue data, a percentile comparison isn't meaningful — reports
  `insufficient_data`, not a guess. In the `denture_base` pilot, only 1 of
  11 products has any demand data at all, so `PRODUCT_IMPROVEMENT` and
  `BUNDLE` are `insufficient_data` for every product today.
- **"Accessory activity" is a weak proxy, not real attach-rate data.**
  Real "frequent accessory purchases" needs co-purchase/"frequently
  bought together" data this project doesn't have. Counting
  `ACCESSORY_ONLY`-classified listings only shows accessories exist and
  were found in the category search — not that they're bought alongside
  the core product. This caveat is written into the condition's stored
  `description` on every row, not just here.
- **"Limited bundled offerings" is a title-keyword heuristic**
  (`bundle`, `combo`, `value pack`, `N-in-1`, `+ accessories`),
  deliberately narrower than generic words like "kit" or "set", which in
  this dataset usually mean a single multi-component product
  (`tests/unit/test_signals.py::test_looks_like_bundle_does_not_match_generic_kit`).
- **`UNDERREPRESENTED_PRODUCT_TYPE` needs real product-type data**, which
  doesn't exist yet (Milestone 5's product-type classifier was never
  built — every product is currently `unclassified`). Reports
  `insufficient_data` honestly rather than fabricating a finding.
- **`PRICE_SEGMENT` finds a gap, not a confirmed opportunity.** A price
  band with no products might mean real unmet demand, or might mean
  nobody wants to pay that much for this kind of product at all. This
  system can tell you the gap exists; deciding whether it's worth filling
  needs business judgment beyond listing counts.
- **`COMPETITIVE_CONCENTRATION` measures listing concentration, not
  revenue or brand-power concentration** — a category could have many
  listings spread across sellers but still be dominated by one brand's
  reputation, which this metric wouldn't catch.

## Real result on the pilot

```
$ python scripts/detect_opportunities.py
products evaluated: 11
signals computed: 29
status breakdown: {'insufficient_data': 23, 'signal_absent': 3, 'signal_present': 3}
by type/status: {
  ('PRODUCT_IMPROVEMENT', 'insufficient_data'): 11,
  ('BUNDLE', 'insufficient_data'): 11,
  ('PRICE_SEGMENT', 'signal_absent'): 2,
  ('PRICE_SEGMENT', 'signal_present'): 3,
  ('UNDERREPRESENTED_PRODUCT_TYPE', 'insufficient_data'): 1,
  ('COMPETITIVE_CONCENTRATION', 'signal_absent'): 1,
}
```

Real, non-trivial findings, not just "insufficient data everywhere":
`PRICE_SEGMENT` correctly identifies the 3 empty price bands
(`$20-50`, `$100-250`, `$250+`) against the pilot's real price
distribution (`<$20`: 8 products, `$50-100`: 3 products) — those two
occupied bands correctly report `signal_absent`, not `signal_present`.
`COMPETITIVE_CONCENTRATION` correctly reports `signal_absent`: the real
HHI (~972) sits well below the 2500 "highly concentrated" threshold.
`PRODUCT_IMPROVEMENT`, `BUNDLE`, and `UNDERREPRESENTED_PRODUCT_TYPE` still
report `insufficient_data` — two compounding, already-documented gaps
(sparse demand data from Milestone 7, zero review insights and no
product-type data from Milestone 5/8), not a flaw in this milestone's
logic. `CUSTOMER_PAIN_POINT` produces zero rows on real data today since
there are no review-insight themes to iterate over yet.

## Bug caught while building this: pandas NaN vs. SQL NULL

An early version of `scripts/detect_opportunities.py` fetched DB rows via
`.df()` (pandas). Pandas represents SQL `NULL` as float `NaN`, and
`float('nan') is None` is `False` — every "does this product have data"
check in `signals.py` silently passed for products with no data at all,
because `NaN` isn't caught by `is not None`. On the real pipeline run,
this produced a wrong `22 insufficient_data / 11 signal_absent` split
instead of the correct all-`insufficient_data` result (before the newer
signal types were added). Fixed by fetching via `fetchall()` and building
dicts manually (the same pattern already used correctly in
`market/aggregation.py`), verified against the real database, and pinned
with `tests/integration/test_opportunity_pipeline.py::test_fetchall_preserves_null_as_none_not_nan`.

## Opportunity Score (Tier 2, Milestone 9)

A single deterministic 0-100 number per product, stored on
`products.opportunity_score`, computed by
`src/dmie/opportunity/scoring.py::compute_opportunity_score()` and
called from `scripts/detect_opportunities.py::run()` right after this
run's signal records are built — no re-query, no new computation beyond
aggregating signals that already exist above with their own evidence.

Only the 3 PRODUCT-scoped signal types feed it: `PRODUCT_IMPROVEMENT`,
`BUNDLE`, `CUSTOMER_PAIN_POINT` (one row per distinct complaint theme).
The 3 category-scoped types (`PRICE_SEGMENT`,
`UNDERREPRESENTED_PRODUCT_TYPE`, `COMPETITIVE_CONCENTRATION`, all with
`product_id = NULL`) describe the category, not a product, and are
excluded — a category-level opportunity score is a natural future
extension, not built here.

Per signal row for a product:

- `status = insufficient_data` → excluded entirely (not evaluated yet,
  never treated as 0 or as absent — PRINCIPLES.md "None never 0")
- `status = signal_absent` → contributes `0.0`
- `status = signal_present` → contributes
  `STRENGTH_WEIGHTS[signal_strength] * confidence`, where
  `STRENGTH_WEIGHTS = {LOW: 30, MEDIUM: 60, HIGH: 90}` (an evenly-spaced
  default, not yet calibrated against real outcome data — same honest
  caveat `signal_strength` itself already carries) and `confidence` is
  the signal's own 0-1 data-coverage confidence.

The score is the mean of all contributions. **`None` (not `0`) when
every product-scoped signal for that product is still
`insufficient_data`** — a product with zero evaluable signals has no
score, not a fabricated low one. In the current `denture_base` pilot,
every product scores `None`: `PRODUCT_IMPROVEMENT`/`BUNDLE` both need
`min_products_for_percentile` comparable products with demand data
(`config/thresholds.yaml`), which this 11-product, mostly-sales-sparse
pilot doesn't yet have — verified against the real database (11/11
products unscored), not a bug.
