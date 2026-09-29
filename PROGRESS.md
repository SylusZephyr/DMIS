# Progress Log

## Prototype baseline (M1–M10) — Production Hardening begins

**Milestones 1 through 10 (below), plus the post-M10 human-review-workflow
and evaluation-report work, constitute the completed prototype.** This
state is tagged in git as `prototype-m10-baseline`.

- First git commit for this project. No version control existed for any
  of the work below until now — see `DECISIONS.md` for what was
  excluded from that commit and why (secrets check, generated database,
  generated pipeline outputs, caches; the raw source export and the
  human-labeled gold sample were deliberately kept tracked).
- Full test suite run immediately before the baseline commit, on this
  exact code: **259 passed, 0 failed** (Python 3.14.5, pytest 9.1.0,
  win32). No code was changed to make this pass — this is the test
  result the M1-M10 prototype actually produces.
- No application code was refactored or fixed as part of establishing
  this baseline — this entry and the git setup are the only changes in
  the baseline commit beyond `.gitignore`.
- From here: **Production Hardening phase.** The gap list from the
  "what am I missing" review (no AI key configured anywhere, the AI-calling
  code paths never exercised against a real response, no product-type
  classifier, no re-classification audit trail, single-category-only
  validation, no pipeline orchestration script, and the empty
  `CHANGELOG.md`/`settings.yaml`/`logging.py`/`utils/config.py` stubs) is
  the starting punch list for this phase, not new discoveries — nothing
  below this point should be read as newly-found scope creep.

## Milestone 1 — Project Bootstrap

Status: complete

### What was built

- `pyproject.toml` — package config (`dmie`), runtime deps (`duckdb`, `pyyaml`), dev deps (`pytest`), pytest config (`testpaths = ["tests"]`)
- `src/dmie/database/connection.py` — `get_connection()`, resolves DB path from arg → `DMIE_DB_PATH` env var → default `database/dmie.duckdb`
- `scripts/init_project.py` — idempotent: creates `data/{raw,interim,processed,validated,exports,samples}/` and applies `src/dmie/database/schema.sql` via `CREATE TABLE IF NOT EXISTS`
- `tests/unit/test_health_check.py` — connects to the DB and asserts all 7 expected tables exist
- `README.md` — setup, init, and test-run instructions
- Directory structure verified against the prior bootstrap — no changes needed

### Not built (by design — out of scope for this milestone)

- No AI classification
- No dashboard
- No ingestion logic (excel_loader.py etc. remain empty stubs)

### Test results

```
tests/unit/test_health_check.py::test_database_connects_and_has_expected_tables PASSED
1 passed in 0.10s
```

### Environment notes

- Python 3.14.5, installed via `pip install -e ".[dev]"` into the global user site-packages (no venv currently in use)
- DuckDB database: `database/dmie.duckdb`, 7 tables (categories, listings, listing_classification, products, product_listings, review_insights, decision_log), all 0 rows

## Milestone 2 — Inspect SellerSprite

Status: complete

### What was built

- Added `pandas`, `openpyxl` to `pyproject.toml` dependencies (needed to read `.xlsx`)
- `scripts/profile_data.py` — read-only per-column profiler; writes `data/samples/denture_base_profile.csv`
- `docs/data_dictionary.md` — full inspection of `data/raw/denture_base/denture_base_sellersprite.xlsx`
- `data/samples/denture_base_profile.csv` — per-column profile (dtype, missing rate, unique count, min/max/mean, sample values)
- `tests/integration/test_denture_base_raw_profile.py` — pins raw-file sha256 (immutability check) plus shape, columns, ASIN/price/rating/image sanity checks, and the category-contamination finding

### Key findings (see data_dictionary.md for full detail)

- Raw file: 106 rows × 17 columns, single sheet (`Sheet1`)
- **73.6% of rows are mislabeled for this pilot**: `二级类目` shows 78/106 rows as `义齿修复材料` (denture repair material), only 28/106 as `义齿基托` (denture base) — direct evidence of the keyword-purity problem flagged in the project brief
- 1 duplicate ASIN (`B0FMK8XB26`, non-identical rows — needs a dedup rule)
- 4 product titles shared across distinct ASINs (9 rows) — early evidence of Listing != Product
- `子体销量`/`子体销售额` (sales/revenue) are 70–72% missing — cause unconfirmed, not assumed to mean zero
- `上架时间` ("listing date") holds floats in 0.30–0.88 — not a valid date serial, meaning unknown, flagged as an open question
- No review-count field exists anywhere in this export — `listings.review_count` has no source column yet
- `产品经理` is a constant internal field (not Amazon data), `FBA($)` has no destination in the current schema

### Not built (by design — out of scope for this milestone)

- No relevance/product classification
- No schema changes (open questions about `上架时间`, review count, duplicate ASIN left for the user/next milestone)
- No cleaning/normalization logic

### Test results

```
10 passed in 1.20s
```
(9 new integration tests + the 1 existing health-check test)

## Milestone 3 — Build Normalization

Status: complete

### What was built

- `src/dmie/ingestion/schema.py` — raw→canonical column map, marketplace constant
- `src/dmie/ingestion/excel_loader.py` — read-only `.xlsx` loader with column validation
- `src/dmie/cleaning/normalize.py` — deterministic normalization: whitespace/control-char cleanup, robust numeric parsing, price/rating/sales/revenue validation (invalid → NULL + logged `Decision`, not row-dropped), stable `listing_id` (sha1 of marketplace+ASIN), ASIN dedup (first-occurrence-wins), missing-ASIN row exclusion
- `src/dmie/cleaning/validation.py` — post-normalization invariant checks
- `src/dmie/database/repository.py` — `upsert_listings` (idempotent, `ON CONFLICT DO UPDATE`), `insert_decision_log`, `export_listings_parquet`
- `scripts/ingest.py` — CLI: raw xlsx → normalize → validate → DuckDB + Parquet
- `tests/unit/test_normalize.py` — 22 tests covering malformed/missing input (NaN, blank, currency strings, comma-thousands, out-of-range values, missing ASIN, duplicate ASIN)
- `tests/integration/test_ingest_pipeline.py` — 7 tests: real pilot file end-to-end, idempotent upsert, decision-log roundtrip, Parquet roundtrip, and a regression test for the incident below
- `DECISIONS.md` — created; documents normalization design decisions and the incident below
- Added `pandas`/`openpyxl` already present from Milestone 2; no new deps needed

### Pipeline run (denture_base pilot)

```
normalized listings: 105   (106 raw rows − 1 duplicate ASIN dropped)
decisions logged: 1
parquet: data/processed/denture_base_normalized.parquet
```

### Incident (caught and fixed within this milestone)

An early version of the Parquet export briefly **overwrote the raw source
file** due to a DuckDB parameter-binding order issue in a COPY statement.
Caught immediately, raw file recovered from an untouched backup (hash
verified against the one pinned in Milestone 2), root cause fixed, and a
regression test added. Full detail in `DECISIONS.md`. Final state
confirmed clean: raw file hash matches, 105 listings, 1 decision, Parquet
export correct.

### Not built (by design — out of scope for this milestone)

- No AI/relevance classification
- No category_id assignment (left NULL — no category-resolution logic exists yet)
- No schema changes for FBA/上架时间/package fields (still open questions from Milestone 2)

### Test results

```
48 passed in 5.53s
```
(full suite: Milestone 1 health check + Milestone 2 raw-profile tests + Milestone 3 normalize/ingest tests)

## Interim — Gold Labeling Sample (before Milestone 4)

Status: complete

- `scripts/build_gold_sample.py` — samples 50 rows (seed=42, reproducible) from the 105 deduplicated denture_base listings (same ASIN-dedup rule as `scripts/ingest.py`)
- Output: `data/samples/gold_labels_pilot.xlsx` — 50 rows, 50 unique ASINs
- Context columns kept: asin, title, brand, image_url, url, price, rating, monthly_sales, monthly_revenue, plus raw_secondary_category/raw_subcategory (二级类目/小类目, for labeler context — not part of the normalized `listings` schema)
- Blank columns added for human labeling: relevant, reason, product_type, product_id, status, notes — confirmed all-null (0/50 filled) in every row
- Raw file hash re-verified unchanged after running the script
- Sent to the user for manual labeling; Milestone 4 will consume the filled-in version

### Manual labeling pass (on request)

All 50 rows labeled `relevant` (YES/NO/UNCERTAIN) + `reason`, per the exact
question "does this listing genuinely belong to 义齿基托/denture base?" —
not "is this dental-related". `product_type`/`product_id`/`status` left
blank (belong to later stages).

`reason` uses a short standardized code, not free text:

| Code | Count | Relevant | Meaning |
|---|---|---|---|
| `EXACT_MATCH` | 9 | YES | Base-plate wax, denture base resin, or reline/base-repair material that literally reworks the base plate |
| `WRONG_CATEGORY` | 29 | NO | Dental, but a different function (adhesive, cleaning machine, tooth-repair/replacement beads, general repair kit with no base-specific material) |
| `ACCESSORY_ONLY` | 1 | NO | A tool/accessory used on the base, not the base material itself (polishing burs) |
| `UNRELATED` | 1 | NO | Not dental at all — a miscategorized novelty item |
| `AMBIGUOUS` | 10 | UNCERTAIN | Genuine boundary case (DIY full-denture kits, reline bundled with general repair, finished denture product vs. base material) |

Unusual or non-obvious explanations (18 rows) are in `notes`, not `reason` —
e.g. cases where "base" appears in the title as a false-positive keyword
(a cleaning machine's tray, an adhesive's "gum base") rather than referring
to an actual denture base, and the one row where the raw category label
itself (`二级类目`) is wrong relative to the relevance call.

This labeled file is the intended gold set for evaluating Milestone 4's relevance classifier (precision/recall against these 50, not just eyeballing).

### Product clustering pass (on request)

Ran a systematic pairwise title-similarity scan (not just eyeballing) across all 50 to find candidate same-product clusters.

- **1 confirmed cluster** (`product_id = P001`, `status = confirmed`): `B0H14R7BBY` + `B0H14ZK7HR` — identical title/brand/price/rating, same product under two ASINs.
- **3 candidate pairs flagged `needs_review`** (title near-identical, but brand and/or price differ unexplained): not merged, `product_id` left blank, exact mismatch documented in `notes`.
- **1 bundle relationship noted, deliberately not merged**: same brand/base product, but one listing is an explicit multi-pack bundle — kept separate since bundle-vs-single is its own analysis axis.
- Weaker title-similarity pairs (≤0.84) from the saturated "moldable tooth-repair bead" sub-market were checked and NOT flagged — explained by shared SEO-template phrasing across competing brands, not shared product identity.

### Self-review pass (on request) → rules written up

Reviewed all 50 completed labels read-only (file itself unchanged) to find inconsistencies before Milestone 4 builds a classifier on this. No label reversals, but several implicit rules were surfaced and written down:

- The word "base" is unreliable in both directions (false positives from cleaning-machine trays and adhesive "gum base"; false negative from a reline product that never says "base").
- Reline only counts as relevant when it's the listing's dominant function, not merely mentioned alongside competing framing or bundled with a separately-named general repair kit.
- `ACCESSORY_ONLY` (fabrication tool used on the base) vs. `WRONG_CATEGORY` (belongs to a wholly separate product family like denture cleansers) is a real distinction that wasn't written down.
- Bundle-vs-identity ambiguity resolves differently depending on brand match: same-brand + explicit bundle suffix = policy decision (no review flag); different-brand + implicit cue = genuine identity uncertainty (`needs_review`).
- One unresolved boundary question flagged for the business, not fixable by text classification alone: DIY "make your own denture at home" kits sit between "denture base" and "complete denture."

Written up as two new docs:
- `docs/classification_guidelines.md` — relevance/YES-NO-UNCERTAIN rules, with the accessory-vs-wrong-category distinction, grounded in specific ASINs from the gold sample
- `docs/entity_resolution.md` — same-product merge criteria (confirmed vs. needs_review vs. explicit non-merge for bundles), with a similarity/brand/price decision table

## Milestone 4 — Building Your Gold Dataset

Status: accepted as-is by user — 50-row canonical file; 300–500 row target still blocked (see Deferred section in DECISIONS.md)

- `scripts/build_gold_dataset.py` — reshapes `data/samples/gold_labels_pilot.xlsx` into the canonical benchmark schema (`listing_id, relevant, relevance_reason, product_type, product_id, reviewer, review_status`); `listing_id` derived via the same stable hash used by the ingestion pipeline, cross-checked against the DB
- Output: `data/validated/denture_base/gold_labels.csv` — 50 rows (first population of the `data/validated/` tier)
- `product_type` left blank for all 50 — no product-type taxonomy/classifier exists yet
- `review_status`: 36 `model_labeled`, 14 `needs_review` (10 UNCERTAIN relevance calls + 4 product-cluster candidates)

**Caveat surfaced, then resolved by user policy:** `reviewer = model` for all 50 rows. Flagged that the milestone brief calls for human review, not model-generated answers — user's ruling: the labelling model work done under explicit user instruction counts as manual review for this project (see Policy section at the top of DECISIONS.md). Accepted as-is; no rework needed. Scaling to 300–500 rows remains blocked on additional raw source data.

## Milestone 5 — Relevance Classifier

Status: complete (AI stage implemented but unconfigured — see caveat below)

### What was built

- `src/dmie/classification/rules.py` — deterministic rules engine (`RULES_VERSION = rules_v1`), grounded in `docs/classification_guidelines.md`. Fires only on unambiguous title patterns; an escape-hatch list (`reline`, `refit`, `full denture`, `base former`, `denture mold`) suppresses any IRRELEVANT rule when present, deferring to the AI/human stage instead of guessing.
- `src/dmie/classification/classifier.py` — hybrid orchestrator: rules → `classify_with_ai` (real Anthropic SDK call, requires `ANTHROPIC_API_KEY`; returns `None` — not a verdict — when unconfigured) → confidence thresholding (`config/thresholds.yaml`, `ai_confidence_threshold = 0.80`) → structured `ClassificationResult` (all 8 required fields, plus `review_status`)
- `config/thresholds.yaml`, `prompts/relevance_classifier.md` (v1) — populated for the first time
- `src/dmie/database/repository.py::upsert_listing_classifications` — idempotent write into the pre-existing `listing_classification` table
- `scripts/classify.py` — runs the hybrid classifier over all 105 listings in the DB
- `src/dmie/classification/evaluation.py` — precision/recall/F1 per class + confusion matrix, gold vs. predictions joined on `listing_id`, read-only w.r.t. the gold CSV
- `docs/classification_guidelines.md` — extended with an "Implementation" section documenting the actual deployed rules, escape hatches, and the one known limitation
- Tests: `tests/unit/test_rules.py` (17, grounded in real gold titles), `tests/unit/test_classifier.py` (5, AI mocked), `tests/unit/test_evaluation.py` (3, hand-computed metrics), `tests/integration/test_classification_pipeline.py` (3, real end-to-end run + gold-file-hash regression check)

### Real evaluation results (50 gold rows, AI stage unconfigured)

```
label        precision  recall    f1   tp  fp  fn
RELEVANT          1.00    0.78  0.88    7   0   2
IRRELEVANT        0.97    1.00  0.98   31   1   0
UNCERTAIN         0.82    0.90  0.86    9   2   1
```

Only 3 of 50 disagree with gold, and all 3 are understood, not mysterious:
- `B01I3ZIV2M`, `B0DYJMP4ZJ` (gold: RELEVANT) → predicted UNCERTAIN. Both were deliberately kept out of the deterministic rules (the acrylic-liquid-as-base-material and pure-reline-dominance judgment calls need real semantic reasoning) and are correctly waiting on the AI stage, which has no API key configured yet.
- `B0FGXS3NLX` (gold: UNCERTAIN) → predicted IRRELEVANT. The one accepted rule limitation: a DIY full-denture kit whose title also contains "Fake Teeth" and "Denture Repair kit", tripping the WRONG_CATEGORY rule. Documented and tested as a known false-positive rather than patched with a one-off exception.

**Important caveat: the AI semantic stage is implemented but not live.** No `ANTHROPIC_API_KEY` is set in this environment, so every listing the rules don't resolve currently becomes `UNCERTAIN`/`needs_review` — safe-by-default, not silently wrong, but real recall on nuanced cases (28/105 listings, 56% of the full set) won't improve until a key is provided.

### Not built (by design — out of scope for this milestone)

- `product_type` classification (separate `prompts/product_type_classifier.md`, not touched)
- Any change to the gold dataset (read-only throughout, hash-verified in tests)

### Update — AI prompt replaced with user-supplied template (v2)

