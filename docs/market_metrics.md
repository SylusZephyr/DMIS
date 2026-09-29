# Market Metrics — Formulas and Definitions

All calculations here are deterministic Python/DuckDB — no LLM performs
any arithmetic. Implementation: `src/dmie/market/{sales,revenue,pricing,
competition,aggregation}.py`. Orchestration: `scripts/calculate_market.py`.
Every number in this document was produced by running that script against
the real `denture_base` pilot data, not invented for illustration.

## "Observed", not "market size"

Every sales/revenue field is named `observed_*` or `*_observed_*`, never
just "sales", "revenue", or "market size". SellerSprite-derived numbers
are observations for the specific listings this system has ingested,
classified as relevant, and resolved into products — not a measurement of
total Amazon market volume. A category could have real sales this system
has no visibility into (listings not yet ingested, sales SellerSprite
itself doesn't report, or activity outside Amazon entirely). Calling any
of this "market size" would be a claim the data cannot support. This
naming was an explicit requirement, not a stylistic choice.

## Population: which listings count

Every calculation below operates **only** over listings with
`listing_classification.relevance_class = 'RELEVANT'`. `UNCERTAIN` and
`IRRELEVANT` listings are excluded entirely — including them would
reintroduce the exact category contamination Milestones 2 and 5 exist to
remove (see `docs/data_dictionary.md`). This is also why the numbers below
look small: as of this milestone, only 12 of 105 `denture_base` listings
have been confidently classified `RELEVANT` (the rest are `IRRELEVANT` or
sitting in the `UNCERTAIN` human-review queue, since the AI classification
stage has no API key configured — see Milestone 5's PROGRESS.md). These
metrics are correct for the population they cover; they are not yet a
complete picture of the category.

Listings are grouped into products via `product_listings`
(`product_id`), the Milestone 6 entity-resolution output. A listing with
no confirmed match to any other listing is its own singleton product —
see `docs/entity_resolution.md`.

## Missing data: `None`, never `0`

Every function here distinguishes "no listing in this group has this
value" (returns `None`) from "the value is legitimately zero". A product
whose listings have no sales data reports `best_listing_observed_monthly_sales
= None`, not `0` — reporting `0` would claim the product sells nothing,
which is a different (and false) statement from "we don't have sales data
for it". This matters a great deal here: only 2 of 12 relevant listings
in the pilot have any `monthly_sales` data at all (see
`docs/data_dictionary.md`'s ~71% missing-rate finding). Coverage counters
(`listings_with_price_data`, `listings_with_sales_data`) are included on
every product precisely so a metric's reliability is visible alongside
the metric itself, not hidden.

---

## Product-level metrics

Computed per `product_id` in `src/dmie/market/aggregation.py::compute_product_metrics`.

| Field | Formula | Notes |
|---|---|---|
| `total_listing_count` | `COUNT(listing_id)` among the product's RELEVANT listings | |
| `best_listing_id` | the listing with the max non-null `monthly_sales` in the group | `None` if no listing in the group has sales data (never guessed from price/rating) |
| `best_listing_observed_monthly_sales` | that listing's `monthly_sales` | |
| `best_listing_annualized_observed_sales` | `best_listing_observed_monthly_sales × 12` | assumes the observed month is representative — a stated assumption, not hidden math |
| `observed_monthly_revenue` | `SUM(monthly_revenue)` across **all** the product's listings, ignoring nulls | deliberately a sum across listings, not just the best listing — see "Why sum revenue across listings" below |
| `annualized_observed_revenue` | `observed_monthly_revenue × 12` | |
| `min_price` / `max_price` / `median_price` | min/max/median of `price` across the product's listings, ignoring nulls | |
| `representative_price` | the best-selling listing's own price, if a best-seller is determinable; else `median_price` | the price most buyers of this product actually paid, weighted toward its dominant listing — falls back to median when no listing has sales data to establish a best-seller |
| `rating` | unweighted mean of `rating` across listings with a rating value | |
| `review_count` | `SUM(review_count)` across listings, ignoring nulls | **currently always `None`** — this SellerSprite export has no review-count column at all (`docs/data_dictionary.md` §12), not a calculation gap |
| `listings_with_price_data` / `listings_with_sales_data` | count of listings in the group with non-null price / sales | coverage transparency, not a metric per se |

### Why sum revenue across listings, but take the max for sales?

`best_listing_observed_monthly_sales` answers "how well does this
product's best-performing listing do" — a single listing's own number.
`observed_monthly_revenue` answers "how much does this product earn in
total, across every ASIN selling it" — summing is correct here because
distinct listings of the same product are still distinct sales channels;
a customer buying via ASIN A and a customer buying via ASIN B are
different transactions, not double-counted units of the same one. This
mirrors the project brief's two distinct questions: "how does the
best-selling listing perform" vs. "total estimated sales" for the
product as a whole.

### Worked example (real data): `Pf598530a9a`

Two listings, `B0CXMQ7DFZ` and `B0H6RLQ6YZ` — the exact-duplicate-title
base-plate-wax pair discovered in Milestone 6:

| | B0CXMQ7DFZ | B0H6RLQ6YZ |
|---|---|---|
| price | $8.99 | *(no data)* |
| monthly_sales | 50.0 | 50.0 |
| monthly_revenue | $449.00 | *(no data)* |
| rating | 3.9 | *(no data)* |

Result: `total_listing_count=2`, `listings_with_price_data=1`,
`listings_with_sales_data=2`, `best_listing_observed_monthly_sales=50.0`
(tied — tie-break is deterministic by `listing_id`, see
`tests/unit/test_sales.py`), `observed_monthly_revenue=449.0` (sum
ignoring the one `None`), `annualized_observed_revenue=5388.0`,
`min_price=max_price=median_price=representative_price=8.99` (the only
price value that exists), `rating=3.9` (the only rating value that
exists). Every "only one listing had this data" fact is visible in the
coverage counters, not silently averaged away.

---

## Category-level metrics

Computed in `src/dmie/market/aggregation.py::compute_category_metrics`.

| Field | Formula | Notes |
|---|---|---|
| `total_product_count` | number of distinct `product_id` among RELEVANT listings | |
| `total_listing_count` | count of RELEVANT listings in the category | |
| `total_observed_monthly_sales` | `SUM(monthly_sales)` across every RELEVANT listing in the category, ignoring nulls | equals the sum of every product's contribution — summing per-listing and summing per-product-then-across-products give the same total |
| `annualized_observed_sales` | `total_observed_monthly_sales × 12` | |
| `total_observed_monthly_revenue` | `SUM(monthly_revenue)` across every RELEVANT listing | |
| `annualized_observed_revenue` | `total_observed_monthly_revenue × 12` | |
| `price_distribution` | `{count, min, max, median, mean, bands}` over listing prices; bands are `<$20, $20-50, $50-100, $100-250, $250+` | JSON in the DB column |
| `sales_distribution` | `{count, min, max, median, mean}` over listings with `monthly_sales` data | **no bands** — with only 2/12 listings reporting sales at all in the pilot, fixed bands would imply a granularity the data doesn't support; add them once real coverage improves |
| `listing_concentration_hhi` | Herfindahl-Hirschman Index over listing counts per product: `Σ(listing_count_i / total_listings)² × 10000` | standard 0–10000 scale; 10000 = one product holds every listing, → 0 as listings spread evenly across many products |
| `product_type_distribution` | count of products per `product_type` (mode of a product's listings' classified type), with missing values counted explicitly as `"unclassified"` | Populated for real since M11 Stage 2/3 (`docs/product_type_classification.md`) — reflects the frozen `denture_base_v1` taxonomy (`DB_RESIN`/`DB_WAX_PLATE`/`DB_RELINE`); `"unclassified"` still appears for any product whose listings didn't clear the confidence threshold, which is honest, not a placeholder |

### Listing concentration, worked example (real data)

12 relevant listings, 11 products (one product has 2 listings, ten have
1 each): `HHI = ((2/12)² + 10×(1/12)²) × 10000 ≈ 972.2`. On the standard
scale (0 = fully dispersed, 10000 = a monopoly), ~972 reflects a
category that is currently almost entirely one-listing-per-product, with
one small exception — expected, since only 14 of 91 candidate pairs were
confirmed matches in Milestone 6.

### Real category totals (denture_base, as of this milestone)

```
total_product_count: 11
total_listing_count: 12
total_observed_monthly_sales: 100.0        (only 2 of 12 listings report sales)
annualized_observed_sales: 1200.0
total_observed_monthly_revenue: 449.0      (only 1 of 12 listings reports revenue)
annualized_observed_revenue: 5388.0
listing_concentration_hhi: 972.2
price_distribution: count=11, min=8.88, max=77.00, median=15.99, mean=28.43
  bands: <$20: 8, $20-50: 0, $50-100: 3, $100-250: 0, $250+: 0
product_type_distribution: {"DB_WAX_PLATE": 8, "DB_RESIN": 1, "DB_RELINE": 1, "unclassified": 1}
```

Reproduce with `python scripts/calculate_market.py` (run
`scripts/classify_product_types.py` and `scripts/resolve_products.py`
first, or just use `scripts/run_pipeline.py`, to populate
`product_type_distribution` for real — see
`docs/product_type_classification.md`). The 1 remaining `unclassified`
product is a genuine, documented rules-ambiguity case (M11 Stage 2's
`B0FMK8XB26`), not missing work.

## What isn't built yet

- Package weight/dimension attributes aren't in the normalized schema
  (a known gap since `docs/data_dictionary.md`), so nothing here uses
  them.
- `product_type_distribution` is structurally correct but has no real
  data to distribute yet.
- Category-level metrics assume one category (`denture_base`); the schema
  and functions take a `category_id` parameter and are not hardcoded to
  it, but multi-category rollups haven't been exercised with real data
  from a second category.
