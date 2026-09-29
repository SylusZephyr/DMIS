# Live Amazon data (`src/dip/acquire`)

SellerSprite uploads keep working exactly as before. Live acquisition is a second way in: it fetches Amazon
data from licensed APIs, writes it as a **dated snapshot** in the same universal record layout, and runs the
same pipeline (ingestion → relevance → category boundary → products → metrics → opportunity engine). Every
number the platform shows is therefore computed the same way whichever way the data came in.

## What it solves

| Problem | With live acquisition |
|---|---|
| One undated snapshot per market: no growth, seasonality or change alerts; evidence grade D | a dated snapshot on a schedule, and a **12-month history backfill** (one snapshot per month end rebuilt from price, sales-badge, rating and review-count histories) |
| Customer pain inferred from star ratings | **review text** for the best-selling listings, analysed by the existing pain ontology |
| Fees and weights often missing | FBA pick & pack fee and package weight from the provider feed landed cost |
| Manual export every time | scheduler tick runs each market every `schedule.every_days` |

## Providers

| Provider | Credentials | Search | Details | History | Reviews | Notes |
|---|---|---|---|---|---|---|
| **Keepa** (recommended backbone) | `DIP_KEEPA_API_KEY` | ✓ | ✓ | ✓ | – | "bought in past month" badge + history, price/rank/rating/review-count history, FBA fee, weight |
| Amazon data API (vendor JSON) | `DIP_SCRAPER_API_KEY`, `DIP_SCRAPER_API_URL` | ✓ | ✓ | – | ✓ | request templates and field names in config, so any vendor with the common `type=search|product|reviews` shape works |
| Product Advertising API 5.0 | `DIP_PAAPI_ACCESS_KEY`, `DIP_PAAPI_SECRET_KEY`, `DIP_PAAPI_PARTNER_TAG` | ✓ | ✓ | – | – | SigV4-signed; no sales badge, no review text |
| Direct amazon.com pages | `direct_html.enabled: true` | ✓ | ✓ | – | page reviews | **off by default**: Amazon's Conditions of Use do not permit automated access. Slow on purpose, never signs in, stops at the first robot check |

Which provider serves which capability, and in what order, is `capabilities:` in
`config/platform/acquisition.yaml`. The first configured one wins.

**Recommended setup:** Keepa (search, details, 12-month history) plus one review-capable data API. Direct page
fetching stays off unless you have assessed the risk yourself.

## Accuracy rules

* Amazon's "N+ bought in past month" is a **floor on a fixed ladder**, not a sales count. It goes into `sales`
  exactly as a SellerSprite badge export does, so the interval-censored demand model reads it correctly. A
  listing without a badge has no value ("below the first rung"), never 0.
* A month with no price in the history means the listing was not buyable that month, so it is left out of
  that month's snapshot (not carried forward).
* Fields from several providers are merged per ASIN. The first source wins where both have a value; the
  source is recorded in `acquired_via`.

## Guard rails

* **Raw evidence:** every provider response is written once, unmodified, to
  `data/platform/landing/<provider>/<date>/`. Secrets are redacted in the recorded request.
* **Budget:** each run stops at `run.budget_usd_per_run`. What was already fetched is still processed and the
  run is marked `partial` with the reason. Keepa's token balance is watched too.
* **Cache and rate limits:** identical requests within `run.cache_hours` are free; each provider has its own
  requests-per-minute limit; 429/5xx responses are retried with backoff.
* **Traceability:** each run is an `acquisition_runs` row recording which provider served each capability,
  the search terms, counts, snapshots produced, requests, cache hits, cost and errors.

## Using it

```bash
export DIP_KEEPA_API_KEY=...                      # and optionally a review provider
python -c "from dip.acquire import status; print(status())"
python -c "from dip.acquire import run_market; print(run_market('denture_base', history=True))"
```

API: `GET /api/v2/acquire/status`, `POST /api/v2/acquire/markets/{market}/run` (`{history, reviews, max_listings}`,
runs in the background), `GET /api/v2/acquire/runs[?market=]`, `GET /api/v2/acquire/runs/{id}`,
`GET /api/v2/markets/{market}/acquired-reviews[?asin=]`.

Search terms per market are in `config/categories.yaml` (`search_terms`, and `sourcing_terms_zh` for supplier
search). The scheduler (`dmis scheduler`) acquires every market on the configured cadence once a provider is set.

## Competitor watchlist

`src/dip/watch.py`, page `/watchlist` (Decide), **Watch** on a product page.

**What gets tracked.** Follow chosen listings (ASINs): price, the "bought in past month" sales badge (or the sales estimate), best-sellers rank, rating and review count.

**Where observations come from.** Two sources, merged into one history:

- **Snapshots:** every processed snapshot of the market (a SellerSprite upload or a live run). These are read from the lake's `observation_history`; nothing is copied. The watchlist works with uploads alone.
- **Live:** with an Amazon detail provider configured, the scheduler re-checks only the watched ASINs every `watchlist.every_hours`, within `watchlist.budget_usd_per_run`. **Check now** does the same on demand. Each run is logged in `/acquire/runs` as kind `watchlist`.

**When an alert fires.** Changes are computed in code between the two latest observations of each metric. They are reported when they pass `watchlist.alerts`:

- price moves ±8 %;
- rating drops 0.2;
- reviews grow 10 %;
- rank moves ±30 %;
- sales estimate moves ±25 %;
- **or** the listing moves to a new sales-badge tier.

A change is published as a `watch.change` event, once per observation. A price cut deeper than `important_price_drop` is *important*, so it reaches owners by e-mail; any other change is a *notice*.

**API:**

| Method | Path |
|---|---|
| `GET` | `/watchlist` |
| `POST` | `/watchlist` |
| `DELETE` | `/watchlist/{id}` |
| `GET` | `/watchlist/{asin}/history` |
| `POST` | `/watchlist/refresh` |

Access is controlled by the `watchlist` permission resource.