User supplied a more general-purpose relevance-classification prompt
(generic `<target_category>`/`<listing>` structure, explicit "the word
'dental' is NOT required" instruction, function/intended-use/lab-use/
clinical-use/accessory-relationship framing). Adopted as
`prompts/relevance_classifier.md` v2, replacing v1's denture-base-specific
prose.

- `config/categories.yaml` populated for the first time — the denture-base
  relevance definition (previously embedded in the v1 prompt text) now
  lives here as data, injected into `{TARGET_CATEGORY}` at call time, so
  the same prompt template works for any future leaf category without
  code changes (PRINCIPLES.md principle 9).
- `classifier.py`: `classify_with_ai`/`classify_listing` now take a
  `category` parameter (default `denture_base`); `_load_target_category`
  and `_build_listing_data` build the two prompt placeholders; template
  substitution uses plain string replace (not `.format`) since the
  template's own JSON output example contains literal braces.
- The AI's own `relevant` boolean is not trusted — always re-derived from
  `relevance_class` in code, so the two fields can never disagree even if
  the model's output is inconsistent.
- 2 new unit tests (target-category-from-config, listing-data-is-valid-json). Full suite: 80 passed.
- Still not live: no `ANTHROPIC_API_KEY` configured, same caveat as above.

## Milestone 6 — Product Entity Resolution

Status: complete (AI arbitration stage implemented but unconfigured — same caveat pattern as Milestone 5)

### What was built

