# Dashboard Spec (Milestone 10)

Streamlit, 5 pages + a landing page. Read-only: the dashboard never
writes to the database (`get_connection()` opens `read_only=True`).
Everything shown is queried live from the real DuckDB database — nothing
is fabricated to fill out the UI, and where real coverage is small (this
pilot has 105 listings, 12 classified `RELEVANT`), the dashboard shows
that real number with context, not a padded one.

Run with:

```bash
pip install -e ".[dashboard]"
streamlit run dashboard/app.py
```

## Structure

```
dashboard/
├── app.py                        landing page: category list + overall data-quality snapshot
├── components/
│   └── data.py                   every DB query, all read-only, all cached (st.cache_data/st.cache_resource)
└── pages/
    ├── 1_Category_Overview.py
    ├── 2_Market_Map.py
    ├── 3_Product_Detail.py
    ├── 4_Listing_Explorer.py
    └── 5_Data_Quality_Center.py
```

Numbered filenames control Streamlit's sidebar ordering — without them
pages sort alphabetically, which would put Data Quality Center second.

## Page 1 — Category Overview

Metrics straight from `category_market_metrics` (Milestone 7): product
count, listing count, observed monthly sales/revenue, median price, plus
the three distributions (price, sales, product-type) as charts. Every
metric label says "observed", matching `docs/market_metrics.md`'s naming
rule, and the page states explicitly which listings the numbers cover
(`relevance_class = RELEVANT` only) with a link to the Data Quality
Center for what's excluded.

## Page 2 — Market Map

The primary visualization, exactly as specified: X = price, Y = observed
monthly sales, bubble size = listing count. Built with Plotly
(`st.plotly_chart(..., on_select="rerun")`) so clicking a bubble sets
`st.session_state["selected_product_id"]`, picked up by Product Detail.

Products with no observed sales data (most of them, in the current pilot
— see below) are **not silently dropped or plotted at y=0** — they're
listed in a separate table underneath, honoring `docs/market_metrics.md`'s
"None, never 0" rule. A product image gallery below the chart gives the
"supermarket" browsing experience even where the scatter plot itself is
sparse.

## Page 3 — Product Detail

The **AI confidence layer is shown first**, before any other product
detail — this was an explicit requirement, not an afterthought:

- **Product identity confidence** — mean `product_listings.match_confidence`
  across the product's listings (Milestone 6's entity resolution)
- **Category relevance confidence** — mean `listing_classification.confidence`
  across the same listings (Milestone 5's classifier)
- **⚠ N listings require review** — count of *other* listings still sitting
  in `match_candidates` with `review_status = 'needs_review'` against this
  product's listings, i.e. candidates that might still join or leave this
  product's cluster

This prevents exactly what the brief warned against: presenting an
uncertain AI classification as a settled fact. Below that: image, type/
brand/price/sales/rating, customer pain points (from `review_insights`,
currently empty — see Milestone 8), the full listings table, bundle
structure (from the `BUNDLE` opportunity signal, Milestone 9), and an
Evidence section showing every opportunity signal's raw JSON evidence —
nothing summarized away.

## Page 4 — Listing Explorer

Product → Listings → ASIN → Source data, covering **all 92 resolved
products**, not just the 11 with market metrics — deliberately broader
than pages 1-3, since this page's job is audit, including listings
classified `IRRELEVANT` or still `UNCERTAIN`. Selecting an ASIN shows the
raw `listings` row, its `listing_classification`, its `product_listings`
assignment, every `match_candidates` row it appears in, and every
`decision_log` entry that touched it. This is the literal answer to "why
did you classify this as the same product?"

## Page 5 — Data Quality Center

Not in the original brief — added because a system that hides its own
uncertainty can't be improved. Real counts (105 raw listings, not the
10,000+ scale of the original mockup — shown honestly rather than padded)
plus two inspectable tables: uncertain relevance classifications and
uncertain product-match candidates, each sortable by confidence.

## Bugs found and fixed while building this (manual browser verification, not just unit tests)

Every page was opened in a real browser against the real database before
this milestone was called done (per the project's UI-verification rule).
That surfaced three real bugs no unit test had caught:

1. **pandas NaN vs. `None` in dashboard truthiness checks** — the same
   class of bug fixed in `scripts/detect_opportunities.py`
   (`docs/opportunity_signals.md`), now in `if row.get(x):` /
   `x if row["y"] else ...` patterns across `2_Market_Map.py` and
   `3_Product_Detail.py`. `st.image(NaN, ...)` doesn't fail silently — it
   raises `AttributeError: 'float' object has no attribute 'format'`
   inside Streamlit's image-format validator, a confusing error for what
   is really just a missing-value bug. Fixed by using `pd.notna()`
   everywhere a DataFrame/Series value is checked, never `is not None` or
   bare truthiness.
2. **A query referenced `product_type` on `product_market_metrics`**,
   which doesn't have that column (Milestone 7 never added one) — crashed
   with `KeyError` the moment Product Detail actually rendered. Fixed by
   joining `listing_classification.product_type` via the product's best
   listing instead (correctly `None` today, since Milestone 5's
   product-type classifier was never built).
3. **A cached DuckDB connection left open across the test session** —
   `dashboard/components/data.py::get_connection()` is `@st.cache_resource`,
   which keeps the connection alive for the life of the process. Running
   the new dashboard tests in the same `pytest` session as the rest of the
   suite left a read-only connection open, and DuckDB won't grant a
   read-write connection (every other test file's normal
   `dmie.database.connection.get_connection()`) while any connection is
   held on the same file — 16 unrelated integration tests started failing
   with lock errors. Fixed with an explicit `autouse` teardown fixture
   that closes and clears the cached connection after the dashboard tests
   finish.

## What isn't built (by design, given known upstream gaps)

- Product-type charts/filters show "unclassified" — Milestone 5's
  product-type classifier doesn't exist yet.
- Customer Pain Points shows "no data" for every product — Milestone 8
  has no real review text to extract from.
- The Market Map scatter is sparse (1 of 11 products has both price and
  sales data) — Milestone 7's AI classification stage is unconfigured, so
  most listings never leave `UNCERTAIN`.

None of these are dashboard bugs — the dashboard's job is to show the
pipeline's real current state accurately, including its gaps, not to
paper over them.
