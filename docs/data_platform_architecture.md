# Data Platform Architecture (Milestone 16)

## Why this exists

Through Milestone 15, getting new data into DMIE meant manually copying an
xlsx file into `data/raw/<category>/` and running `scripts/ingest.py` (or
the full `scripts/run_pipeline.py`) from a terminal. That's fine for one
developer running the pilot, but it doesn't scale to "a user manages the
data lifecycle" — there's no visibility into whether a new export is even
valid before it overwrites something, no record of what version of the
data produced a given set of results, and no way to tell what changed
between two runs.

Milestone 16 adds that layer **on top of** the existing pipeline, not
instead of it. Every actual computation (classification, entity
resolution, market metrics, opportunity signals) is completely
unchanged — this milestone is entirely about what happens *before* a
file reaches `scripts/ingest.py`, and what gets recorded *around* a
pipeline run.

## The five new concerns

### 1. Upload lifecycle (`dataset_uploads`)

A file a user uploads through the dashboard's **Data Ingestion Center**
page moves through one state machine:

```
staged -> validated -> approved -> ingested
                 \
                  -> rejected (not currently wired to a UI action, but
                     the state exists for a future "reject" button)
```

- **staged**: the file has been saved to `data/staging/<category>/` —
  never `data/raw/`. Nothing in the production database has been
  touched except this one metadata row.
- **validated**: the Dataset Validation Engine (below) has run and
  recorded its report.
- **approved**: the staged file has been copied to a permanent,
  version-stamped path under `data/raw/<category>/` (immutable from
  that point on — PRINCIPLES.md principle 3 — the staged copy in
  `data/staging/` is left in place too, since staging is not itself
  authoritative).
- **ingested**: `scripts/run_pipeline.py` has run against that file and
  completed (successfully or not — `pipeline_run_id` points at the
  `pipeline_runs` row either way).

See `src/dmie/database/datasets.py`.

### 2. Dataset Validation Engine (`src/dmie/validation/dataset_validator.py`)

Runs a read-only, non-destructive check over the raw uploaded
DataFrame *before* anything is normalized or written anywhere. Every
check mirrors a rule that `dmie.cleaning.normalize`/`dmie.cleaning.validation`
already enforce downstream — this is deliberate: a FAIL or WARNING here
means normalization would reject or silently drop the same rows, not a
second, independently-invented notion of "valid" that could drift from
what actually happens at ingest time.

| Check | Severity if triggered |
|---|---|
| Missing/renamed columns (schema mismatch) | FAIL |
| Missing ASIN | FAIL |
| Duplicate ASINs | WARNING (first occurrence is kept, same as `normalize.py`) |
| Missing price / rating / monthly_sales / monthly_revenue | WARNING (kept as unmeasured, never zeroed) |
| Price <= 0 | WARNING (dropped to unmeasured downstream) |
| Rating outside [0, 5] | WARNING (dropped to unmeasured downstream) |
| Missing product image | WARNING |

Overall status is the worst severity across all checks: FAIL > WARNING
> PASS. The Data Ingestion Center blocks approval on FAIL; WARNING can
proceed (the same rows would be handled the same way if the file were
ingested manually today).

### 3. Dataset version registry (`dataset_versions`)

One permanent, append-only row per dataset that actually completed the
full lifecycle and reached `listings`. Tracks `source` (which connector
produced it), `version`, `category_id`, `record_count`, and when it was
created. Deliberately separate from `dataset_uploads` — the same
separation `pipeline_runs` already has from `classification_runs`/
`product_resolution_runs`: one table is "what is this upload's current
status", the other is "what data versions have we ever actually
ingested".

### 4. Connector architecture (`src/dmie/connectors/`)

A minimal interface (`DataConnector.fetch()`) so every future data
source implements the same contract:

- `sellersprite.py` — the only connector that actually exists today.
  Wraps the existing `dmie.ingestion.excel_loader.load_raw_excel` — no
  new file-reading logic. SellerSprite has no public real-time API, so
  "fetch" means "read the file the user exported and uploaded", exactly
  as it always has.

Not built, by explicit instruction (this milestone is foundation only):

- `amazon_api.py` — would fetch live Amazon listing data instead of a
  manual export.
- `google_trends.py` — search-interest signals.
- `web_crawler.py` — direct Amazon page scraping.

Each future connector would implement `fetch()` its own way (an HTTP
call instead of a file read) and plug into the exact same validation →
staging → approval → pipeline flow this one already uses. Nothing about
the Data Ingestion Center page or the pipeline needs to change for a
new connector to exist — that's the point of the interface.

### 5. Snapshot system + change detection (`src/dmie/database/snapshots.py`)

`take_snapshot(category_id)` captures `product_market_metrics` for a
category into a JSON blob (`market_snapshots.snapshot_data`) — price,
observed monthly sales, observed monthly revenue, and listing count per
product. `scripts/run_pipeline.py` now takes one snapshot as its very
first stage (capturing the *previous* run's ending state) and one more
right after `market_calculation` (capturing the *new* state), then
immediately diffs the two with `detect_changes()`.

`detect_changes()` writes one `market_changes` row per detected change:

| `change_type` | Meaning |
|---|---|
| `NEW_PRODUCT` | A product_id in the "after" snapshot that wasn't in "before" |
| `REMOVED_PRODUCT` | The reverse |
| `NEW_LISTING` | A product's `total_listing_count` increased |
| `PRICE_CHANGE` | A product's price moved more than 1% (floating-point noise threshold, not a business-significance judgment) |
| `SALES_CHANGE` | Same, for observed monthly sales |

This is observation, not calculation — the module never recomputes a
market metric itself; it only diffs numbers `src/dmie/market/*` already
produced.

## What deliberately was NOT built here

Per explicit instruction: no 3D UI, no graph database (Neo4j), no AI
agents. This milestone is entirely deterministic Python + DuckDB +
Streamlit — the same stack the rest of the project already uses. The
Data Ingestion Center's validation engine and connector interface are
foundations other milestones can build on, not a finished self-service
data platform.

## How the pieces connect

```
Data Ingestion Center (dashboard/pages/7_Data_Ingestion_Center.py)
        |
        v
components/ingestion.py --- stage_upload() -------> data/staging/<category>/
        |                    (dataset_uploads: staged)
        |
        |--- validate_upload() --> dataset_validator.validate_raw_dataframe()
        |                          (dataset_uploads: validated)
        |
        |--- approve_and_run_pipeline()
                |
                |--> copy staged file to data/raw/<category>/<category>_sellersprite_<version>.xlsx
                |    (dataset_uploads: approved)
                |
                |--> scripts/run_pipeline.py run(category, raw_xlsx=<that path>)
                |       |
                |       |-- market_snapshot stage (take_snapshot, "before")
                |       |-- ingestion_and_normalization  (unchanged, M3)
                |       |-- relevance_classification     (unchanged, M5)
                |       |-- product_type_classification  (unchanged, M11)
                |       |-- entity_resolution_and_product_master (unchanged, M6/M12)
                |       |-- market_calculation            (unchanged, M7)
                |       |-- market_snapshot_and_change_detection (take_snapshot "after" + detect_changes)
                |       |-- review_analysis, opportunity_detection, dashboard_preparation (unchanged)
                |
                |--> dataset_uploads: ingested
                |--> dataset_versions: new row
```
