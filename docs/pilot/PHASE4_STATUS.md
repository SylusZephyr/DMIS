# Master Prompt 4 — status

Branch `p4-pilot`. Tooling for every measurable phase is built, and it has been run on the data that exists. Phases that need inputs only you can supply are marked **BLOCKED**. They wait for those inputs; nothing in them has been simulated or invented.

## Audit before starting (EXISTS / EXTEND / NEW)

| Phase | Before P4 | P4 result |
|---|---|---|
| 0 Inventory | ingestion reports per dataset (EXISTS) | **NEW** `dip/pilot/inventory.py` → `DATA_INVENTORY.md` |
| 1 Accuracy | relevance review queue and feedback (EXISTS) | **NEW** stratified samples, labelling page and CSV, metrics with intervals. Relevance labels feed the existing feedback store. |
| 2 Market numbers | coverage shown as "lower bound" (EXISTS) | **NEW** coverage sensitivity. **FIX**: stable product IDs across snapshots |
| 3 Backtests | forecast backtest MAPE inside `forecast_series` (EXISTS) | **NEW** rolling-origin backtest, alert persistence and pseudo-launch backtest |
| 4 Reviews / suppliers | pain extraction and supplier matching (EXISTS) | **BLOCKED**: no review text or supplier list in `data/raw/` |
| 5 Deployment | compose, backup and restore (EXISTS, P3b) | **BLOCKED**: no server or domain. `PILOT_RUNBOOK.md` drafted |
| 6 Pilot operation | audit log (EXISTS) | **NEW** "This looks wrong" feedback and triage, page-view usage. **BLOCKED**: no pilot users yet |
| 7 Exit report | — | **BLOCKED** on phases 1, 3–6 |

## Findings on the existing data (3 markets, 1 snapshot each)

### 1. The "implants" market is mostly not implants (category definition vs. platform relevance)

`config/categories.yaml` defines implants as implant hardware and placement instruments. It explicitly **excludes** water flossers, floss threaders, toothbrushes and similar products "whose marketing copy lists implants as one of several compatible situations".

The v2 platform's relevance stage answers a different question: is the listing dental at all? It does not answer whether the listing belongs in this category. So all 279 rows were kept.

Measured deterministically from titles (not a label-based accuracy number):
- 24 of the 180 "implants" products have "flosser" in the title.
- Those 24 products carry **95.8 %** of the market's observed revenue.
- The 7 products with "water flosser" in the title alone carry 94.7 %.

Every implants number shown today (market size, leaders, opportunity) therefore describes mostly water flossers.

- **Decision needed:** enforce the category boundary in the platform. The proposal is category-level relevance driven by `categories.yaml` include/exclude rules, measured before and after on a labelled implants sample.
- Until you decide, the implants market should be read as "oral-care products mentioning implants".
- The labelling page lets a reviewer tick "not this category" for exactly this case.

### 2. Leadership conclusions are not robust to missing sales (`COVERAGE_SENSITIVITY.md`)

| Market | Sales known | What changes if the missing sales were at the p25–p75 of observed sales |
|---|---|---|
| dental_models | 9 % of products | Market leader changes (Ultrassist → Ningfan). The leader brand changes in 18 of 27 segments. Revenue could be 10× the observed floor. |
| denture_base | 30 % | Market leader changes. The leader changes in 5 of 11 segments. |
| implants | 34 % | Leaders mostly stable (2–3 of 20 segments change). The **segment ranking is not stable**: Spearman 0.41, 0.28 and −0.07. |

- **Recommendation:** show "leader (observed sales only)" wording and the coverage next to every leader or share claim, until exports with fuller sales coverage are available.

### 3. Product IDs changed whenever a product's listing set changed — fixed

- **Cause:** product IDs were a hash of the member listing IDs (`fast.py`, `dedup.py`). A new seller listing joining a product, or one being delisted, gave the product a new ID. That silently detached labels, comments, project links and anything else keyed on the product.
- **Fix:** `pipeline/product_resolution/stable_ids.py`.
  - Each new group inherits the previous run's ID when it holds the majority of that product's still-present listings.
  - On a split, the larger part keeps the ID.
  - Grouping itself is unchanged.
- **Tests:** a regression test reproduces the ID change first. A unit test covers split, carry and new.
- **Record:** the stability stats of each upload are in the market summary (`product_ids`, `python scripts/pilot.py stability MARKET`).

### 4. Backtests (`BACKTEST_REPORT.md`)

- **Forecasts and change alerts:** not testable yet. Each market has 1 period, and the backtest needs ≥ 4. Nothing is extrapolated.
- **Launch estimates (pseudo-launches):** products launched in the 180 days before the export, estimated from the other products.

  | Market | Products evaluated | Actual inside p10–p90 | Median actual / p50 | WAPE of p50 |
  |---|---|---|---|---|
  | implants | 11 | 82 % (95 % CI 52–95 %) | 1.06× | 108 % |
  | dental_models | 2 | too few to judge | — | — |

  - Interval coverage is near the 80 % target and the median is unbiased. Point estimates are wide, so read the range, not p50.
  - Caveats: n is small; 10 of the 11 evaluated products are floss or flosser oral-care products, not implants (finding 1); only products still listed are observed; segment aggregates still include the product.

### 5. Data inventory (`DATA_INVENTORY.md`)

- `denture_base_sellersprite.xlsx` is **byte-identical** to `dental_models/dental_models_sellersprite_v1.xlsx`. Is it the same data under two market names?
- There is no snapshot date on any dataset.
- There are no review counts or review text.
- denture_base's `上架时间` column is not dates.

## What I need from you

1. **Exports:**
   - 3–6 monthly SellerSprite exports each for micromotor plus 1–2 more categories.
   - Name them with the month (`micromotor_2026-06.xlsx`), or tell me the snapshot dates.
2. **Reviews and suppliers:** review exports (ASIN, text, rating, date) and your supplier list.
3. **Labelling time:** about 50 products per market on the Accuracy labelling page, roughly an hour per market. The accuracy report fills in from these labels.
4. **Decisions:**
   - Enforce the implants category boundary (finding 1)?
   - Is `denture_base` a duplicate of the dental_models v1 export?
5. **Pilot:** a server or VM with a domain, and 3–5 pilot users (name, e-mail, role, categories).

## Commands

```bash
python scripts/pilot.py inventory   --out docs/pilot/DATA_INVENTORY.md
python scripts/pilot.py sample implants --n 50 --excluded 20 --seed 7     # or use the Accuracy labelling page
python scripts/pilot.py accuracy    --out docs/pilot/ACCURACY_REPORT.md
python scripts/pilot.py sensitivity --out docs/pilot/COVERAGE_SENSITIVITY.md
python scripts/pilot.py backtest    --out docs/pilot/BACKTEST_REPORT.md
```
