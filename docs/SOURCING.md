# Sourcing intelligence (`src/dip/sourcing_intel`)

From "this sub-category is an opportunity" to "this is the supplier to contact, at this landed cost and
margin, and here is the message". One run searches every configured Chinese marketplace for a product concept,
judges each offer against the facts the platform already computed, merges the same factory across platforms
and recommends the best offer, with its reasons and what to verify.

## Platforms

| Platform | Connection | Credentials | Notes |
|---|---|---|---|
| 1688.com | official cross-border open API (`com.alibaba.fenxiao.crossborder` keyword + **image** search) | `DIP_1688_APP_KEY`, `DIP_1688_APP_SECRET` (+ `DIP_1688_ACCESS_TOKEN` if the namespace needs one) | signed `_aop_signature` (HMAC-SHA1); English title translation when the API returns it |
| AliExpress | official affiliate API (`aliexpress.affiliate.product.query`) | `DIP_ALIEXPRESS_APP_KEY`, `DIP_ALIEXPRESS_APP_SECRET`, `DIP_ALIEXPRESS_TRACKING_ID` | HMAC-SHA256 `sign` |
| Alibaba.com | data API (any vendor answering a keyword search with JSON) | `DIP_ALIBABA_API_KEY`, `DIP_ALIBABA_API_URL` | request template and field mapping in config |
| Taobao / Tmall | data API | `DIP_TAOBAO_API_KEY`, `DIP_TAOBAO_API_URL` | same |
| Made-in-China.com | data API | `DIP_MIC_API_KEY`, `DIP_MIC_API_URL` | same |

Field names in platform responses differ between API versions and vendors, so every platform's mapping
(dotted paths; first present wins) is in `config/platform/sourcing_intel.yaml`. Adding a platform or switching
vendor is a configuration change. Each platform's requests go through the same client as live Amazon data
(rate limit, retries, raw landing archive, budget).

## The concept

* **From a scope** (a board or intelligence-map sub-category / taxonomy node): the requirements brief
  (price band → target Amazon price, must-have attributes, differentiators, the max-FOB sourcing ceiling),
  the scope's **median FBA fee and package weight** (landed cost), and its expected monthly units
  (first order = expected units × `first_order_months`).
* **From text**: a product idea, with an optional target price and quantity.
* **Queries**: English for international platforms; Chinese for 1688 / Taobao from the category's
  `sourcing_terms_zh` (config/categories.yaml) and the EN→ZH glossary. 1688 also gets an **image search** with
  the scope's best-selling Amazon image.

## How offers are judged

Each component is scored 0–100. A component without data is left out, never guessed, and the coverage is shown.

| Component | Weight | How |
|---|---|---|
| fit | 0.30 | share of the concept's key words the offer states (English, or their Chinese glossary terms); × must-have attributes where stated; negative terms (toy, keychain…) mark "not a match" |
| margin | 0.30 | `(target price − referral − FBA fee − (unit at the first-order tier + freight + duty)) / target price`; 0 at break-even, 100 at 1.5 × target margin |
| reliability | 0.20 | years on platform, verified factory, trade assurance, rating, repeat-buyer rate, sales, response rate |
| compliance | 0.10 | share of the category's required certifications the offer states (e.g. denture base: ISO 13485, FDA). Not stated means "verify with the supplier", never a pass |
| MOQ fit | 0.10 | 100 when the MOQ fits the first order, else first order ÷ MOQ |

Money is converted with configured, **dated** rates (`fx`, CNY from 2026-09-25); a currency without a rate
stops the run. Freight and duty come from landed cost; when no duty rate is set the landed cost is flagged as
"before duty".

**Recommendation:** the Pareto front over (landed unit cost, reliability, fit) among matching offers. The best
offer is the highest-scoring non-dominated offer that leaves a positive margin.

**Suppliers:** offers are merged by normalized company name across platforms (legal suffixes and generic words
removed in English and Chinese) and ranked by their best matching offer.

## Reaching out

`reach_out` registers the supplier once (as `sourcing:<platform>`) and records an **inquiry** in the supplier's
cooperation history with a ready RFQ in English and Chinese. The RFQ covers the spec, must-haves, first-order
quantity, the target price (the max-FOB ceiling), price tiers, MOQ, lead times, samples, FBA-ready packaging and
certificates. Quotes then go into the supplier history as usual (price level, ranking).

## API

`GET /sourcing/status` · `POST /sourcing/concept` (preview, no search) · `POST /sourcing/runs`
(`{market, scope+scope_id | text, target_price?, qty?, platforms?, project_id?}`) · `GET /sourcing/runs[?market=]` ·
`GET /sourcing/runs/{id}` · `GET /sourcing/runs/{id}/offers[?match_only=]` · `POST /sourcing/runs/{id}/reach-out`.

Every run is a `sourcing_runs` row (concept, queries sent per platform, counts, best offer, shortlist, cost,
errors), and all offers with their score components and reasons are kept in `data/platform/sourcing/<run>.json`.