- `src/dmie/matching/similarity.py` — Stages 2-4 as pure functions: `brand_score` (normalized brand match, no model field exists in this dataset), `title_similarity` (difflib on normalized titles), `attribute_score` (scoped to price — package weight/dims exist in the raw export but aren't in the normalized schema yet, documented as a follow-up), `extract_quantities` (pack/bundle-size detection, both word orders: "3 ea" and "Pack of 2")
- `src/dmie/matching/candidates.py` — Stage 5 blocking: brand-block + title-fingerprint-block (3 longest non-stopword words, alphabetically tie-broken). Cuts 5,460 possible pairs on the 105-listing pilot down to ~90 (>98% reduction) while still catching every manually-verified case.
- `src/dmie/matching/resolution.py` — Stage 1 (exact identifier, always defers — no such field exists), Stage 6 (weighted composite scoring + a quantity-mismatch override that can force UNCERTAIN even above the match threshold), Stage 7 (AI arbitration, same graceful-degradation pattern as Milestone 5's classifier), union-find clustering into `product_id` groups (every listing gets exactly one, singleton or shared)
- Schema changes: `product_listings.reviewed BOOLEAN` → `review_status VARCHAR` (matches the required-fields list and the pattern already used by `listing_classification`); new `match_candidates` table — the candidate-pair table, a full audit trail (including NO_MATCH/UNCERTAIN, not just confirmed matches)
- `src/dmie/database/repository.py::replace_match_candidates`/`replace_product_listings` — both fully-derived tables, regenerated from scratch each run rather than upserted (product_id assignments can shift as new matches are found)
- `scripts/resolve_products.py` — runs the full pipeline; also exports the uncertainty queue to `data/exports/denture_base/product_matching_uncertainty_queue.csv`
- `config/thresholds.yaml`, `prompts/entity_resolution.md` (v1) — populated
- `docs/entity_resolution.md` — extended with an "Implementation" section
- Tests: `tests/unit/test_similarity.py` (14), `test_candidates.py` (8), `test_resolution.py` (12, grounded in the 5 manually-verified pairs from the earlier gold review), `tests/integration/test_resolution_pipeline.py` (6, real end-to-end run). Full suite: **119 passed**.

### Bug caught and fixed during this milestone: non-deterministic blocking

`title_fingerprint` sorted candidate words by `-len(word)` only. Python's
`set` iteration order is hash-randomized per process, so ties between
equal-length words were broken arbitrarily — re-running the exact same
pipeline could select different "top 3" words and produce a different
candidate set each time (91 vs 88 vs 87 pairs across 3 runs). Fixed by
adding an alphabetical secondary sort key
(`key=lambda w: (-len(w), w)`); verified deterministic across
`PYTHONHASHSEED=0,1,2`. Regression test added
(`test_candidates.py::test_title_fingerprint_is_deterministic_on_length_ties`).
Violates PRINCIPLES.md's reproducibility requirement if left unfixed — caught
before being reported as a final result, not after.

### Real results (105 denture_base listings)

```
candidate pairs (post-blocking): 91   (vs. 5,460 all-pairs -- 98.3% fewer comparisons)
pair decisions: MATCH=14, NO_MATCH=1, UNCERTAIN=76
resulting products: 92               (24 listings clustered into 13 multi-listing products)
uncertainty queue: 76 rows -> data/exports/denture_base/product_matching_uncertainty_queue.csv
```

All 5 manually-verified pairs from the earlier gold review behave exactly
as documented in `docs/entity_resolution.md`: the confirmed duplicate
listing (`B0H14R7BBY`/`B0H14ZK7HR`) shares a `product_id`; the 4
needs-review candidates (different-brand near-duplicate, color-variant
with unexplained price gap, and the bundle pair) all land in UNCERTAIN and
are NOT auto-merged. The pipeline also found 13 *new* confirmed matches
beyond the 50-row gold sample — including the exact-duplicate-title pair
(`B0CXMQ7DFZ`/`B0H6RLQ6YZ`) flagged as evidence of Listing != Product back
in Milestone 2's data dictionary, but never resolved into product IDs
until now.

### Not built (by design — out of scope for this milestone)

- Package weight/dimension attribute matching (Stage 4 scoped to price; the raw fields exist but aren't in the normalized schema)
- `products` table population (product_name, representative_image, etc. — a later milestone's job)
- AI arbitration is not live (no `ANTHROPIC_API_KEY`) — all 76 UNCERTAIN pairs are genuinely queued for human review, not silently guessed at

### Update — Product Identity Rules (own section) + per-category variant policy

User asked for a dedicated "Product Identity Rules" write-up: Same product
(same brand + same model + same core configuration), Different product
(different model OR materially different configuration OR different
functional product), and Product Family (variants like a micromotor's
35K/40K/50K RPM tiers) — with explicit instruction that whether a variant
dimension makes one product or several must be **configurable per
category, never hardcoded once for all categories**.

- `docs/entity_resolution.md` — new "Product Identity Rules" section (the
  three definitions above, plus the micromotor example verbatim from the
  brief) placed ahead of the Milestone 6 implementation section
- `config/categories.yaml` — new `variant_policy` block per category:
  `denture_base` gets its real, evidence-based policy (`pack_quantity:
  separate_products`, `color: same_product`); added an illustrative
  `micromotor` category (no data ingested — flagged as such) with the
  **opposite** `rpm_tier: separate_products` policy, to make the
  per-category configurability concrete rather than just asserted in prose
- `src/dmie/matching/resolution.py` — refactored: the pack-quantity
  override that was previously unconditional now reads
  `variant_policy.pack_quantity` for the pair's category
  (`resolve_pair(..., category=...)`) before applying it. Real pipeline
  output on the 105-listing pilot is unchanged (denture_base's config
  matches the old hardcoded behavior) — this was a refactor for
  configurability, not a behavior change.
- 3 new tests, including one that runs the *same* pair under two different
  mocked category policies and asserts two different outcomes — proving
  the behavior is actually read from config, not just documented as if it
  were. Full suite: **121 passed**.

## Milestone 7 — Market Engine

Status: complete

### What was built

- `src/dmie/market/sales.py`, `revenue.py`, `pricing.py`, `competition.py` — pure Python functions, one per formula (annualize, best-selling-listing selection, revenue summing, price/sales distributions, representative price, HHI concentration, product-type distribution). No LLM touches any of this arithmetic.
- `src/dmie/market/aggregation.py` — orchestrates `fetch_relevant_listings` (only `relevance_class = 'RELEVANT'`, joined through `product_listings`) → `compute_product_metrics` → `compute_category_metrics`
- Schema: new `product_market_metrics` and `category_market_metrics` tables (field names use `observed_*`/`annualized_observed_*`, never "market size" — an explicit user requirement, not a stylistic choice); populated `categories` with the `denture_base` row and backfilled `listings.category_id` for all 105 rows (a gap flagged since Milestone 3)
- `src/dmie/database/repository.py` — `replace_product_market_metrics`, `replace_category_market_metrics` (both fully regenerated each run, same pattern as Milestone 6's tables), `update_best_listing_flags` (finally populates `product_listings.is_best_listing`, a field that existed since the original schema but was never written to until now)
- `scripts/calculate_market.py` — CLI, takes a `category_id` (defaults to `denture_base`)
- `docs/market_metrics.md` — every formula documented with a real worked example from the actual data, not illustrative numbers
- Tests: `test_sales.py` (9), `test_revenue.py` (5), `test_pricing.py` (9), `test_competition.py` (8), `test_aggregation.py` (6), `tests/integration/test_market_engine_pipeline.py` (5, real end-to-end run). Full suite: **163 passed**.

### Real results (denture_base, 12 RELEVANT listings → 11 products)

```
total_observed_monthly_sales: 100.0        (2/12 listings report sales)
total_observed_monthly_revenue: 449.0      (1/12 listings reports revenue)
annualized_observed_sales: 1200.0
annualized_observed_revenue: 5388.0
listing_concentration_hhi: 972.2
price_distribution: count=11, min=8.88, max=77.00, median=15.99, mean=28.43
```

Small numbers are expected and correct, not a bug: only 12 of 105
listings have reached `relevance_class = 'RELEVANT'` so far (Milestone
5's AI stage is still unconfigured, so 28 sit in the `UNCERTAIN` queue).
The engine is scoped exactly to what's been verified relevant — it does
not pad the numbers with unverified listings.

### Design decisions worth flagging

- **"Observed", never "market size."** Every field name follows the
  user's explicit naming requirement: `observed_monthly_sales`,
  `annualized_observed_revenue`, etc. — SellerSprite data is an
  observation of the listings this system has ingested and verified, not
  a claim about total Amazon market volume.
- **Missing data returns `None`, never `0`.** A product with zero
  sales-data coverage reports `best_listing_observed_monthly_sales =
  None`; coverage counters (`listings_with_price_data`,
  `listings_with_sales_data`) travel alongside every product so a
  metric's reliability is visible, not hidden. Given real coverage is
  ~2/12 for sales and ~1/12 for revenue in this pilot, getting this
  wrong would have been the single most misleading thing this milestone
  could have shipped.
- **Revenue sums across a product's listings; best-selling sales does
  not** — deliberately different aggregations for two different
  questions ("how much does this product earn in total" vs. "how well
  does its best listing perform"). Documented explicitly in
  `docs/market_metrics.md` since it's the kind of asymmetry that looks
  like a bug if unexplained.
- **`sales_distribution` has no price-style bands** — with only 2/12
  listings reporting sales, fixed bands would imply a granularity the
  data doesn't support. Scoped down honestly rather than fabricating
  bucket boundaries.

### Not built (by design — out of scope for this milestone)

- Package weight/dimension attributes (still not in the normalized schema)
- Real `product_type_distribution` data (structurally correct, nothing to distribute yet — Milestone 5's product-type classifier was never built)
- Multi-category rollups exercised with a second real category (function signatures take `category_id`, not hardcoded, but untested beyond `denture_base`)

## Milestone 8 — Review Intelligence

Status: pipeline complete and tested; cannot run against real data yet (no review text exists in this project's data)

### The blocker, stated up front

Our only data source, SellerSprite, has **no review text at all** — not
even a review count (`docs/data_dictionary.md` §12, `listings.review_count`
is NULL for every one of 105 listings). This milestone's pipeline is
fully built and tested using clearly-synthetic example reviews, but has
never touched real `denture_base` data because there is no real review
text to give it. `scripts/analyze_reviews.py` reports this plainly rather
than fabricating example reviews for real products. Same pattern as the
300-500 gold-dataset blocker (Milestone 4) and the unconfigured AI stages
(Milestones 5-7): flag the real gap, don't paper over it.

### What was built

- `config/review_taxonomy.yaml` — the fixed 6-category controlled taxonomy exactly as specified (QUALITY/PERFORMANCE/COMPATIBILITY/USABILITY/PACKAGING/VALUE, each with its subcategories), plus a 1-5 severity rubric
- `prompts/review_analysis.md` (v1) — the extraction prompt, extended with the taxonomy injected as the allowed category list and the severity rubric, plus a `no_pain_point_found` escape hatch so the model isn't forced to invent a complaint in a purely positive review
- `src/dmie/reviews/themes.py` — taxonomy loading + **code-level** validation (`validate_taxonomy_path`) — an invented category like "RELIABILITY" is rejected even though it sounds plausible, not just discouraged in the prompt
- `src/dmie/reviews/extraction.py` — AI extraction with the same graceful-degradation pattern as every other AI stage (`classify_with_ai`/`ai_arbitrate`), plus **`verify_evidence`**: the model's claimed evidence quote must be an actual (case/whitespace-tolerant) substring of the source review, checked in code — the concrete enforcement of "extract only claims supported by the supplied review," not just a prompt instruction
- `src/dmie/reviews/aggregation.py` — deterministic `theme_frequency`, `theme_severity`, `rejection_summary` (no LLM arithmetic, per PRINCIPLES.md principle 4 and this milestone's explicit instruction)
- `src/dmie/database/repository.py::insert_review_insights` — only `status='extracted'` insights (passed both guardrails) go into `review_insights`; every rejection (`rejected_no_evidence`, `rejected_invalid_taxonomy`, `ai_unavailable`, `no_pain_point`) is logged to `decision_log`, never silently dropped
- `scripts/analyze_reviews.py` — CLI, takes a reviews JSON path; reports the "no data" gap plainly when none exists
- `docs/methodology.md` — the pipeline diagram, taxonomy, severity rubric, and evidence-guardrail rationale, populated for the first time
- Tests (all using clearly-synthetic example reviews, not real Amazon data): `test_themes.py` (8), `test_extraction.py` (8, including a fabricated-evidence rejection test and an invented-taxonomy rejection test), `test_aggregation_reviews.py` (6, hand-computed frequency/severity), `tests/integration/test_review_insights_pipeline.py` (3, real DB write path). Full suite: **189 passed**.

### Design decisions worth flagging

- **Evidence verification is a hard code check, not a prompt request.**
  A test (`test_extract_insight_rejects_fabricated_evidence`) proves a
  fabricated quote gets rejected regardless of how confident the model's
  other fields look.
- **Taxonomy validation is also a hard code check.** A test proves an
  invented-but-plausible category name ("RELIABILITY") gets rejected even
  though nothing in the prompt response format would obviously flag it as
  wrong without the code-level check.
- **Rejections are logged, not discarded.** Every non-extracted outcome
  is traceable in `decision_log` with its specific reason.

## Milestone 9 — Opportunity Engine

Status: complete (logic validated against synthetic data matching the brief's 3 patterns exactly; real pilot data reports insufficient_data honestly, not fabricated findings)

### What was built

- `src/dmie/opportunity/signals.py` — 6 individual condition functions (`is_high_demand`, `has_many_listings`, `has_high_complaint_frequency`, `has_low_differentiation`, `has_accessory_activity`, `has_limited_bundle_offerings`, `looks_like_bundle`) plus 3 signal-composition functions (`detect_opportunity_signal`, `detect_improvement_signal`, `detect_bundle_signal`) implementing the brief's exact 3 patterns. `OpportunitySignal.status` is **tri-state** (`signal_present`/`signal_absent`/`insufficient_data`) — a product the system can't evaluate never looks identical to one it evaluated and found nothing for.
- `src/dmie/opportunity/evidence.py::build_evidence` — the literal supporting data behind every signal: each condition's real value and threshold, a product-metrics snapshot, and (once real data exists) quoted review evidence text. This is the direct answer to "the system should show the evidence behind every signal."
- Schema: new `opportunity_signals` table (`conditions`/`evidence` as JSON, `status` tri-state)
- `config/thresholds.yaml` — new `opportunity` block; every threshold (demand percentile, complaint count/severity, price CoV, accessory/bundle thresholds) is documented and adjustable, not a hidden constant
- `scripts/detect_opportunities.py` — orchestrates all 3 signal types for every product in a category
- `docs/opportunity_signals.md` — every condition's operational definition, explicit limitations (the accessory-activity and bundle-offering proxies are named as proxies, not real attach-rate/co-purchase data), and the real pilot result
- Tests: `test_signals.py` (24, including all 3 named patterns firing correctly on clean synthetic data), `test_evidence.py` (3), `tests/integration/test_opportunity_pipeline.py` (4, real end-to-end run + the NaN regression test below). Full suite: **219 passed**.

### Bug caught and fixed during this milestone: pandas NaN vs. SQL NULL

`scripts/detect_opportunities.py` originally fetched product/review rows
via `.df()` (pandas), which represents SQL `NULL` as float `NaN` — and
`float('nan') is None` is `False`. Every "does this product have data" check
silently passed for products with no data at all. Real symptom: the first
run of the real 33-signal pipeline produced `22 insufficient_data / 11
signal_absent`; after the fix, the honest result is `33 insufficient_data`
(matching the real data: only 1/11 products has any demand data, 0 review
insights exist). Fixed by fetching via `fetchall()` + manual dict
construction — the same pattern already used correctly in
`market/aggregation.py`. Pinned with a regression test that reads a known-
NULL column directly from the real DB and asserts it comes back `None`,
not `NaN`. Full writeup in `docs/opportunity_signals.md`.

### Real result on the pilot

```
products evaluated: 11
signals computed: 33 (3 per product)
status breakdown: {'insufficient_data': 33}
```

Every signal reports `insufficient_data`, honestly — two compounding data
gaps (sparse demand data from Milestone 7, zero review insights from
Milestone 8's still-unresolved blocker), not a bug in this milestone's
logic. The three signal-composition functions are validated end-to-end
against clean synthetic data reproducing the brief's exact 3 examples,
proving the logic is correct; there is simply nothing real to find yet.

### Design decisions worth flagging

- **Tri-state status, not a boolean.** Distinguishing "no signal here"
  from "we can't tell" was treated as non-negotiable, not a nice-to-have
  — conflating them would have made every one of today's 33
  `insufficient_data` results look like confirmed negative findings.
- **Every proxy is named as a proxy in the data itself**, not just in
  prose. The `accessory_activity_proxy` condition's `description` field
  says "weak proxy — NOT real co-purchase/attach-rate data" every single
  time it's stored, so a future consumer of `opportunity_signals` can't
  mistake it for real basket-analysis evidence just by looking at the
  table.
- **`looks_like_bundle` deliberately excludes "kit"/"set"** even though
  those words are common in this dataset — tested explicitly, since
  including them would have made "limited bundled offerings" fire
  constantly and meaninglessly on data where "kit" means something else
  entirely (a single multi-component product, per Milestone 5's
  classification guidelines).

### Update — opportunity_signals schema refined to the user's requested taxonomy

User asked for the `opportunity_signals` table to carry
`product_id, signal_type, signal_strength, evidence, supporting_metrics,
supporting_review_themes, confidence`, with `signal_type` drawn from a
named 6-value taxonomy (`PRODUCT_IMPROVEMENT`, `BUNDLE`, `PRICE_SEGMENT`,
`UNDERREPRESENTED_PRODUCT_TYPE`, `CUSTOMER_PAIN_POINT`,
`COMPETITIVE_CONCENTRATION`) rather than my original 3 generic types.

- `src/dmie/opportunity/signals.py` — renamed `opportunity`→retired,
  `improvement`→`PRODUCT_IMPROVEMENT`, `bundle`→`BUNDLE`; added 4 new
  detectors: `detect_customer_pain_point_signal` (standalone per-theme,
  no demand/listing conditions needed), `detect_price_segment_signal`
  (category-level, flags an empty price band), `detect_underrepresented_product_type_signal`
  (category-level, honestly reports `insufficient_data` given no real
  product-type data exists yet), `detect_competitive_concentration_signal`
  (category-level, uses the real HHI from Milestone 7 against the US
  DOJ/FTC "highly concentrated" threshold of 2500 — an external,
  defensible reference point). `signal_type` is now a closed taxonomy
  enforced in code (`OpportunitySignal.__post_init__` asserts membership).
  Added `signal_strength` (LOW/MEDIUM/HIGH, based on margin over
  threshold) and `confidence` (based on sample size) as two **independent**
  computed properties — a signal can have high confidence and low
  strength, or the reverse.
- `src/dmie/opportunity/evidence.py::build_signal_record` — produces the
  exact 7 requested fields; `build_supporting_metrics` and
  `build_supporting_review_themes` split market-engine evidence from
  review-theme evidence (with actual quoted text, not just counts) into
  separate arrays, rather than one undifferentiated blob.
- Schema: `opportunity_signals` rebuilt with the new columns
  (`signal_strength`, `confidence`, `supporting_metrics`,
  `supporting_review_themes`); `product_id` is `NULL` for the 3
  category-level signal types by design (documented in the schema
  comment and in `docs/opportunity_signals.md`).
- `scripts/detect_opportunities.py` — now also computes the 3
  category-level signals from `category_market_metrics` (already
  computed in Milestone 7) and per-theme `CUSTOMER_PAIN_POINT` signals
  from `review_insights`.
- Tests rewritten for the new API + 10 new tests for the 4 new signal
  types and the strength/confidence properties. Full suite: **231 passed**.

**Real result improved substantially** — no longer "all insufficient_data
everywhere". `PRICE_SEGMENT` correctly identifies the pilot's 3 genuinely
empty price bands (`$20-50`, `$100-250`, `$250+`) against 2 real occupied
bands (`signal_absent`, not fabricated as present). `COMPETITIVE_CONCENTRATION`
correctly reports `signal_absent` (real HHI ~972, well below the 2500
threshold). `PRODUCT_IMPROVEMENT`/`BUNDLE`/`UNDERREPRESENTED_PRODUCT_TYPE`
still honestly report `insufficient_data` — same two compounding, already-
documented data gaps as before (sparse demand data, zero review insights,
no product-type data), not a flaw in this refinement.

```
signals computed: 29
status breakdown: {'insufficient_data': 23, 'signal_absent': 3, 'signal_present': 3}
```

## Milestone 10 — Dashboard

Status: complete. All 5 pages manually verified in a real browser against the real database, not just built and assumed working — 3 real bugs found and fixed in the process.

### What was built

- `dashboard/app.py` — landing page: category list + overall data-quality snapshot
- `dashboard/components/data.py` — every DB query behind the dashboard, all read-only (`get_connection(read_only=True)`), all cached
- `dashboard/pages/1_Category_Overview.py` — metrics from `category_market_metrics` + 3 distribution charts, all labeled "observed" per Milestone 7's naming rule
- `dashboard/pages/2_Market_Map.py` — the primary visualization: price × observed monthly sales, bubble size = listing count, click-to-select via Plotly, products with no sales data shown in a separate table rather than dropped or plotted at y=0, plus an image gallery
- `dashboard/pages/3_Product_Detail.py` — the **AI confidence layer shown first**: product identity confidence (Milestone 6), category relevance confidence (Milestone 5), and a "⚠ N listings require review" warning sourced from `match_candidates`; then image/type/brand/price/sales/rating, customer pain points, listings table, bundle structure, and full evidence JSON per opportunity signal
- `dashboard/pages/4_Listing_Explorer.py` — Product → Listings → ASIN → Source data, covering all 92 resolved products (not just the 11 market-relevant ones) for full audit traceability, including `match_candidates` and `decision_log` entries per listing
- `dashboard/pages/5_Data_Quality_Center.py` — added beyond the brief, per the user's own suggestion: real counts (105 raw listings, not a padded number) plus two inspectable tables of uncertain records
- `docs/dashboard_spec.md` — architecture, every page's data source, and the 3 bugs below
- `tests/integration/test_dashboard_data.py` — 10 tests against the real database, including regression tests for both bugs below
- Added `streamlit`/`plotly` as a new `dashboard` optional-dependency group in `pyproject.toml`

### 3 real bugs found via manual browser verification (not caught by any unit test)

Every page was opened in the built-in browser against the real database
before this milestone was called done, per the project's UI-verification
rule. That's what caught these — unit tests alone would not have:

1. **pandas NaN vs. `None`, again** — the same bug class fixed in
   `scripts/detect_opportunities.py` (Milestone 9), now in the dashboard's
   `if row.get(x):` / `x if row["y"] else ...` patterns. `st.image(NaN, ...)`
   raised a confusing `AttributeError` instead of failing obviously. Fixed
   with `pd.notna()` everywhere; this is clearly a recurring project-wide
   trap (second occurrence), documented prominently in
   `docs/dashboard_spec.md` and `data.py`'s docstrings so a third
   occurrence is less likely.
2. **A query referenced `product_market_metrics.product_type`**, a column
   that was never added in Milestone 7 — crashed with `KeyError` the
   moment Product Detail rendered for real. Fixed by joining
   `listing_classification.product_type` via the best listing instead.
3. **A cached DuckDB connection left open across the test session** —
   `data.py::get_connection()` is `@st.cache_resource`, so it stayed open
   for the rest of the `pytest` process once the new dashboard tests ran,
   and DuckDB won't grant a read-write connection (every other test
   file's connection) while it's held — 16 unrelated integration tests
   started failing with lock errors. Fixed with an explicit teardown
   fixture that closes and clears the cached connection.

### Real state shown by the dashboard (nothing padded)

- Category Overview: 11 products, 12 listings, $100 observed monthly
  sales, $449 observed monthly revenue, $15.99 median price
- Market Map: 1 of 11 products has both price and sales data (plotted);
  10 shown in the "no observed sales data" table instead
- Product Detail (wax product): 90% identity confidence, 95% relevance
  confidence, 1 candidate listing pending review
- Data Quality Center: 105 raw listings, 12 relevant / 65 irrelevant / 28
  uncertain, 92 products, 16% of match candidates reviewed

### Not built (by design — reflects known upstream gaps, not dashboard bugs)

- Product-type charts show "unclassified" (Milestone 5 gap)
- Customer Pain Points shows "no data" everywhere (Milestone 8 gap — no review text exists)
- Market Map scatter is sparse (Milestone 5's AI stage unconfigured, most listings stuck `UNCERTAIN`)

## Post-Milestone-10 — Human Review Workflow + Evaluation Report

Status: complete. Confidence-tier thresholds are now empirically calibrated where the data allows it, and honestly flagged as not-yet-calibratable where it doesn't.

### Human review workflow (3-tier confidence routing)

The example 95%/70% thresholds from the brief were explicitly not to be
treated as final — built a real calibration tool and measured instead.

- `src/dmie/classification/calibration.py` — `sweep_thresholds` (precision/coverage at every distinct observed confidence value, not an arbitrary grid) + `recommend_tiers` (smallest threshold meeting a precision target, with explicit `warnings` for degenerate cases — no threshold meets the target, or the distribution is too sparse/bimodal to place a genuine middle tier)
- `scripts/calibrate_thresholds.py` — runs the sweep against the real 50-row gold dataset (relevance classification) and the 5 manually-verified pairs (entity resolution)
- **Real finding: `automatic_threshold = 0.95`** for relevance classification — empirically 97.4% precision (38/39) on the gold set, the highest threshold with real coverage
- **Real finding: the sampling/QA tier could not be empirically calibrated.** The classifier's confidence values are currently bimodal (0.95 for rule matches, 0.0 for `ai_unavailable`) since the AI stage has never run — there's no real data between them. The calibration tool flags this explicitly rather than silently reporting a number that looks measured but isn't. `sampling_threshold = 0.80` is kept as a reasoned pre-existing default, not claimed as measured.
- `src/dmie/classification/classifier.py` — refactored from a binary `AI_CONFIDENCE_THRESHOLD` constant to genuine 3-tier routing (`_tier_for_confidence`) reading `config/thresholds.yaml` dynamically, applied uniformly to both rule-based and AI-based results (previously rules always bypassed the threshold check entirely). New `sample_for_qa` review_status: keeps the model's stated class (more likely right than not at that confidence band) but flags it for periodic audit, distinct from `needs_review` which forces `UNCERTAIN`. Re-ran `scripts/classify.py` against real data — output is byte-for-byte identical (12/65/28, 77 auto_accepted/28 needs_review, 0 sample_for_qa) confirming this was a behavior-preserving refactor for the current data, not a regression.
- `docs/human_review_workflow.md` — the workflow diagram, the calibration methodology, and both real findings above, including the entity-resolution comparison (n=5, too small to be robust, all correct across the full confidence range tested)
- Tests: `test_calibration.py` (9, including a regression test reproducing the real bimodal-distribution warning), `test_classifier.py` extended (3-tier routing, config-loaded thresholds)

### Evaluation report

- `dashboard/pages/6_Evaluation_Report.py` — 3 sections, all real numbers:
  - **Classification Performance**: per-class precision/recall/F1 against the 50-row gold set (surfaces Milestone 5's existing `evaluation.py`, not recomputed)
  - **Product Matching**: `src/dmie/matching/resolution.py::matching_breakdown` — real split of the 91 candidate pairs: **16% automatic, 0% AI-assisted, 84% human-reviewed (pending)**. The 0% AI-assisted is stated plainly with its cause (Stage 7 has no API key), not hidden.
  - **Review Extraction**: honestly reports "not measurable yet" (zero review insights exist) rather than fabricating a "validated sample accuracy" — and separately reports what *is* real: the extraction pipeline's evidence-verification and taxonomy-validation guardrails are confirmed working via passing synthetic-data tests, explicitly labeled as mechanism verification, not real-world accuracy
- Verified in a real browser — no errors, all three sections render with real numbers
- Full suite: **259 passed**

### Design decision worth flagging

The original brief's example numbers (96.2% precision, 72%/21%/7% matching
split) were never used as targets — every number actually shown was
measured from real data, and every section that couldn't be measured
(review extraction) says so explicitly rather than presenting a
plausible-looking placeholder as if it were real.

## Milestone 11 — Product Taxonomy & Classification (Stages 1-3 complete)

Status: **complete.** taxonomy_v1 frozen 2026-09-21; Stage 2 classifier
built, tested, and evaluated 2026-09-21; Stage 3 (Product Master
integration) run for real against the live DB 2026-09-21.

The largest functional hole identified after M10: the pipeline goes
`Listing → Relevant/Irrelevant → Product clustering` with no product-type
step in between, despite the source material (e.g. brushed/brushless
micromotors) explicitly requiring type-level distinctions. Per explicit
instruction, taxonomy discovery and classification must NOT happen in the
same step — discover candidates, get human approval, freeze v1, only then
classify.

- `src/dmie/classification/product_type_discovery.py` — Stage 1 only:
  fully deterministic (no LLM), word-frequency + union-find clustering of
  RELEVANT listings' titles, using only generic (non-category-specific)
  stopwords. Never the final taxonomy.
- **Real run on the 12 RELEVANT denture_base listings**: 3 candidates —
  "Wax Plate" (9 listings), a reline/repair-kit cluster (2 listings,
  auto-label ties on several equally-scored words), and 1 singleton
  (a resin listing sharing no connector word with anything else).
- Bug found and fixed during development: an early version let one word
  ("base" — ~100% prevalent in this RELEVANT population, since that's what
  makes them relevant) bridge all 12 listings into one useless cluster.
  Fixed with an upper prevalence bound (`max_prevalence`), not just the
  lower one. A second bug (word-ranking tie-breaks depending on Python's
  per-process hash randomization, same class as M6's `title_fingerprint`)
  was fixed with explicit `sorted()` calls; regression-guarded with a
  subprocess-based test across 3 different `PYTHONHASHSEED` values, since
  same-process tests can't catch this bug class by construction.
- Tests: `tests/unit/test_product_type_discovery.py` (8, grounded in the
  real 12-listing fixture).
- `docs/product_taxonomy.md` — the candidate approval document: ASINs,
  titles, why each cluster formed (full connector-word overlap, not just
  the top-2 auto-label), and possible category-definition issues per
  candidate (mixed marketing intent inside the wax cluster, a latent
  bite-wax/casting-wax sub-pattern, the reline pair never having been
  compared by entity-resolution blocking at all, etc.).
- **User reviewed and approved all 3 candidates, with renames:**
  `DB_WAX_PLATE` ("Denture Base Wax Plate," CAND_01, kept as one type —
  no pack-size or bite/casting sub-split), `DB_RELINE` ("Denture Reline
  Material/Kit," CAND_02 — explicitly rejecting the auto-label
  "Adjustment Curing" as a tie-break artifact, not a real name),
  `DB_RESIN` ("Denture Base Resin," CAND_03 — approved as its own type
  despite n=1; explicit reasoning: "the taxonomy should represent the
  market, not the current sample size," and "Other" is reserved for
  genuinely unidentifiable purpose, not small samples). Flat, no
  hierarchy, no "Other" bucket, no automatic future expansion (structural
  changes require a new versioned file, never a silent edit to v1).
- **taxonomy_v1 frozen:** `config/taxonomy/denture_base_v1.yaml`
  (machine-readable, `status: frozen`) and
  `docs/taxonomy_denture_base_v1.md` (the approval record, mapping each
  frozen type back to its discovery candidate and documenting every
  explicitly-rejected alternative). See `DECISIONS.md` "M11 — Taxonomy v1
  frozen" for the full reasoning.
### Stage 2 — Classifier (built, tested, evaluated on the 12; not run against the live DB)

Same hybrid pattern as relevance classification (`classifier.py`) and
entity resolution (`resolution.py`): deterministic rules first, AI
fallback second, confidence-threshold routing, never a silent guess.

- `src/dmie/classification/product_type_classifier.py`:
  - `load_taxonomy()` reads `config/taxonomy/{category}_v{version}.yaml`
    at runtime — the allowed output space is never hardcoded in Python.
    Proven by a test that feeds it a completely fake taxonomy and gets a
    fake id back.
  - `apply_rules()`: scores each taxonomy id by keyword "signals" from
    the new `config/product_type_signals.yaml` (a separate, editable file
    — NOT the frozen taxonomy itself; revising a keyword list doesn't
    change the taxonomy or need a new version). Exactly one id scoring >0
    → confident match (0.95). Zero or >1 ids scoring >0 → ambiguous,
    returned at a low confidence that can never auto-accept, forcing a
    defer to AI.
  - `classify_with_ai()` / `classify_product_type()`: same
    graceful-degradation contract as the rest of the codebase — no
    `ANTHROPIC_API_KEY` configured means AI returns `None`, never a
    guessed verdict. New `config/thresholds.yaml` `product_type` section:
    rules 0.95, AI automatic 0.85, AI sampling floor 0.60 (below that, or
    with no AI answer at all, `product_type` is forced to the literal
    string `"UNCERTAIN"` — never a fabricated label). An AI answer outside
    the taxonomy (e.g. `"Other"`) is hard-rejected to `UNCERTAIN` too,
    regardless of stated confidence.
  - `prompts/product_type_classifier.md` — filled in the empty stub left
    over from Milestone 9/10 (`1_Category_Overview.py` used to note it as
    "still an empty stub"). Explicitly instructs the model never to
    invent, rename, or split a taxonomy type, and to return `UNCERTAIN`
    rather than force a fit.
- **First validation target (12 confirmed-relevant listings vs. the
  human-approved taxonomy_v1 mapping), AI unavailable in this
  environment:**

  ```
  ASIN        Human Type      Predicted       Correct
  B07DYMJ7TQ  DB_WAX_PLATE    DB_WAX_PLATE    YES
  B094YBT6VD  DB_WAX_PLATE    DB_WAX_PLATE    YES
  B09JL2CYKR  DB_WAX_PLATE    DB_WAX_PLATE    YES
  B0CDM8JCLH  DB_WAX_PLATE    DB_WAX_PLATE    YES
  B0CXMQ7DFZ  DB_WAX_PLATE    DB_WAX_PLATE    YES
  B0CXTC26TV  DB_RESIN        DB_RESIN        YES
  B0DRBMXKZR  DB_WAX_PLATE    DB_WAX_PLATE    YES
  B0F26TYZQD  DB_WAX_PLATE    DB_WAX_PLATE    YES
  B0F7LKY5V7  DB_WAX_PLATE    DB_WAX_PLATE    YES
  B0FMK8XB26  DB_RELINE       UNCERTAIN       NO
  B0G81P76D8  DB_RELINE       DB_RELINE       YES
  B0H6RLQ6YZ  DB_WAX_PLATE    DB_WAX_PLATE    YES

  Accuracy: 11/12 (91.7%)
  ```

  **The one miss is a real, documented ambiguity, not a bug:**
  `B0FMK8XB26`'s title contains "Acrylic-Based" (a `DB_RESIN` signal)
  alongside "reline"/"repair"/"adjustment" (`DB_RELINE` signals) — resin
  and reline kits are chemically the same acrylic material used for
  different purposes, so keyword rules alone genuinely can't separate
  them here. The rules layer correctly recognized the tie and deferred to
  AI instead of guessing; with no AI key configured, it correctly landed
  on `UNCERTAIN` rather than a silent wrong answer. Extending the check to
  2 more hand-reviewed listings from the relevance gold set
  (`B01I3ZIV2M`, `B0DYJMP4ZJ` — see `docs/classification_guidelines.md`'s
  own description of them) reproduces the exact same, single ambiguity
  pattern (`B01I3ZIV2M` has the identical acrylic/repair overlap) and
  otherwise scores 12/14 (85.7%) with no new failure modes.
- **Product Master integration verified**, not yet run for real:
  `matching/resolution.py::build_products()` (M12, already mode-votes
  `product_type` across a product's listings) was fed real classifier
  output in a test — confirmed a multi-listing product gets its type
  correctly (`B0CXMQ7DFZ`/`B0H6RLQ6YZ`, same product, both
  `DB_WAX_PLATE`), and confirmed the one unresolved listing surfaces as
  `None` on its product, never a guessed type ("None never 0"). No
  product identity code or product-master schema was changed to do this —
  `build_products` already accepted a `product_type` field per listing.
- Tests: `tests/test_product_type_classifier.py` (19 — taxonomy loading,
  the rules layer's 3 worked examples from the brief, the ambiguous-title
  case, all AI-tier transitions, the invalid-label guard, an explicit-AI-
  UNCERTAIN-is-valid case, reproducibility, both evaluation tables above,
  and the Product Master integration). Full suite: **293 passed**.
- `docs/product_type_classification.md` — the consolidated stage
  reference (workflow diagram, both config tables, the full evaluation
  writeup, and an explicit "what this stage does NOT do" section).
- **Not classified beyond the 12 RELEVANT denture_base listings this
  stage** — the other ~80 listings in the raw export are IRRELEVANT/
  UNCERTAIN for the category itself and are correctly out of scope for
  product-type classification (PRINCIPLES.md: type is a property of an
  in-category product). Stage 3 below runs the classifier for real
  against exactly these 12 and flows the result into the Product Master.

See `DECISIONS.md` "M11 Stage 2 — Product-type classifier built and
evaluated" for the full design rationale (why signals live in a separate
config from the taxonomy, the threshold values chosen, and the ambiguous-
case handling).

### Stage 3 — Product Master type integration (run for real, 2026-09-21)

Objective: populate `products.product_type` from the Stage 2 classifier's
output, without touching product identity logic.

**New table, not a reuse of `listing_classification`'s columns:**
`listing_product_type_classification` (schema.sql) stores the Stage 2
classifier's structured output (`taxonomy_version`, `product_type`,
`confidence`, `reason`, `classifier_method`, `review_status`) per listing.
`listing_classification.confidence`/`reason`/`review_status` already mean
"relevance decision confidence/reason/status," and `evaluation.py`/the
relevance pipeline depend on that meaning — writing product-type results
into those same columns would have silently corrupted the relevance
pipeline's own data on every re-run (exactly the "silently change
business definitions" failure mode the project principles warn against).
See `DECISIONS.md` "M11 Stage 3" for the full reasoning.

**`products` table extended** with 2 new columns (`schema.sql` + a
one-time `ALTER TABLE` applied to the live DB, since `products` is
otherwise fully regenerated from scratch each run and this DB predates
the schema change):
- `product_type_confidence DOUBLE` — mean confidence of the classifications
  that agree with the product's winning (majority-vote) type. `None` when
  no listing in the product has any classified type at all — never a
  fabricated 0.
- `product_type_conflict BOOLEAN` — `True` whenever a product's listings
  disagree on type (more than one distinct classified type present) —
  surfaced for a human to look at, rather than silently resolved by an
  arbitrary tie-break.

**`matching/resolution.py::build_products()` extended** (product IDENTITY
logic — name/brand/model/representative_image/identity confidence — left
completely unchanged): now computes the majority-vote `product_type`
alongside its confidence and conflict flag from each listing's classified
type + that classification's own confidence. New tests:
`tests/unit/test_resolution.py` (+4 — majority vote with averaged winning
confidence, no-conflict-when-unanimous, unclassified listings don't dilute
the average, singleton confidence/conflict defaults) and
`tests/integration/test_product_type_pipeline.py` (+4 — real round-trip
through the new table via a real in-memory schema, replace-not-append
semantics, confirms `listing_classification` is never touched by a
product-type write, and `build_products` consuming the new table via the
same LEFT JOIN `scripts/resolve_products.py` uses for real).

**Run for real against the live DB:**
- `scripts/classify_product_types.py` (new) — runs Stage 2's classifier
  over the 12 RELEVANT denture_base listings and writes
  `listing_product_type_classification`. Real output:
  `product_type: {DB_WAX_PLATE: 9, DB_RESIN: 1, DB_RELINE: 1, UNCERTAIN: 1}`,
  `classifier_method: {rules: 11, unavailable: 1}` — exactly matching
  Stage 2's evaluation (the one `UNCERTAIN` is `B0FMK8XB26`'s documented
  acrylic/reline ambiguity, AI unavailable).
- `scripts/resolve_products.py` re-run afterward (now reading
  `listing_product_type_classification` instead of the always-empty
  `listing_classification.product_type`, mapping `UNCERTAIN` to `NULL`
  since it isn't a real vote for any type).

**Verified — before vs. after, on the real DB:**
- Before: `SELECT COUNT(*) FROM products WHERE product_type IS NOT NULL` → **0**
- After: → **10** (of 11 market-scoped products; the 11th is the one
  product built from the unresolved `B0FMK8XB26` listing, correctly `NULL`
  — "None never 0," never a guessed type)
- `product_type_conflict`: **0** products flagged — every classified
  product's listings currently agree (expected at this scale: most
  products have 1-2 listings, and the one multi-listing product,
  `Pf598530a9a`, is `B0CXMQ7DFZ`/`B0H6RLQ6YZ`, identical listings that
  necessarily agree).

Commit: `9e1fa20` covered M12/M11 Stages 1-2 (the backlog with no prior
commit checkpoint); this stage's changes are committed separately per the
updated project principle to commit each completed milestone on its own.

## Milestone 12 — Product Master Completion

Status: complete

The pipeline defined a `products` table (Product Master) since Milestone 4
but nothing ever wrote to it — `scripts/resolve_products.py` only ever
populated `product_listings` and `match_candidates`. The dashboard had no
choice but to reconstruct "the product" ad hoc on every page load from the
best listing's raw fields, and a third, separate, throwaway derivation of
"the product's type" already existed in `market/aggregation.py` purely for
a category-level histogram. Three inconsistent notions of one product.

- `src/dmie/matching/resolution.py::build_products()` — derives one
  `products` row per resolved product_id (all 92, not just the 12
  RELEVANT ones) from its constituent listings: `product_name` (best-selling
  listing's title, falling back to the lowest listing_id when no listing
  has sales data), `brand` (mode, alphabetical tie-break), `product_type`
  (mode of classified listings, `None` if none classified — true for all
  92 today, since M11's classifier doesn't exist yet), `confidence`
  (weakest-link match_confidence among the MATCH decisions that formed the
  cluster; `None` for singletons — never a fabricated 1.0), `model`
  (`None` — no extraction exists).
- `src/dmie/database/repository.py::replace_products()` — same
  delete-and-regenerate-from-scratch pattern as `match_candidates`/
  `product_listings`. Wired into `scripts/resolve_products.py`.
- `dashboard/components/data.py` — `load_product_metrics` and
  `load_product_ai_confidence` now read identity fields from `products`
  instead of re-deriving them from listings on every dashboard load.
  Dashboard pages needed no changes (column names kept stable); verified
  in a real browser (Market Map hover/gallery, Product Detail header +
  confidence metric) with no errors.
- Full pipeline re-run against the real DB: `products` now has 92 rows,
  11 with a non-null identity confidence (the multi-listing ones).
- Tests: `tests/unit/test_resolution.py` (+5, `build_products` derivation
  rules) and `tests/integration/test_resolution_pipeline.py` (+2, guarding
  against the table silently going empty again). Full suite: **274 passed**.
- See `DECISIONS.md` "M12 — Product Master Completion" for the full
  field-derivation rationale.

## Milestone 13 — AI Reliability Layer

Status: complete

Objective: make the 4 existing AI call sites production-safe. Every one
of them (`classifier.py::classify_with_ai`,
`product_type_classifier.py::classify_with_ai`,
`matching/resolution.py::ai_arbitrate`,
`reviews/extraction.py::extract_with_ai`) had its own copy of the same
pattern: call the SDK, then blindly index `response.content[0].text` and
`json.loads` a brace-slice, with **no exception handling anywhere**. A
network error, a rate limit, or a response with no valid JSON in it would
raise straight out of the function — since 3 of the 4 call sites are
invoked from a list comprehension over every listing/pair/review in a
batch script, one bad AI response for listing #50 would crash the whole
script before listings 1-49's results were ever saved, let alone 51-105.

- `src/dmie/ai/client.py::call_ai()` (new package, `src/dmie/ai/`) — the
  single place that now talks to the SDK. Never raises: every outcome
  (success, unconfigured, exhausted-retries API error, unparseable
  response) comes back as a structured `AICallResult(status, data, error,
  attempts)`. Retries transient failures (any exception from the API call
  itself, or a response that didn't parse as JSON) up to `max_retries`
  (default 2, so 3 attempts total) with a linear backoff. Logs every
  retry/failure via the standard `logging` module (`logger =
  logging.getLogger("dmie.ai")`).
- All 4 existing call sites refactored to delegate to `call_ai()` —
  **business logic untouched**: each function still builds its own prompt
  and post-processes its own result shape (e.g. `classifier.py` still
  derives `relevant` from `relevance_class` itself); only the "talk to the
  SDK and parse JSON" mechanics moved into the shared, non-raising helper.
  Verified behavior-preserving by re-running the real pipeline
  end-to-end: `scripts/classify.py` output is byte-for-byte identical
  (12/65/28, 77 auto_accepted/28 needs_review) to every prior run, and
  `scripts/classify_product_types.py`/`scripts/resolve_products.py`
  likewise unchanged.
- Tests: `tests/test_ai_failure_handling.py` (14) — valid response
  (including one with surrounding prose, not just a bare JSON object),
  malformed response (no JSON object at all), invalid JSON syntax, empty
  response text, a generic API/connection failure, a rate-limit-style
  exception, a timeout, recovering after a transient failure followed by
  success, giving up after exhausting all retries (asserts the exact
  `call_count`), and — the actual requirement, not just the mechanism —
  two tests proving a failed `call_ai()` does not crash
  `classify_listing()` or `classify_product_type()`, it degrades that one
  result to `UNCERTAIN`/`needs_review` exactly as if the AI stage were
  simply unconfigured. Full suite: **314 passed**.
- **No business logic changed** — no relevance/type/matching/extraction
  rule, threshold, or output shape was touched. This milestone is purely
  about what happens when the AI call itself fails, not what it means
  when it succeeds.

See `DECISIONS.md` "M13 — AI Reliability Layer" for why retries cover
both exceptions and parse failures, and why the SDK's own exception
classes aren't used for error classification.

## Milestone 14 — Run Versioning and Data Provenance

Status: complete

Objective: prevent silent overwriting of historical results. Every
pipeline script (`classify.py`, `classify_product_types.py`,
`resolve_products.py`) deletes-and-regenerates its DATA tables on every
run (by design — see M12/M11 Stage 3), which meant a previous run's
output left no trace once superseded.

- **3 new, append-only tables** (`schema.sql`): `pipeline_runs` (one row
  per script execution — `run_id`, `category_id`, `pipeline_stage`,
  `pipeline_version`, `started_at`/`completed_at`, `status`, `summary`
  JSON), `classification_runs` (relevance + product-type classification
  runs, distinguished by `stage`), `product_resolution_runs`. **Never
  deleted or updated by a later run** — the actual mechanism satisfying
  "do not delete previous results automatically."
- `src/dmie/database/runs.py` — `start_run()`/`finish_run()` (the latter
  in a `finally`/`except` block in every script, so a crashed run is
  recorded `failed` rather than silently vanishing),
  `record_classification_run()`/`record_product_resolution_run()` for the
  stage-specific comparable counts, `list_runs()`, and `compare_runs()` —
  the concrete implementation of "how to compare two pipeline runs" (see
  `docs/pipeline_runs.md`), not just documentation.
- All 3 pipeline scripts wired in and **re-run for real against the live
  DB**: each now prints its own `run_id`, and `list_runs()` against the
  real database shows 2 distinct runs of `classify.py` with identical
  `class_counts`/`auto_accepted_count`/`needs_review_count` — proof the
  classifier's output is reproducible run-to-run, and that this run history
  is now genuinely queryable rather than only visible in terminal
  scrollback.
- `docs/pipeline_runs.md` — the "how to compare two runs" walkthrough,
  plus an explicit "what this milestone deliberately does NOT do" section
  (the DATA tables themselves are still regenerated in place — full
  historical versioning of every table's every row is a separate,
  much larger architectural change, not folded into this one; see
  `DECISIONS.md`).
- Tests: `tests/integration/test_pipeline_runs.py` (11) — running,
  success, and failed statuses; two runs never overwriting each other;
  filtering/ordering; both stage-specific record functions; and
  `compare_runs()` both finding zero differences on identical reruns and
  surfacing real ones, with `run_id`/`created_at` correctly excluded from
  the diff. Full suite: **325 passed**.

See `DECISIONS.md` "M14 — Run Versioning and Data Provenance" for the
scope boundary (why the DATA tables weren't also versioned) and the
reasoning behind what counts as a "meaningful" difference between runs.

## Bug fix — `category_market_metrics.product_type_distribution` stale since M11 Stage 3

Found during the full-system audit (`docs/full_system_audit_report.md`),
confirmed against the live DB, fixed, and re-verified against the live DB
again — 2026-09-21.

- **Root cause:** `market/aggregation.py::fetch_relevant_listings` read
  `product_type` from `listing_classification` (permanently `NULL` since
  Stage 3 moved real classifier output to
  `listing_product_type_classification`). A real regression from Stage 3
  — `build_products()`/`resolve_products.py` were updated to the new
  table at the time, this second independent read was missed.
- **Fix:** `fetch_relevant_listings` now reads from
  `listing_product_type_classification` (same `UNCERTAIN`→`NULL` mapping
  already used elsewhere).
- **Before/after, live DB:**
  `category_market_metrics.product_type_distribution`:
  `{"unclassified": 11}` → `{"DB_WAX_PLATE": 8, "DB_RESIN": 1, "DB_RELINE": 1, "unclassified": 1}`
  (the 1 remaining is the genuine, already-documented rules-ambiguity
  case, not a new bug). `opportunity_signals` now includes a real
  `UNDERREPRESENTED_PRODUCT_TYPE` `signal_present` computed from correct
  data.
- 1 new regression test
  (`test_fetch_relevant_listings_reads_real_product_type_classifications`).
  Full suite: **326 passed**.
- See `DECISIONS.md` "Bug fix — `category_market_metrics.product_type_distribution`
  was permanently stale" for the full writeup.

## Milestone 15 — Pipeline Orchestration

Status: complete

Objective: one reproducible command
(`python scripts/run_pipeline.py --category denture_base`) instead of
running 8 scripts by hand in a required order — the exact gap that let
the M11-Stage-3-era bug (fixed above) go undetected for 2 days.

- `scripts/run_pipeline.py` (new) — calls each existing script's own
  `run()` function in order; reimplements nothing. The requested 10-step
  order maps onto 8 actual callables (`ingest.run()` already combines
  ingestion+normalization; `resolve_products.run()` already combines
  entity resolution+Product Master generation — both pre-existing,
  splitting them here would mean duplicating code). `dashboard_preparation`
  is a real no-op: the dashboard reads DuckDB directly and
  `scripts/build_dashboard_data.py` has never contained any code.
  Supports `--category` (required), `--skip-reviews`, `--skip-dashboard`.
  On a stage failure: every remaining stage is marked `skipped` (never
  silently run against inconsistent data), a readable per-stage summary
  is printed, and a full log is preserved at
  `data/exports/<category>/pipeline_logs/run_<timestamp>.log`. Every run
  is recorded via M14's existing `pipeline_runs` machinery
  (`pipeline_stage="full_pipeline"`) — no new logging infrastructure was
  built.
- **A real, confirmed bug was found by this orchestrator's first actual
  run, not by any test:** `src/dmie/cleaning/normalize.py::normalize_dataframe`
  had hardcoded `category_id: None` for every listing since the function
  was first written — a pre-existing gap that had been invisible because
  nothing had re-run `scripts/ingest.py` against the live DB since some
  earlier, undocumented one-time fix had set `listings.category_id`
  correctly. The orchestrator's first real run called `ingest.run()`
  again, which re-upserted every listing and **wiped `category_id` back
  to `NULL` for all 105 rows** — silently breaking every downstream
  `WHERE category_id = ?` query (product-type classification, market
  calculation, opportunity detection all briefly reported 0
  products/listings). Caught immediately by the orchestrator's own
  summary output, not left undetected.
  - **Fix:** `normalize_dataframe()` and `scripts/ingest.py::run()` now
    accept and thread through a real `category_id` parameter instead of
    hardcoding `None`.
  - **Verified:** re-ran the full pipeline via the orchestrator after the
    fix — `listings.category_id` correctly restored to `denture_base` for
    all 105 rows, and every downstream stage's output matched the
    known-good numbers from before this incident (11 products, 29
    opportunity signals with the same breakdown as the earlier bug fix).
  - This is exactly the kind of defect pipeline orchestration exists to
    surface — a manually-run, partial pipeline had been masking it.
- Tests: `tests/test_run_pipeline.py` (8 — successful full run, a
  mid-pipeline failure correctly stopping every dependent stage, invalid
  category rejected before anything runs, log-file creation, parameter/
  category threading through every stage, `--skip-reviews`/
  `--skip-dashboard`), plus 3 regression tests for the `category_id` bug
  (`tests/unit/test_normalize.py` +2,
  `tests/integration/test_ingest_pipeline.py` +1 —
  `test_category_id_survives_a_second_ingest_run`). Full suite:
  **337 passed**.
- `README.md` — new "Running the complete pipeline" section.

See `DECISIONS.md` "M15 — Pipeline Orchestration" for the design
rationale (why 10 requested steps map to 8 callables, why failure
handling stops rather than continues, and the full incident writeup for
the `category_id` bug).

## Entity Resolution Evaluation Report

Status: complete (report only — no code changed)

`docs/entity_resolution_evaluation_report.md` — a dataset-wide,
quantitative pass over real entity-resolution output (105 listings, 92
products, live DB as of the M15 pipeline run), distinct from
`docs/entity_resolution.md`'s 5 hand-verified reference pairs.

- **Headline numbers:** 88.0% singleton rate (81/92 products), average
  1.14 listings/product, 91 candidate pairs → 14 MATCH / 76 UNCERTAIN / 1
  NO_MATCH. Confidence distribution across all 91 pairs is continuous
  (mean 0.775, not bimodal), unlike relevance classification's — real
  calibration data now exists for `match_threshold`/`no_match_threshold`
  if that's ever wanted.
- **3 suspected false merges found, all one root cause:** manually
  inspecting all 14 auto-MATCHed pairs, 3 look wrong on read (a 1-piece
  vs. 2-pack polishing-bur listing pair; a "Hard" vs. "Soft" reline kit
  pair — different formulations scored 0.934, above `match_threshold`,
  so it never reached Stage 7 AI arbitration at all; a "1 Box" vs. "2
  Bottle" pair). Traced precisely: all 3 are instances of an already
  self-documented limitation in
  `src/dmie/matching/similarity.py::extract_quantities` (a shared,
  unrelated number defeats the disjointness check; "bottle" isn't in the
  recognized unit vocabulary) — not 3 new, unrelated bugs.
- **1 detection-consistency issue flagged** (not a confirmed miss — it's
  correctly sitting UNCERTAIN, not wrongly merged): the same "Pack of 2"
  listing gets its quantity difference caught against one comparison
  listing but not another, depending on whether the other side has any
  recognized quantity marker at all.
- **What's working:** 11/14 (79%) of auto-matches are correct on
  inspection with zero AI arbitration active; the one NO_MATCH decision
  is correct on inspection; the pack-variant guard correctly queues every
  case its vocabulary actually recognizes.
- 4 concrete, prioritized recommendations recorded (widen the quantity
  vocabulary, loosen the disjointness check, decide how to handle
  contrastive-adjective pairs like hard/soft, recalibrate thresholds once
  real AI-arbitration data exists) — **none applied**, per this being a
  report, not a fix pass.

## Dashboard Re-Verification (real browser, post-M13/M14/bug-fix)

Status: complete (verification only — no code changed)

`docs/dashboard_verification_report.md` — all 6 pages checked in a real
Streamlit session, first re-check since M12.

- **Confirms the `category_market_metrics.product_type_distribution` bug
  fix visually, not just in SQL:** Category Overview's pie chart now
  shows real `DB_WAX_PLATE`/`DB_RESIN`/`DB_RELINE`/`unclassified`
  percentages instead of 100% unclassified.
- Market Map: bubbles are products (not listings), hover/click/image
  gallery all confirmed working, missing-sales-data products correctly
  listed separately rather than plotted at a fabricated 0.
- Product Detail, Listing Explorer, Data Quality Center, Evaluation
  Report: all render real, correct data; `category_id` fix (M15) visible
  in Listing Explorer's raw record view.
- Only console errors found are a harmless Streamlit-framework artifact
  (per-page `_stcore/health` 404 that immediately succeeds at the root),
  identical on every page.
- **Gap found, not fixed:** no product-type/price/sales/brand/rating
  filter widgets exist on the Market Map, despite being requested in
  earlier prompts this session. A missing feature, not a defect — not
  built without explicit direction, per this session's "don't add
  features without being asked" discipline.

## Market Map Filters

Status: complete

Built the filter gap identified above, on explicit request.
`dashboard/pages/2_Market_Map.py` gained a "Filters" expander: product
type and brand (multiselect, defaulting to all values present), price
range and sales range (sliders, only shown when there's real variation to
filter on), and a minimum-rating slider. Every filter treats a missing
value as a pass-through, never an implicit exclusion — a product with no
rating isn't "below the rating filter," it's unmeasured (PRINCIPLES.md "None
never 0"); only rows that actually have a value and fall outside the
chosen range/selection are dropped. The chart, the no-sales-data table,
and the product gallery all read from the same filtered set, so they
never disagree with each other.

Verified in a real browser: removing `DB_RELINE` from the product-type
filter correctly dropped the count from 11 to 10 and removed exactly the
one `DB_RELINE`-classified product from the chart/table/gallery (while
correctly leaving the separately-`Unclassified` reline-kit product in
place — the filter distinguishes the two, doesn't conflate them); raising
minimum rating to 5.0 correctly dropped one more product with a real
sub-5 rating while leaving rating-less products untouched; clearing all
product types renders a graceful "no products match" message rather than
crashing. Also fixed a layout issue found in the same pass: 5 equal-width
columns left the multiselect chips unreadably cramped — changed to a
2-column row (product type, brand) over a 3-column row (the 3 sliders).

No new automated test — this project's existing convention for
Streamlit page files (not `components/data.py`, which does have tests)
is real-browser verification, not unit tests, since page scripts aren't
structured as testable functions; restructuring them would be a bigger
change than this request. 337 tests still passing (data layer untouched).

## Fix: Entity Resolution Quantity Vocabulary (false-merge #3)

Status: complete

Scoped to the specific gap requested — the vocabulary issue from
`docs/entity_resolution_evaluation_report.md`, not the related
disjointness-logic issue (recommendation #2, still open).

- `src/dmie/matching/similarity.py::extract_quantities` — recognized unit
  words widened from `pcs?|pc|pack|box|ea|piece` to also include
  `bottle`, `set`, `count`, `ct`.
- **Fixes a real, confirmed false merge:** `B0GVB7J6PV` ("White 1 Box")
  vs `B0GVB48DSX` ("White 2 Bottle") — same brand, near-identical title,
  previously auto-MATCHed at 0.927 because "bottle" wasn't recognized at
  all, so the pack-variant guard (needs both sides non-empty) never ran.
- **Verified against the live DB:** re-ran the full pipeline. This exact
  pair now resolves `UNCERTAIN`/`possible_pack_variant`, not `MATCH` —
  `match_candidates` MATCH count 14 → 13, resolved products 92 → 93.
  Market-scoped numbers (`product_market_metrics`, 11) unaffected, since
  neither listing is RELEVANT.
- 5 new regression tests (`tests/unit/test_similarity.py` +4 —
  bottle/set/count/ct recognized, the exact real pair now disjoint;
  `tests/unit/test_resolution.py` +1 — `resolve_pair` on the real ASIN
  pair no longer returns MATCH). Full suite: **342 passed**.
- **Deliberately not fixed in this pass:** the disjointness-logic gap
  (false-merge #1, `B0F599K6BR`/`B0F598SL5Q` — a shared "12pcs" masks a
  real "1 Piece"/"2 Pack" difference) — a separate mechanism, separate
  recommendation, not part of "the vocabulary issue."

See `DECISIONS.md` "Fix: entity resolution quantity vocabulary" for why
the two recommendations were kept as separate changes.

## Fix: Entity Resolution Disjointness Logic (false-merge #1)

Status: complete

Recommendation #2 from the evaluation report, the other half of the
"fix the vocabulary issue" pair — implemented on explicit follow-up
request.

- `src/dmie/matching/resolution.py::resolve_pair` — pack-variant guard
  changed from requiring full disjointness (`q_a.isdisjoint(q_b)`) to
  firing on symmetric difference (`q_a ^ q_b`). Fixes false-merge #1:
  `B0F599K6BR`/`B0F598SL5Q` shared "12pcs" (an unrelated per-kit
  component count) defeated the old full-disjointness check even though
  they also stated a real, different pack size ("1 Piece"/"2 Pack").
- **Verified the blast radius before writing code, not after:**
  recomputed the guard's old vs. new behavior for all 91 real candidate
  pairs directly against the live DB first. Exactly 3 pairs change: the
  intended fix, one legitimate match now queued instead of auto-decided
  (an accepted tradeoff, not a regression — see below), and one
  already-correct decision that gets a more accurate method label.
- **Accepted tradeoff, checked and documented, not accidental:**
  `B0F9B9LHPW` ("2 Pack (8 Count)")/`B083QN94W5` ("Pack of 2") is a
  correct match that now requires human confirmation instead of
  auto-matching, because one title states a sub-detail ("8 Count") the
  other omits. Queuing a correct match costs a quick human glance; the
  bug this fixes was silently shipping a wrong one. A more surgical fix
  (splitting "count" from "pack" as separate quantity axes) was
  considered and explicitly not done — real restructuring for one
  already-safe side effect, not proportionate to what was asked.
- **Verified against the live DB:** re-ran the full pipeline.
  `match_candidates` MATCH count 13 → 11, resolved products 93 → 95.
  `product_market_metrics` (11) and `opportunity_signals` (29) unchanged
  — none of the affected listings are market-relevant.
- 2 new regression tests using the real ASIN pairs directly (one for the
  fix, one documenting the accepted tradeoff so it can't be silently
  "fixed back" later). Full suite: **344 passed**.

See `DECISIONS.md` "Fix: entity resolution disjointness logic" for the
full 91-pair diff and why the "smarter" count/pack-splitting alternative
was rejected.

## Dashboard: Dynamic UI/UX Pass — Market Map

Status: complete (1 of several pages — a multi-page pass done
incrementally, one page/commit at a time)

On explicit request to make the dashboard "dynamic and in-depth." Every
addition surfaces real data already in the database that wasn't shown
anywhere in the UI yet — nothing fabricated.

- `dashboard/components/data.py::load_product_metrics` — now also
  selects `product_type_confidence`/`product_type_conflict` (M11 Stage 3
  fields that existed in `products` since that milestone but were never
  read by the dashboard).
- New `load_latest_pipeline_run()`/`load_pipeline_run_history()` —
  read M14's `pipeline_runs` table via `src/dmie/database/runs.py`'s
  existing `list_runs()` (no query logic duplicated).
- `dashboard/pages/2_Market_Map.py`:
  - Bubbles now **colored by product type** (was a single flat color) —
    the "Color: Product type" requirement from earlier in this session,
    not previously implemented. Free side effect: Plotly's legend
    becomes click-to-isolate / shift-click-to-compare by type.
  - Hover now also shows monthly revenue, listing count,
    `product_type_confidence`, and `product_type_conflict`.
  - New "🔄 Refresh data" button (`st.cache_data.clear()` + rerun) — a
    manual, honest way to see fresh results after a pipeline run without
    restarting the app (no auto-polling added — not proportionate for a
    locally-run analytical tool).
  - New pipeline-freshness line ("🟢 Data as of the last pipeline run:
    success, completed ... (run `xxxxxxxx`)") reading the real M14 run
    log — previously nothing in the UI showed when data was last
    regenerated.
  - New "What am I looking at?" expander — the project's core
    definitions (listing != product, "observed" not "market size", None
    never 0) made visible in-context, not just in docs.
  - Now surfaces a warning when any plotted product has
    `product_type_conflict = true`.
- **Bug fixed as part of this change, not introduced by it:** coloring by
  product type makes Plotly Express render one trace per type, so the
  existing click-handler's `point["point_index"]` (an index *within a
  single trace*) silently pointed at the wrong row the moment more than
  one product type appeared in the plotted set. Switched to reading
  `product_id` out of `point["customdata"][0]` instead (Plotly Express
  always includes every `hover_data` field in `customdata`, verified
  directly: `fig.data[i].customdata`) — correct regardless of how many
  color traces exist.
- **Verified:** ran the app for real (a separate manual `streamlit run`
  on a spare port, since port 8501 turned out to be already in use by an
  unrelated process — see note below) and confirmed the color legend,
  richer hover fields, refresh button, and freshness banner all render
  with real data. The chart-bubble click-to-select interaction itself
  could not be triggered reliably via automated browser clicks in this
  environment (a known limitation automating canvas/SVG chart libraries,
  not new to this change) — the underlying fix was instead verified
  directly in Python (`fig.data[i].customdata` inspection, shown above),
  and the equivalent gallery "Select" button flow (a plain
  `st.button`, unaffected by this change) was confirmed working
  end-to-end, including real navigation to Product Detail with the
  correct product loaded.
- 4 new tests (`tests/integration/test_dashboard_data.py`). Full suite:
  **348 passed**.

**Unrelated discovery during verification, noted for you, not acted
on:** port 8501 was occupied by a Streamlit process running from a
**different directory** (`...\Downloads\Internship Task\dental-market-intelligence\.venv\...`)
showing a page ("Pipeline and Downloads") and a "snapshot" concept
(`20260922T053602Z-...`) that don't exist in this repository. This
wasn't touched or investigated further — flagging only so you're aware
another copy of this project appears to be running elsewhere.

## Dashboard: Dynamic UI/UX Pass — Category Overview

Status: complete (2 of several pages)

- **Fixed a stale, now-incorrect claim**, not new content: the
  "unclassified" fallback message said "product-type classification
  hasn't been built yet (Milestone 5's `prompts/product_type_classifier.md`
  is still an empty stub)" — leftover from before M11 existed.
  `docs/market_metrics.md` had the identical stale claim in its own field
  reference table and worked example; both corrected to reflect the real,
  current M11 state (and the worked example's numbers updated to the
  actual current values).
- **Market Concentration section (new)** — surfaces
  `listing_concentration_hhi`, computed since M7 but never shown
  anywhere in the UI. Banded using the exact same US DOJ/FTC threshold
  (HHI ≥ 2500 = "highly concentrated") the opportunity engine's
  `COMPETITIVE_CONCENTRATION` signal already uses
  (`src/dmie/opportunity/signals.py`) — not a new threshold invented for
  display.
  - `st.metric`'s `help` tooltips on the sales/revenue tiles now also
    surface `annualized_observed_sales`/`annualized_observed_revenue`
    (computed since M7, never shown).
- **Brand Distribution chart (new)** — how many products per brand;
  real data, not previously shown anywhere.
- **Price Band × Product Type chart (new)** — a real 2-dimensional
  breakdown (which product types cluster at which price points),
  computed client-side from `load_product_metrics`'s already-loaded
  DataFrame; no new query.
- Same refresh button + pipeline-freshness banner pattern as the Market
  Map (reused, not duplicated logic — both read
  `load_latest_pipeline_run()`).
- **Verified in a real browser**: all sections render with real data
  (HHI 972 correctly banded "Competitive / dispersed"; product-type pie
  showing the real `DB_WAX_PLATE`/`DB_RESIN`/`DB_RELINE`/unclassified
  split; brand and price-band-by-type charts both populated). No console
  errors beyond the same harmless per-page Streamlit health-check 404
  seen on every page.
- No new tests — this page's changes are presentation over already-tested
  `load_category_metrics()`/`load_product_metrics()` data, consistent
  with this project's existing convention of verifying page files by
  browser check.

## Dashboard: Dynamic UI/UX Pass — Product Detail

Status: complete (3 of several pages)

- **Product type now shows its human name and definition**, not just the
  raw taxonomy code — reads `config/taxonomy/denture_base_v1.yaml` via
  the existing `load_taxonomy()` (no taxonomy data duplicated into the
  dashboard). "DB_WAX_PLATE" → "Denture Base Wax Plate (`DB_WAX_PLATE`)"
  with its frozen definition shown as a caption.
- `product_type_confidence`/`product_type_conflict` (M11 Stage 3 fields,
  never surfaced anywhere) now shown next to the type — a conflict shows
  an explicit warning pointing at the Listings table below it.
- Price range across a product's listings (`min_price`–`max_price`) shown
  when they differ — previously only a single `representative_price` was
  visible.
- **New "Related Products" section** — other products sharing the same
  classified type, each a real card (image, price, "View" button) that
  reloads the page on that product. Cross-linking that didn't exist
  before; makes the type taxonomy something you can browse, not just a
  label on one product.
- Same refresh button + pipeline-freshness banner pattern as the other
  two pages.
- **A real bug was found and fixed during browser verification**, not
  shipped: `st.write(..., help=...)` isn't a valid call in this
  Streamlit version (`TypeError: WriteMixin.write() got an unexpected
  keyword argument 'help'`) — caught immediately because this page was
  actually loaded in a browser before committing, not just written and
  assumed correct. Fixed by using a separate `st.caption()` for the
  definition text instead.
- **Verified in a real browser**, including interaction: loaded a real
  product (`Pf598530a9a`, `DB_WAX_PLATE`, type confidence 95%), then
  clicked "View" on a related product card and confirmed it correctly
  navigated to a *different* real product (`P027aab49ea`) with its own
  correct data. No console errors beyond the known harmless per-page
  Streamlit health-check 404.
- No new tests — presentation over already-tested data functions,
  verified live.

## Dashboard: Dynamic UI/UX Pass — Listing Explorer

Status: complete (4 of several pages)

- `dashboard/components/data.py::load_product_listings` — now also joins
  `listing_product_type_classification` for each listing's *own*
  classification (type, confidence, method), distinct from the
  product-level mode-voted type. This is shared by Product Detail too
  (already benefits from it there without further changes).
- `load_listing_source_record` — now also returns the listing's
  product-type classification record (previously missing entirely from
  this page's "why was this decided" traceability, even though the
  equivalent relevance-classification record was always shown).
- **Step 2 (Listings table)** now shows each listing's own classified
  type/confidence/method, and warns explicitly when a product's listings
  disagree on type — pointing at exactly why a product-level type might
  differ from what one specific listing says.
- **Step 3 (ASIN → Source data)** now has a fourth JSON panel:
  "Product-type classification", showing the full real record (taxonomy
  version, type, confidence, reason, method, review status) — verified
  live for `B0CXTC26TV`: `{"product_type": "DB_RESIN", "confidence": 0.95,
  "reason": "rule_match:DB_RESIN", "classifier_method": "rules", ...}`.
- **New search box** ("Search by product_id, brand, or title") above the
  product dropdown — with 92 products, a plain dropdown was workable but
  slow; verified live (searching "resin" correctly narrowed to exactly
  the one `DB_RESIN` product).
- Same refresh button pattern as the other pages.
- 2 new tests (`tests/integration/test_dashboard_data.py`). Full suite:
  **349 passed**.

## Dashboard: Dynamic UI/UX Pass — Data Quality Center (final page)

Status: complete (5 of 5 pages — this closes the multi-page pass)

The biggest real gap of the 5 pages: **pipeline status was never shown
anywhere**, despite M14 building the exact table
(`pipeline_runs`) this section needed — nothing had read it until now.

- New `load_confidence_statistics()` — mean/median/min confidence per
  automated decision stage (relevance, product-type, entity resolution),
  read directly from each stage's own real table. Real pilot values:
  relevance mean 0.70 (n=105), product-type mean 0.87 (n=12), entity
  resolution mean 0.77 (n=91).
- New `load_uncertain_product_types()` — the product-type equivalent of
  the existing `load_uncertain_listings()`/`load_uncertain_match_candidates()`;
  a 3rd tab added alongside the existing 2 ("Uncertain product types").
- New `load_product_type_conflicts()` — products whose listings disagree
  on type (`product_type_conflict`, M11 Stage 3, never surfaced before).
  A real, previously invisible data-quality signal, distinct from
  relevance/matching uncertainty. Currently empty in the live pilot data
  (verified, not assumed) — the section correctly says so rather than
  hiding when there's nothing to show.
- **New "Pipeline status" section** — reuses `load_pipeline_run_history()`
  (built for Market Map's freshness banner, no new query logic) to show
  every recorded run for a category, newest first, with a failure
  warning if any recent run failed. This is the literal "pipeline
  status" requirement from earlier in this session, previously
  unbuilt.
- Same refresh button pattern as the other 4 pages.
- **Verified in a real browser**: all 3 tabs render, "Unresolved
  products" correctly reports zero conflicts (matches the real DB state,
  not a placeholder), Confidence statistics table shows real per-stage
  numbers, and Pipeline status shows the real run history accumulated
  across this session's many pipeline re-runs (visible run_ids matching
  the ones referenced throughout this session's other commits). No
  console errors beyond the known harmless per-page Streamlit
  health-check 404.
- 3 new tests. Full suite: **352 passed**.

**This closes the multi-page "dynamic and in-depth" pass** (Market Map,
Category Overview, Product Detail, Listing Explorer, Data Quality
Center) requested at the start of this stretch. Every addition across
all 5 pages surfaced real data that already existed in the database but
had never been shown anywhere — nothing was fabricated, no new
computation was invented, and 2 real bugs (the Streamlit `st.write(help=)`
crash, and the stale "product-type classification hasn't been built"
messaging) were found and fixed along the way specifically because each
page was loaded in a real browser before being called done.

## Visual pass: dark "command-center" theme (all 6 pages)

Requested explicitly, with 3 reference images of high-tech dark
dashboards (glowing map panels, neon-accented metric tiles). This is a
pure presentation-layer change — no data, calculation, or business logic
touched (PRINCIPLES.md principle #1, "data accuracy before visualization",
means the reverse is also true: visuals never get to change what a
number *is*, only how it looks).

- New `.streamlit/config.toml` — sets Streamlit's own base theme to
  dark (background/secondary-background/text/primary colors), so widgets
  the app doesn't explicitly style (date pickers, tooltips, etc.) inherit
  a dark look instead of clashing with injected CSS.
- New `dashboard/components/theme.py` — the single shared theme module
  every page imports, so the look stays consistent without duplicating
  CSS six times:
  - `inject_theme()` — one CSS block (dark radial-gradient background,
    glowing cyan-bordered `st.metric`/`st.container(border=True)`/
    `st.expander` panels, styled sidebar nav with an active-page glow,
    neon-outlined buttons, colored alert accent bars, Rajdhani/Share
    Tech Mono Google Fonts for a HUD feel). Targets Streamlit's
    `data-testid` attributes, not markup the app controls, so it works
    against `st.metric` etc. as-is — no need to rewrite every metric
    call into custom HTML across 6 pages.
  - `style_chart(fig)` — applies `plotly_dark` + a shared neon colorway
    (cyan/magenta/amber/green/...) to any Plotly figure. Found and fixed
    a real bug while verifying: Plotly Express bakes a literal color
    into a *single-series* bar/scatter trace at construction time (using
    whatever template was active then, not the one applied afterward),
    so `style_chart` explicitly recolors single-trace bar/scatter/
    histogram charts; multi-trace charts (an explicit `color=` column)
    already cycle the colorway correctly via `layout.colorway` and are
    left alone. Confirmed by comparing before/after screenshots of the
    Category Overview "Price Distribution" bar chart, which stayed
    default-Streamlit-blue until this fix.
- Wired into `app.py` and all 5 dashboard pages plus the Evaluation
  Report page: one `inject_theme()` call near the top of each, and
  every existing `px.` chart wrapped in `style_chart(...)` before
  `st.plotly_chart(...)`. No page's data-loading or business logic
  touched.
- **Verified in a real browser** (manually-started server, same
  workaround as the rest of this session since `preview_start` can't
  bind port 8501 — see the note above): Category Overview (metric
  tiles, info/warning boxes, all 4 charts including the pie chart's
  per-slice colorway and the fixed bar-chart color), Market Map (filter
  expander, multiselect tags, sliders, colored scatter bubbles), Data
  Quality Center (metric grid, tabs, warning banner), Product Detail
  (confidence tiles, image card) — all render with the dark/neon theme
  applied and are still fully readable and functional; no console
  errors, no broken interactions.
- Full suite: **352 passed** (unchanged — a visual-only change has
  nothing for the existing data-layer tests to catch, and none needed
  to change).

## Visual pass, part 2: real cockpit-grid layout (not just theme colors)

Follow-up request: the first pass only recolored the existing stacked
layout, but the 3 reference images (generic stock-dashboard mockups) all
share a real structural pattern — a large "hero" visualization panel, a
side rail of compact stat cards, a bottom row of small chart panels, a
top HUD status strip — not just dark colors on the same single-column
page. Asked the user one clarifying question first: none of the
references' content maps to real data this project has (no per-country
sales breakdown exists for Amazon US dental listings), so a literal
world map would mean plotting fabricated geography. Confirmed: rebuild
the *layout composition* using only real data, no fake map.

- New shared layout primitives in `dashboard/components/theme.py`:
  `ticker()` (a horizontal HUD status strip of label/value chips,
  replacing a stacked row of `st.metric` for headline numbers),
  `panel_title()` (a compact glowing section label for dense grids),
  `rail_card()` (a compact stat card with an optional glowing
  proportion bar — the side-panel "sparkline" widgets from the
  references, built from numbers the page already computed, never a
  new calculation).
- **Root-cause color fix**, found while restructuring: Plotly Express
  bakes trace colors from `px.defaults.color_discrete_sequence` at
  figure-construction time for *every* bar/scatter trace, single- or
  multi-series — the earlier "recolor single-trace charts" band-aid
  from part 1 only caught the single-trace case; a real multi-series
  chart (Price Band × Product Type, colored by product type) was still
  rendering Plotly's stock blue/red/pink, not the neon palette. Fixed
  at the actual source instead of patching more call sites: `theme.py`
  now sets `px.defaults.color_discrete_sequence = CHART_COLORWAY` and
  `px.defaults.template = "plotly_dark"` once at import time, before
  any page constructs a figure. The old per-chart single-trace
  workaround in `style_chart()` was removed as redundant.
- **Market Map** (the page most literally named "map"): the existing
  price×demand bubble scatter — already spatial, glowing, colored by
  type — becomes the hero panel with a glowing marker outline added;
  a new stat rail alongside it shows Plotted count, Median Price,
  Total Observed Revenue, Leading Type, and No-Sales-Data count, each
  from data the page already loaded. The "no sales data" table moved
  into a collapsed expander (was a full-width divider section) and the
  product gallery is now a single bordered panel instead of a bare
  section, matching the rest of the grid.
- **Category Overview**: the 5 headline metrics became a top ticker
  strip; Product-Type Distribution (as a donut, `hole=0.45`) is now the
  hero panel with HHI concentration / leading brand / sales coverage as
  rail cards beside it; the remaining 4 charts (Price Distribution,
  Sales Distribution, Brand Distribution, Price Band × Product Type)
  moved from a stacked single column into a real 2×2 bordered panel
  grid.
- **app.py landing page**: the data-quality metrics became a ticker
  strip; a new donut of Relevant/Irrelevant/Uncertain listing
  composition (from the same `load_data_quality_summary()` numbers
  already shown elsewhere, no new query) is the hero "system status"
  panel, with each category as a rail card showing its product count
  and a proportion bar relative to total resolved products.
- **Deliberately left unchanged**: Product Detail, Listing Explorer,
  Data Quality Center, Evaluation Report. These are record/table
  drill-down pages (a single product's detail, a searchable listing
  table, a review queue) — forcing a "hero map + stat rail" cockpit
  grid onto a detail view doesn't match what the page actually is, so
  they keep the theming from part 1 without the layout rebuild. Flagged
  explicitly rather than silently skipped.
- **Verified in a real browser** on all 3 restructured pages (manual
  server workaround, same as elsewhere in this session): ticker strips,
  hero donuts/scatter, rail cards with proportion bars, and the 2×2
  panel grid all render correctly; caught and fixed the multi-series
  color bug by comparing a screenshot before and after the
  `px.defaults` fix.
- No test changes needed (pure presentation layer again). Full suite:
  **352 passed**.

## Milestone 16 — Data Operations Center Foundation

Requested as "M15 Data Operations Center foundation" — Milestone 15
already exists (Pipeline Orchestration), so this is Milestone 16
instead; flagged rather than silently reused. See DECISIONS.md for the
full design rationale, scope discipline, and the 3 real bugs found while
building this. Summary of what was built:

**1. Dataset Upload Interface** — new dashboard page
`7_Data_Ingestion_Center.py`: upload a SellerSprite XLSX, select
category/dataset type, assign a version (auto-suggested from
`dataset_versions`), preview the first 20 rows. Never writes to
production tables directly.

**2. Dataset Validation Engine** — `src/dmie/validation/dataset_validator.py`:
PASS/WARNING/FAIL report covering schema mismatch, missing ASIN,
duplicate ASINs, missing price/rating/sales/revenue values, invalid
prices/ratings, missing images. Every check mirrors a rule
`normalize.py` already enforces downstream.

**3. Dataset Version Management** — new `dataset_versions` table +
`src/dmie/database/datasets.py`: one permanent row per dataset that
actually reached `listings`, tracking source/version/category/timestamp/
record count. Separate from `dataset_uploads` (the upload lifecycle
tracker), same separation pattern as `pipeline_runs` vs
`classification_runs`.

**4. Connector Architecture** — `src/dmie/connectors/`: a minimal
`DataConnector` interface plus `sellersprite.py` (wraps the existing
Excel loader). Amazon API / Google Trends / web crawler connectors are
explicitly not built yet — foundation only, per the brief.

**5. Snapshot System** — `src/dmie/database/snapshots.py`'s
`take_snapshot()`: captures `product_market_metrics` into a new
`market_snapshots` row before and after every pipeline run.

**6. Change Detection Foundation** — same module's `detect_changes()`:
diffs two snapshots into `market_changes` rows (`NEW_PRODUCT`,
`REMOVED_PRODUCT`, `NEW_LISTING`, `PRICE_CHANGE`, `SALES_CHANGE`).
`scripts/run_pipeline.py` now takes a snapshot as its first stage and
another right after `market_calculation`, diffing the two automatically
— every pipeline run now reports "market changes detected since the
previous run" in its summary.

**7. Documentation** — `docs/data_platform_architecture.md`.

**8. Tests** — 43 new tests: `tests/unit/test_dataset_validator.py` (10),
`tests/integration/test_datasets_and_versions.py` (11),
`tests/integration/test_snapshots.py` (13),
`tests/unit/test_connectors.py` (4), plus 5 new cases in
`tests/test_run_pipeline.py` covering the new snapshot stages and the
`raw_xlsx` override.

**Verified for real**: staged, validated, and approved a copy of the
actual denture_base export through the full lifecycle — validation
reproduced the known real counts (106 raw rows, 1 duplicate ASIN, 105
final listings), the pipeline ran successfully with the new stages, a
real `dataset_versions` row was created, `market_changes` correctly
recorded zero changes against identical re-ingested data. Smoke-test
artifacts were deleted afterward so version history stays honest. Also
verified the new page renders correctly in a real browser (upload
widget, category/version selectors) and that every existing dashboard
page still works after the connection-config fix. Full suite:
**395 passed**.

## Post-Milestone-16 — System Completion Audit + Tier 1

Given a new 20-milestone "AI-powered global dental product intelligence,
consumer recommendation, and supply chain discovery platform" brief.
Per its own Milestone 0 instruction, produced `docs/system_completion_audit.md`
first and stopped for review before writing any code — full inventory
of every existing module against all 20 milestones, honestly flagging
that Milestones 7, 8, 12, 13, 15, 16, and 17 are blocked on data sources
(real review text, offline/manufacturer data, an org chart, a
consumer-facing surface, supplier data) that don't exist anywhere in
this repo, not on missing code. Recommended a 3-tier build order by
what's actually unblocked today. User approved proceeding with Tier 1.

**Tier 1 delivered** (all additive, no existing module rebuilt):

1. **CSV upload support** (Milestone 1 gap) — `dashboard/components/ingestion.py`
   gained `_read_dataset()`, dispatching on file extension (`.csv` via
   `pd.read_csv`, `.xlsx`/`.xls` via `pd.read_excel`); the uploader now
   accepts both. A staged CSV is converted to XLSX once, at approval
   time, when it becomes a permanent `data/raw/` file — the core
   `ingest.py`/`excel_loader.py` path never had to learn a second
   format.
2. **Aggregate dataset quality score** (Milestone 1 gap) —
   `ValidationReport.quality_score` (0-100, deterministic): FAIL → 0;
   otherwise 100 minus the average, across all 8 row-level data-quality
   dimensions (duplicate ASINs, 4x missing-value checks, invalid
   prices/ratings, missing images), of the fraction of rows each one
   flagged — a clean dimension contributes a real 0%, so one moderate
   issue among several otherwise-clean dimensions doesn't tank the
   score. New `dataset_uploads.quality_score DOUBLE` column (schema.sql
   + a one-time `ALTER TABLE`, same established pattern as M11 Stage
   3's `products` columns), surfaced as a metric on the Data Ingestion
   Center's Validation step and in Upload History.
3. **Price-Sales Matrix quadrant classification** (Milestone 14) —
   Market Map's existing bubble scatter now classifies each plotted
   product into Best Value / Premium / Budget / Poor Investment,
   split around the *currently plotted set's own* median price/sales
   (shown as dashed reference lines on the chart itself, never an
   opaque label), with a quadrant-count breakdown and a "Leading
   Quadrant" rail card. Computed dashboard-side, same precedent as
   Category Overview's existing Price Band × Product Type cross-tab.
4. **Brand market share + price positioning** (Milestones 6/10 gaps) —
   two new panels on Category Overview: a revenue-share donut per
   brand (excludes, never zeroes, products with no observed revenue),
   and a price-positioning table (each brand's median price vs. the
   category median, labeled Premium >120% / Budget <80% / Mid-range
   otherwise). Both computed dashboard-side from `load_product_metrics()`
   data already loaded — no new pipeline stage or schema field, same
   "compute the cross-tab in the dashboard page" precedent as #3.

**Verified**: full test suite (405 passed, 10 new — 5 quality-score
cases including the "average across all 8 dimensions, not just
triggered ones" fix caught during implementation, 5 CSV/XLSX dispatch
cases). All three UI changes checked in a real browser: Market Map's
quadrant expander and reference lines, Category Overview's brand donut
and positioning table (both showing real, honestly-labeled data),
Data Ingestion Center's quality-score metric and CSV-accepting
uploader. Also smoke-tested `stage_upload`→`validate_upload` end-to-end
for both an XLSX and a CSV version of the same real denture_base data —
identical quality_score (81.6) confirms the format dispatch doesn't
change validation results, as it shouldn't. Smoke-test artifacts
deleted afterward. The user's own locally-running dashboard had to be
stopped once (with notice) to apply the `ALTER TABLE` migration and run
the suite, then handed back for them to restart.

## Tier 2 — Product Knowledge Graph, Opportunity Score, AI Market Analyst

Continuing the system completion audit's recommended build order. Asked
the user one clarifying question before touching schema: Milestone 4
(Product Knowledge Graph) wants a Family -> Type -> Model -> Variant
hierarchy, but the pilot's real data (11 products, 3 real types) has no
natural family-level diversity to derive one from. User chose
"schema-only foundation" -- real, wired schema, deliberately left
unpopulated rather than inventing structure. See DECISIONS.md "Tier 2"
for full detail on all three deliverables.

**Milestone 4 (schema-only)**: inventoried the existing schema before
adding anything -- Category/Leaf Category, Product Type, and Variant
(via `product_listings` + each category's `variant_policy`) already
existed; `products.model` already existed too (unpopulated since M12).
Only `product_family` was genuinely new. Added it as a real, nullable
column, wired through `build_products()` (always `None` today, honestly
labeled on Product Detail), never a second/duplicate concept for
something that already had a home. `docs/product_knowledge_graph.md`
documents the full mapping and the two real paths to populate it later
(a category with genuine family diversity, or a frozen per-category
family-mapping config, same pattern as the product-type taxonomy).

**Milestone 9 (Opportunity Score)**: `src/dmie/opportunity/scoring.py`
aggregates each product's existing PRODUCT_IMPROVEMENT/BUNDLE/
CUSTOMER_PAIN_POINT signal rows (never the category-scoped ones) into
one 0-100 number, weighted by signal_strength and confidence, excluding
`insufficient_data` signals entirely rather than treating them as
absent (PRINCIPLES.md "None never 0"). New `products.opportunity_score`
column, computed in `scripts/detect_opportunities.py::run()` right
after signals are persisted (no re-query), shown on Product Detail.
Verified against the real pipeline: honestly `None` for all 11
denture_base products today, because `PRODUCT_IMPROVEMENT`/`BUNDLE`
both need more comparable-demand-data products than this pilot has --
confirmed as correct, not a bug.

**Milestone 18 (AI Market Analyst)**: new page + `src/dmie/ai/market_analyst.py`,
reusing `call_ai()` unchanged. `build_context()` assembles only real,
already-computed data (category metrics, per-product stats including
opportunity_score, active signals, data-quality counts) into a JSON
block the prompt explicitly forbids the model from going beyond --
written into `prompts/market_interpretation.md`, previously a
completely empty stub found during the system audit. Same graceful
degradation as every other AI stage: with no `ANTHROPIC_API_KEY`
configured, returns an "AI unavailable" result and still shows the real
context that would have been sent, rather than failing or faking an
answer. Verified live in the browser: real product data rendered
correctly in the context panel, unavailable-state message matched the
rest of the project's existing pattern exactly.

**Verified**: full test suite 430 passed (25 new -- 1 resolution test,
10 opportunity-scoring unit tests, 5 repository round-trip tests, 9
market-analyst tests mocking `call_ai` the same way
`test_ai_failure_handling.py` already established for classifier.py/
product_type_classifier.py). Two `ALTER TABLE` migrations applied to the
live DB (`products.product_family`, `products.opportunity_score`), same
established pattern as M11 Stage 3. Ran the real, unmocked pipeline
end-to-end afterward (`scripts/run_pipeline.py --category denture_base`)
-- all stages succeeded, opportunity scores correctly computed as
`None` for every real product. All three dashboard changes (Product
Detail's two new fields, the AI Market Analyst page) checked in a real
browser, not just assumed from code.

## Real AI enabled for the first time — AI Market Analyst live-verified

The user supplied a real `ANTHROPIC_API_KEY` specifically to verify the
AI Market Analyst end-to-end. Until this point, no AI stage in this
project had ever run against a real key (see every earlier milestone's
"AI unavailable / rules-only" notes) — this is the first genuine test
of the `call_ai()` layer against the real Anthropic API.

**Setup**: key written to a local `.env` (gitignored, confirmed via
`git check-ignore` before writing anything), `.env.example` populated
as a template, `anthropic`/`python-dotenv` installed and added as a new
`ai` optional-dependency group in `pyproject.toml`. `src/dmie/ai/client.py`
now loads `.env` automatically at import time (`override=False` — a
real environment variable still wins over the file). README updated
with setup instructions.

**Real bug found and fixed, the serious kind**: running the full test
suite immediately after (routine verification, not expected to find
anything) made `tests/integration/test_classification_pipeline.py::test_evaluates_all_50_gold_rows`
attempt **50 real, unmocked API calls** — its `report` fixture calls
`classify_listing()` directly with no AI mocking, which was always safe
only because no real key had ever existed in this environment before.
One of the real HTTPS connections triggered a **Windows fatal exception
(access violation)** in the httpx2/truststore SSL stack, hanging the
whole test run for 3+ minutes before crashing. Investigated with a
faulthandler stack trace (not guessed) to find the exact call chain:
`test_classification_pipeline.py` → `classify_listing` →
`classify_with_ai` → `call_ai` → real `anthropic` SDK → SSL handshake
crash. The same unmocked-AI-boundary gap existed in
`test_product_type_pipeline.py`, `test_resolution_pipeline.py`, and
parts of `test_product_type_classifier.py` — none of them had ever been
exercised with a real key before, so the gap was invisible until now.

**Root-cause fix**: new `tests/conftest.py`, one autouse, **session**-scoped
fixture (`pytest.MonkeyPatch()` directly, not the function-scoped
`monkeypatch` fixture — a session fixture can't depend on a
narrower-scoped one) that clears `ANTHROPIC_API_KEY` before the entire
test session. Session scope was required, not optional: a first attempt
using a function-scoped autouse fixture still didn't prevent the crash,
because `test_classification_pipeline.py`'s `report` fixture is
**module**-scoped, and pytest always sets up broader-scoped fixtures
before narrower ones a test needs — the function-scoped fixture simply
hadn't run yet when the module-scoped one made its real calls. Verified
the fix by reproducing the exact hang first, then confirming it was
gone. This restores every affected test to the deterministic,
rules-only behavior they were always designed around — tests that
specifically want to exercise the AI path
(`tests/test_ai_failure_handling.py`, and unit tests mocking
`classify_with_ai`/`ai_arbitrate` directly) set their own key back
within their own fixture scope, which correctly layers on top of the
session fixture's teardown.

**Real context gap found and fixed via an actual live question**: asked
the AI Market Analyst "what's the highest-priced product and how does
it compare to the category median?" — the real model correctly *refused*
to compute a median itself (per its grounding instructions) because
`price_distribution`/`sales_distribution` weren't included in
`build_context()`, even though both are already computed and sitting in
`category_market_metrics`. Added them, re-asked the identical question,
got a fully-grounded correct answer (`$77.00` vs. the real `$15.99`
median, `insufficient_data: False`). Pinned with a new test
(`test_build_context_includes_price_and_sales_distribution_for_median_questions`)
so this can't silently regress.

**Verified live in the browser** with the real key: asked "what's the
highest rated product?" through the actual dashboard page — got a
correct, real answer (P9b1126af38, HARMONYDENT, rating 5.0) matching
the real database. Full test suite re-run after the conftest fix: all
431 tests pass in ~18s (no hang, no crash) — same count as before,
since the fix restores behavior rather than changing what's tested.

## First real AI-enabled full pipeline run

`python scripts/run_pipeline.py --category denture_base --skip-reviews`,
real AI configured for the first time in this project's history
(`run_id c4258abe64a24f20944ee43a1cb0e4dd`, 576.01s — relevance
classification 194s, entity resolution 346s, both dominated by real,
sequential Anthropic API calls). Captured the exact pre-run rules-only
baseline first specifically to make this comparison possible, not
estimated after the fact.

**Relevance classification** (105 listings): of the 28 listings that
were `UNCERTAIN`/`needs_review` under rules-only, real AI resolved 17 —
13 became `RELEVANT` (`sample_for_qa` tier), 4 became `IRRELEVANT`
(`sample_for_qa` tier); 11 remain genuinely `UNCERTAIN`. Total
`RELEVANT` listings: 12 → **25**.

**Product-type classification**: 4 additional listings AI-classified as
`DB_RELINE` (`auto_accepted`); 1 remains `UNCERTAIN` even after AI.

**Entity resolution** (91 candidate pairs): of the 79 pairs that were
`UNCERTAIN` under rules-only (58 `fuzzy_composite`-tier + 21
`possible_pack_variant`-tier), AI arbitration resolved 55 of the 58
`fuzzy_composite`-tier ones — 12 `MATCH`, 43 `NO_MATCH` — leaving only 3
still uncertain. The 21 `possible_pack_variant`-tier pairs were
correctly left untouched: that guard routes straight to human review by
design (see the earlier "Fix: Entity Resolution Disjointness Logic"
entry), not through AI arbitration. Total `MATCH`: 11 → **23**. Total
`NO_MATCH`: 1 → **44**. Resolved products: 95 → **88** (more listings
correctly merged into fewer, more accurate products, not fewer products
found).

**Market metrics**: relevant-listing coverage nearly doubling unlocked
real sales/revenue data that rules-only classification had been
excluding by design (`aggregation.py` only includes `RELEVANT`
listings) — total observed monthly sales 100 → **1,300**, total
observed monthly revenue $449 → **$39,216**, HHI concentration 972 →
**464** (more real products now counted, less artificially
concentrated).

**Opportunity Score**: 0/11 products scored under rules-only (every
product-scoped signal was `insufficient_data`) → **4/23 scored** now,
including one real `36.0/100` score — the `min_products_for_percentile`
demand-data threshold (`config/thresholds.yaml`) finally has enough
comparable products to clear. Verified live on Product Detail.

**Market changes** (M16's own change-detection system, working exactly
as designed): `NEW_PRODUCT: 12` correctly detected between the
pre-run and post-run snapshots.

**2 real, expected test failures found and fixed** — not a code bug,
a real-data-changed-the-tests-that-hardcode-it situation, the same
pattern this project has handled before (`GOLD_SHA256_BEFORE`-style
checks elsewhere): `tests/integration/test_opportunity_pipeline.py`
hardcoded specific rules-only-era facts (old price-band gaps, an old
"11 priced products" confidence calculation) as literal expected
values. Recalibrated against the new real data (0 empty price bands
now, `UNDERREPRESENTED_PRODUCT_TYPE` is now the real signal_present
example instead of `PRICE_SEGMENT`), plus updated the docstrings on 2
more tests that still passed but referenced stale numbers, for the same
accuracy-over-time discipline this project has always held itself to.

**Verified live in the browser**: Evaluation Report's "AI-assisted"
match-breakdown metric is **60%** — the first time this number has ever
been non-zero, and the stale "AI-assisted is 0% because no
ANTHROPIC_API_KEY configured" caveat box is correctly gone now that the
condition it describes is no longer true. Product Detail for the newly
`36.0`-scored product renders correctly. Full suite: **431 passed**
after the 2 test recalibrations.

## Multi-category robustness pass (2026-09-23) — tested against a real ~200MB merged export

**Source**: a much broader real merged SellerSprite export supplied by
the user (`亚马逊合并总表`, ~30,000 rows across ~154 real raw dental/
dental-lab sub-categories) was used to (a) stress-test the pipeline
beyond the original single-category pilot scale, and (b) onboard two
new real categories: `dental_models` (428 raw listings) and `implants`
(279 raw listings), extracted via a new `scripts/extract_from_merged_export.py`.
The same file also contained a real, previously-unused `产品经理`
(Product Manager) column, covering all ~154 raw categories — loaded as
real Personnel Intelligence data (14 real employees, 153 real
(employee, raw-category) ownership rows; see `dmie.database.personnel`
and the new `dashboard/pages/9_Personnel_Intelligence.py`).

**Every one of the following was invisible with only one category
(`denture_base`) in the system, and would have caused silent data loss
or wrong results the moment a second real category was added — found by
actually adding one, not by inspection:**

1. **Every "regenerate from scratch" write in `src/dmie/database/repository.py`
   did an unscoped, table-wide `DELETE`** before inserting (`products`,
   `product_market_metrics`, `category_market_metrics`, `opportunity_signals`,
   `match_candidates`, `product_listings`, `listing_product_type_classification`,
   plus `update_best_listing_flags`/`update_opportunity_scores`). Running
   the pipeline for `dental_models` would have silently wiped every one
   of `denture_base`'s already-computed products, market metrics, and
   opportunity signals. Fixed: every one of these functions now takes
   `category` and scopes its `DELETE`/`UPDATE` to it (directly where the
   table has its own `category_id` column, via a `listings.category_id`
   join where it doesn't).
2. **`scripts/classify.py` and `scripts/resolve_products.py` queried
   `listings` with no `category_id` filter at all**, and `classify.py`
   never passed `category` into `classify_listing()` — every listing in
   the DB, regardless of category, was being relevance-classified
   against the `denture_base` definition. Fixed: both scripts now scope
   to the requested category.
3. **`rules.py`'s deterministic regex rules are curated specifically for
   `denture_base`** (its own docstring says so) but were being applied
   to every category unconditionally. Fixed: `classify_listing` only
   runs the rules stage for `denture_base`; every other category defers
   straight to the (already category-aware) AI stage — the exact
   "no hardcoded category assumptions" principle this project has always
   held itself to.
4. **The real performance bug behind an apparent "hang":** entity
   resolution over the real, grown `denture_base` table (22,801 raw
   listings — this project's own production data had grown well past
   the original 105-row pilot since M16) produces ~470,000 candidate
   pairs after blocking. `dmie.matching.resolution._variant_policy()`
   re-read and re-parsed `config/categories.yaml` from disk on **every
   single call** — invisible at pilot scale, but 470,471 redundant file
   reads at real scale. Measured runtime before the fix: 30+ minutes (a
   full test-suite run was killed once at 35 minutes still not done).
   Fixed with `@lru_cache` (the underlying config file never changes
   within a process's lifetime); the same redundant-read pattern was
   found and fixed the same way in `dmie.classification.classifier`'s
   and `dmie.classification.product_type_classifier`'s per-listing
   config loaders. Measured after the fix: the full 470,471-pair
   resolution run completes in ~264s.
5. **The real Anthropic API client had no timeout.** A single stuck
   network request blocked an entire `classify.py` run (hundreds of
   listings) for 50+ minutes with zero progress, zero visibility, and no
   way to distinguish "slow" from "dead" except killing the whole
   process — found live, during the first real multi-category
   classification run. Fixed: `anthropic.Anthropic(..., timeout=30.0)`
   in `dmie/ai/client.py`. Also added per-listing progress logging
   (flushed immediately) to `scripts/classify.py`, since a fully silent
   multi-hundred-listing run is itself a robustness gap — there was no
   way to tell it apart from a genuine hang until it was too late.
6. **A real, silent cross-category data-loss bug in ingestion.** The
   ~200MB merged export is not strictly category-exclusive — the same
   ASIN can legitimately appear under more than one raw category label
   (a cross-listed product, or overlapping source categorization).
   `upsert_listings()` used `listing_id` as the upsert key including
   `category_id` in the updated columns, so ingesting a second category
   whose file happened to share an ASIN with an already-ingested
   category silently reassigned that listing away from its original
   category — no warning, no log entry, just a quietly shrinking count.
   Found live: `denture_base` shrank from 22,801 to 22,135 real listings
   (−666) after ingesting `dental_models` and `implants`; `dental_models`
   itself shrank from 428 to 387 (−41, stolen by the `implants` ingest
   that ran after it). Fixed: `upsert_listings()` now detects a
   cross-category conflict per listing_id, leaves the existing row
   completely untouched, and returns it as a loggable `Decision`
   (`decision_type='category_conflict_skipped'`) instead of silently
   overwriting — `scripts/ingest.py` logs these and prints a warning.
   **Not retroactively repaired in this pass** (would need re-ingesting
   in a deliberately chosen precedence order under further real API/DB
   load, and this fix's priority was stopping further silent loss, not
   restoring the already-corrected-going-forward counts) — the live DB
   currently reflects this real discovered state (`denture_base`:
   22,135; `dental_models`: 387; `implants`: 279), which is honest and
   internally consistent, just short of each raw file's original count.
7. **Two straightforward regressions from the fixes above** (caught by
   the full suite, not shipped silently): 3 call sites in
   `tests/integration/test_product_type_pipeline.py` were missed when
   `replace_listing_product_type_classifications()` gained its new
   `category` parameter; `test_load_data_quality_summary_matches_known_real_counts`
   hardcoded a stale `total_listings == 105` literal that broke once
   `denture_base` grew — rewritten to assert against a live ground-truth
   query instead of a frozen literal, since that count is now expected
   to keep growing as more categories are onboarded.

**Real AI relevance classification for `dental_models`/`implants` was
deliberately not run to completion in this pass** — the timeout/hang
discovered in finding #5 above consumed a meaningful amount of real
API budget across two interrupted attempts before the root cause was
found and fixed, and the user asked to stop further large real-API runs
given limited remaining credits. The pipeline is now verified correct
and fast (all bugs above fixed and covered by new/updated tests) and
ready to classify both categories in a single clean run whenever API
budget allows — `python scripts/classify.py dental_models` then
`python scripts/classify.py implants`, each now with live progress
output and a 30s-per-call timeout instead of an unbounded one.

**New test coverage added this pass**: `tests/unit/test_personnel.py`
(5 tests, Personnel Intelligence data loading), a new regression test
in `tests/integration/test_ingest_pipeline.py` for the cross-category
conflict behavior (#6 above). Full suite re-verified passing after every
fix in this list (see DECISIONS.md for the exact before/after counts).

## dental_models: first full real pipeline run for a second real category (2026-09-24)

**The point of this run**: prove the multi-category fixes above actually
work end to end on a second real category, not just in isolated tests --
per the architecture directive's Section VII/XI, a milestone isn't done
because it works for `denture_base`. Every stage below ran against real
data with zero category-specific code changes -- only `config/categories.yaml`,
`config/taxonomy/dental_models_v1.yaml`, and `config/product_type_signals.yaml`
are category-specific, exactly as the architecture requires.

**A real, previously-unknown bug found running this**: `dmie.ai.client`'s
`anthropic.Anthropic(timeout=30.0)` (added earlier this pass) did not
actually bound a stuck connection -- a single listing hung for **2.8
hours** despite the timeout, because the stall happens in the OS network
stack below where httpx's timeout logic ever starts counting (Windows-
specific). Fixed with a hard wall-clock watchdog: `call_ai` now submits
the request to a background thread pool and calls `.result(timeout=35)`,
which bounds the call regardless of where the underlying stall actually
is (the blocked thread leaks rather than the whole batch blocking for
hours -- an accepted tradeoff, documented in `dmie/ai/client.py`).
Also found and fixed: `scripts/classify.py` only wrote results to the
DB once at the very end of a run -- any interruption (the above hang, a
crash, a kill) lost every already-computed result along with it. Now
flushes every 20 listings.

**Relevance classification (real AI, real cost)**: 387 raw listings ->
**299 RELEVANT / 72 IRRELEVANT / 16 UNCERTAIN**. Two individual listings
hit real `APIConnectionError`s during the run; both recovered within
under a minute thanks to the hard-timeout fix (previously: hours).

**Taxonomy**: `product_type_discovery.py`'s deterministic clustering
produced one useless 299-listing supercluster for this category (a
near-universal connector word, same class of failure denture_base's own
"base" word could cause -- see that module's docstring) even after
sweeping `max_prevalence` down to 0.15 and `min_shared_listings` up to
5. Rather than ship a useless taxonomy, all 299 real titles were read
directly and grouped by genuine product function into 6 types (typodont/
teaching models, implant training models, phantom head manikins, lab
fabrication supplies, 3D-printer resin, mounting accessories) --
`config/taxonomy/dental_models_v1.yaml`, `status: draft_autonomous`
(no human reviewer was available -- see DECISIONS.md). Product-type
classification against this taxonomy: **192/299 confidently rule-matched
(144 typodont, 23 lab supply, 11 resin material, 9 implant model, 5
manikin), 107 honestly deferred to review** (no AI budget remained by
this point in the session -- the rules-only fallback degrades to
`UNCERTAIN`/`needs_review`, exactly the designed behavior, not a crash
or a guess).

**A second real bug found running entity resolution**: `product_id` is
a global primary key derived purely from a listing_id's hash
(`dmie.matching.resolution.cluster_products`), not scoped to category.
A listing that migrated category (the cross-category collision bug
above) left a **stale `products` row under its old category**, rooted
on the same listing_id -- `replace_products`'s category-scoped `DELETE`
never touched it, so the new INSERT hit a real `PRIMARY KEY` violation.
Fixed: `replace_products` now also deletes any existing row whose
`product_id` collides with an incoming one, regardless of that row's
own category (safe, since a product_id collision only happens when the
underlying listing has already moved category, making the old row
definitionally stale).

**Entity resolution**: 387 listings -> **307 real products** (129
listings correctly clustered into multi-listing products via
deterministic brand/title blocking + composite scoring; 124 pairs
auto-MATCHed, 3 auto-NO_MATCH, 1521 pairs correctly deferred to
`needs_review` since AI arbitration wasn't available).

**Market metrics** (RELEVANT-scoped, 244 of the 307 products):
total observed monthly sales **2,900**, total observed monthly revenue
**$52,963**, HHI **54.9** (an extremely fragmented market -- no
concentration at all), price range $4.99-$399.99, median $22.41.

**Opportunity detection**: 495 signals computed across 244 products;
**25 products received a real opportunity score** (219 remained
`insufficient_data` for BUNDLE, all 244 `insufficient_data` for
PRODUCT_IMPROVEMENT -- same root cause as denture_base, zero review
insights exist in this data source); 1 real `signal_present`
(`UNDERREPRESENTED_PRODUCT_TYPE`).

**`implants` was intentionally left incomplete.** Relevance
classification reached only 20/279 listings (0 RELEVANT in that
partial sample) before the user's API budget ran out mid-run; the
process was stopped cleanly rather than continuing to burn a budget
that no longer existed. No taxonomy, product-type classification,
resolution, or market/opportunity data exists for `implants` --
running `python scripts/classify.py implants` to completion is the
entire remaining blocker, everything downstream is already fixed and
verified working on `dental_models`.

**Two more real bugs found only by opening the actual dashboard** (not
by inspection -- `preview_start` + live browser verification against
the real dental_models data above):

1. **A new category could be fully onboarded and still be completely
   invisible everywhere.** `config/categories.yaml` (relevance/taxonomy
   config) and the `categories` DB table (what the dashboard's and
   API's category picker actually reads) had no sync mechanism at all
   -- `dental_models` and `implants` were ingested, classified,
   resolved, and scored, and still didn't appear as an option anywhere,
   with no error. Fixed: `dmie.database.repository.upsert_category()` +
   `scripts/ingest.py` now syncs the DB row from config on every ingest
   (raises a clear error if the category isn't in config at all, rather
   than silently skipping). Applied retroactively for `dental_models`/
   `implants`; verified live -- both now appear in Category Overview's
   dropdown with correct real numbers (244 products / 299 listings /
   $52,963 revenue for dental_models).
2. **Personnel Intelligence page crashed on its last section** (`Who
   owns this onboarded category?`) with `TypeError: '<' not supported
   between instances of 'str' and 'float'`. pandas represents SQL NULL
   as `NaN` (a float) even in an object/string column; the page's
   `if row["matched_category_id"]` truthy check doesn't filter NaN out
   (`bool(float('nan'))` is `True` in Python), so NaN values leaked into
   a set alongside real category-id strings, and `sorted()` then failed
   comparing `str < float`. Fixed with `pd.notna()`. Verified live: the
   page now renders fully, including a working "onboarded category"
   selector showing `dental_models` with its real owner (覃柳敏).

Full dashboard verified live end-to-end for `dental_models`: Category
Overview (244 products, 299 listings, $52,963 revenue, real product-type/
brand/price distributions), Personnel Intelligence (14 employees, real
ownership data, no crashes), Data Quality Center (22,801 total listings
across all 3 categories, 324 RELEVANT, consistent with the direct SQL
counts reported above).


## Phase 2 — Universal Market Intelligence Engine (2026-09-24)

New offline, schema- and category-agnostic engine layered on top of the
Phase-1 prototype (nothing in Phase 1 was removed or changed, apart from
registering one new API router). Audit and architecture:
`docs/universal_engine_architecture.md`.

- **Modules built:** universal ingestion + `schema_detector.py` (M1),
  data quality with IQR / robust z-score / Isolation Forest (M2), TF-IDF
  dental relevance with a human-correction feedback loop (M3), DBSCAN/K-Means
  segment discovery (M4), product identity graph dedup (M5), market capacity
  (M6), MA/Holt/ARIMA forecasting (M7), opportunity score (M8), lexicon review
  pain analysis (M9), Monte Carlo simulation (M10), NetworkX knowledge graph
  with Cypher export (M11), supplier scoring (M12), employee ownership links
  (M13), 3D dashboard pages 10–13 (M14), consumer recommendations (M15),
  optional AI analyst with an offline fallback (M16).
- **Real-data runs (`scripts/run_engine.py --all-raw`):** dental_models
  428 records → 397 relevant → 394 current listings → **306 products** in
  27 segments (Phase-1 got 307 products independently); denture_base
  106 → 103 relevant → 86 products / 9 segments; implants 279 → 180
  products / 20 segments. ~6 s per market.
- **Synthetic validation (tests/engine):** a micromotor dataset with
  unfamiliar English headers across 8 months. The engine rediscovered the
  planted segments (brushless 60k rpm / brushless 50k rpm / brushed 35k rpm
  N3-H37L1 / wax / trays), merged a planted duplicate listing, kept 50k and
  60k motors apart (spec veto), forecast +66% for the planted-growth segment
  and −8% for the planted-decline one, and surfaced "overheating → improved
  cooling" from reviews.
- **UI verification:** Playwright drove an actual CSV + reviews upload
  through page 10, then every tab of pages 10–13. Zero Streamlit exceptions.
  Bugs found and fixed this way: NaN edge weights collapsing the 3D layout;
  `$…$` amounts rendered as LaTeX; the market picker not switching to a
  new run; the simulator extrapolating elasticity far outside the observed
  price range.
- **Tests:** 13 new smoke tests pass. The full suite gives 476 passed; the
  same 45 DB-dependent integration tests fail here as before this change
  (they need a locally built `database/dmie.duckdb`, which is gitignored).
- **Data finding (not changed; raw data is immutable):**
  `data/raw/dental_models/dental_models_sellersprite_v1.xlsx` is
  byte-identical to `data/raw/denture_base/denture_base_sellersprite.xlsx`.
  `run_engine.py --all-raw` therefore prefers `<folder>_sellersprite.xlsx`.
- **Honest limits:** SellerSprite sales are missing for 66–91% of rows, so
  market totals are "observed", with coverage reported. Segments with no
  observed sales are capped at an opportunity score of 45. There are no
  forecasts until ≥3 dated snapshots exist (every run with
  `--snapshot-date` adds one). The pain analysis and supplier tables stay
  empty until review and supplier files are provided.
