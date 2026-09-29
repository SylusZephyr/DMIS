# DMIE Full System Audit Report — Phase 1 (Repository Inspection)

**Scope of this document:** Phase 1 only (repository inspection). No code
was modified while producing this report. Phases 2-11 have not been run
yet.

## 0. Critical context correction — read this first

The task's stated "Current Verified Status" says completed work is
**M1-M10 (prototype) + M11 (taxonomy discovery/freeze) + M12 (Product
Master)**. That is not the actual state of this repository. As of this
audit, git history (`git log --oneline`, working tree clean) shows:

```
2e56716 M14: run versioning and data provenance
c224af8 M13: AI reliability layer
a13661e M11: product type classification completed   (Stage 3)
9e1fa20 M11 Stage 2: product type classifier built and evaluated (+ M12, M11 Stage 1/freeze backlog)
e6fed3f Baseline: M1-M10 prototype complete
```

**Actually complete and committed:** M1-M10, M11 (all 3 stages — discovery,
taxonomy freeze, classifier, and Product Master type integration), M12,
M13 (AI reliability layer), M14 (run versioning/provenance).

This audit report describes the **real, current state**, not the M1-M12
baseline described in the task prompt. If the intended release is meant
to be scoped strictly at M12 (before the classifier/AI-reliability/
run-versioning work), that requires a decision from you before Phase 11 —
it would mean releasing from an earlier commit (`9e1fa20` or earlier),
not `HEAD`. Flagging this now rather than silently auditing against the
wrong baseline.

## 1. Architecture overview

```
raw SellerSprite .xlsx (data/raw/denture_base/)
        |
        v
scripts/ingest.py  --------------------->  listings, categories
        |
        v
scripts/classify.py  --------------------> listing_classification (relevance)
        |
        v
scripts/classify_product_types.py  ------> listing_product_type_classification  [M11 Stage 2]
        |
        v
scripts/resolve_products.py  ------------> match_candidates, product_listings, products  [M12/M11 Stage 3]
        |
        v
scripts/calculate_market.py  ------------> product_market_metrics, category_market_metrics
        |
        v
scripts/analyze_reviews.py  -------------> review_insights
        |
        v
scripts/detect_opportunities.py  --------> opportunity_signals
        |
        v
scripts/build_dashboard_data.py  --------> (dashboard reads DuckDB directly, read-only)
        |
        v
dashboard/app.py + dashboard/pages/1-6  -> Streamlit UI
```

Cross-cutting: `src/dmie/ai/client.py::call_ai()` (M13) is now the single
choke point every AI-using module (`classifier.py`,
`product_type_classifier.py`, `matching/resolution.py`,
`reviews/extraction.py`) calls through. `src/dmie/database/runs.py`
(M14) is a parallel, append-only run-log that every script above writes
to (`pipeline_runs`/`classification_runs`/`product_resolution_runs`)
without changing how the tables above it are written.

**Package layout** (`src/dmie/`): `ai/`, `classification/`, `cleaning/`,
`database/`, `ingestion/`, `market/`, `matching/`, `opportunity/`,
`reviews/`, `utils/`. 33 source `.py` files.

**Config** (`config/`): `categories.yaml` (per-category relevance
definition + variant policy), `taxonomy/denture_base_v1.yaml` (frozen
product-type taxonomy), `product_type_signals.yaml` (classifier keyword
signals — deliberately separate from the frozen taxonomy), `thresholds.yaml`
(confidence routing for relevance/product-type/entity-resolution/
opportunity), `review_taxonomy.yaml` (pain-point taxonomy),
`settings.yaml` (**empty, 0 bytes** — see §6).

**Dashboard**: 6 Streamlit pages (Category Overview, Market Map, Product
Detail, Listing Explorer, Data Quality Center, Evaluation Report) +
`dashboard/components/data.py` (the DB access layer). No `launch.json`
exists in the repo, and README does not document the launch command —
see §6.

**Tests**: 30 test files, `tests/unit/` + `tests/integration/` + 2 files
at `tests/` root (`test_ai_failure_handling.py`,
`test_product_type_classifier.py` — the two most recently added, placed
per the literal instruction that created them rather than the
`unit`/`integration` convention every other file follows).

## 2. Completed milestone verification (real, not assumed)

| Milestone | Status | Evidence |
|---|---|---|
| M1-M10 | Complete | Tag `prototype-m10-baseline`, commit `e6fed3f` |
| M11 Stage 1 (discovery) | Complete | `docs/product_taxonomy.md`, `product_type_discovery.py` + 8 tests |
| M11 taxonomy freeze | Complete | `config/taxonomy/denture_base_v1.yaml` (`status: frozen`), `docs/taxonomy_denture_base_v1.md` |
| M11 Stage 2 (classifier) | Complete | `product_type_classifier.py`, `prompts/product_type_classifier.md`, evaluated 11/12 on confirmed-relevant listings |
| M11 Stage 3 (Product Master integration) | Complete | `products.product_type`/`product_type_confidence`/`product_type_conflict` populated for real (see §3) |
| M12 (Product Master) | Complete | `products` table populated (92 rows), dashboard reads from it |
| M13 (AI reliability) | Complete | `src/dmie/ai/client.py`, all 4 AI call sites refactored, 14 tests |
| M14 (run versioning) | Complete | `pipeline_runs`/`classification_runs`/`product_resolution_runs`, `docs/pipeline_runs.md` |

**Not started:** M15 (second category — blocked, no micromotor raw data
exists), M16 (pipeline orchestration script), M17 (infra cleanup), M18
(CI), M19 (dashboard data quality re-verification post-M14), M20
(business validation report).

## 3. Current database state

Live `database/dmie.duckdb`, checked directly:

| table | rows | notes |
|---|---|---|
| `categories` | 1 | denture_base only |
| `listings` | 105 | raw normalized listings |
| `listing_classification` | 105 | relevance decisions |
| `listing_product_type_classification` | 12 | M11 Stage 2 output — RELEVANT listings only, correctly scoped |
| `match_candidates` | 91 | entity-resolution audit trail |
| `product_listings` | 105 | every listing has exactly one product_id |
| `products` | 92 | Product Master — 1 row per resolved product |
| `product_market_metrics` | 11 | market-scoped (RELEVANT) products only |
| `category_market_metrics` | 1 | **stale — see §7 Bug #1** |
| `opportunity_signals` | 29 | **partially stale — see §7 Bug #1's cascade** |
| `review_insights` | 0 | expected — SellerSprite has no review-text field (documented, `docs/methodology.md`) |
| `decision_log` | 1 | |
| `pipeline_runs` | 4 | M14, append-only |
| `classification_runs` | 3 | M14, append-only |
| `product_resolution_runs` | 1 | M14, append-only |

No orphan-record check has been run yet (that's Phase 3). No obvious
orphans expected given `product_listings`'s 1:1-listing coverage (verified
by an existing test, `test_every_listing_has_exactly_one_product_listings_row`),
but this needs a real query pass, not an assumption.

## 4. Current pipeline state

All scripts exist and were run for real against the live DB during
M12-M14 development (not just tested in isolation):
`scripts/ingest.py`, `classify.py`, `classify_product_types.py`,
`resolve_products.py` are all mutually consistent as of the last run of
each. **However**, `calculate_market.py`, `analyze_reviews.py`, and
`detect_opportunities.py` were last run **2026-09-19**, i.e. **before**
`classify_product_types.py`/the Stage-3 `products.product_type` population
ever existed (2026-09-21). Their output does not reflect current
classification state. This is not a "the pipeline is broken" finding —
it's "the pipeline has not been re-run end-to-end since Stage 3," which
Phase 4 exists to fix. See §7 Bug #1 for why re-running alone won't fix
it.

No pipeline orchestration script exists yet (M16) — every stage above is
still triggered manually, in a specific required order, with no
single-command "run everything" path.

## 5. Known limitations (already documented elsewhere, restated for completeness)

- No `ANTHROPIC_API_KEY` has ever been configured in this environment.
  Every AI-dependent path (relevance AI stage, product-type AI stage,
  entity-resolution arbitration, review extraction) has only ever run
  its `None`/unavailable branch. M13 made the *failure handling* around
  these calls robust and tested — it did not and could not test them
  against a real model response, because none has ever been made.
- `review_insights` is permanently empty — SellerSprite's export has no
  review-text field at all (not a bug, a source-data limitation,
  documented in `docs/methodology.md`).
- M11 Stage 2's classifier has only been run against the 12 (+2
  extended-gold) confirmed-relevant listings, never the full dataset via
  a script prior to `classify_product_types.py`'s creation — it has now
  been run for real (§3), but only for `denture_base`.
- Single-category pilot only (`denture_base`). `micromotor` exists in
  `config/categories.yaml` labeled explicitly as "illustrative example
  only — no raw export has been ingested."

## 6. Missing functionality

- `config/settings.yaml`, `src/dmie/utils/config.py`,
  `src/dmie/utils/logging.py`, `.env.example`, `CHANGELOG.md` are all
  **literally 0 bytes** — placeholder stubs from early milestones, never
  filled in. Nothing currently imports/reads `utils/config.py` or
  `utils/logging.py` (confirmed: no logging module is used anywhere
  except M13's `logging.getLogger("dmie.ai")`, which doesn't touch this
  stub at all — a second, disconnected logging surface now exists).
- No documented dashboard launch command. README's "Project layout"
  section mentions `dashboard/` exists but never gives the actual command
  (`streamlit run dashboard/app.py`) or a required port. No
  `launch.json` is committed either.
- No pipeline orchestration script (`scripts/run_pipeline.py` — M16 is
  explicitly the milestone for this; not started).
- No CI configuration exists anywhere in the repo (M18, not started).
- No second-category validation has occurred (M15, blocked on missing raw
  data — see §0/§5).

## 7. Potential bugs

### Bug #1 — **FIXED 2026-09-21** — `category_market_metrics.product_type_distribution` and the `UNDERREPRESENTED_PRODUCT_TYPE` opportunity signal read a source that Stage 3 made permanently empty

**Status: fixed and re-verified against the live DB.** See
`DECISIONS.md` "Bug fix — `category_market_metrics.product_type_distribution`
was permanently stale" and `PROGRESS.md` for the full before/after. Left
the original finding below for the audit trail.

`src/dmie/market/aggregation.py::fetch_relevant_listings` (line ~68) still
does:

```sql
SELECT l.listing_id, ..., lc.product_type
FROM listings l
JOIN listing_classification lc ON lc.listing_id = l.listing_id
...
```

`listing_classification.product_type` has been permanently `NULL` for
every row since M11 Stage 3 moved real product-type output to its own
table (`listing_product_type_classification`) specifically to avoid
colliding with `listing_classification`'s existing columns (see
`DECISIONS.md` "M11 Stage 3"). `market/aggregation.py` was never updated
to read from the new table. Verified directly against the live DB:

```
category_market_metrics.product_type_distribution = {"unclassified": 11}
```

— even though `products.product_type` is correctly populated for 10 of
11 market-scoped products right now. This is a real regression I
introduced during M11 Stage 3: `matching/resolution.py::build_products()`
and `scripts/resolve_products.py` were both correctly updated to the new
table; `market/aggregation.py`'s independent, separate product-type read
(used only for the category-level histogram) was missed.

**Downstream cascade:** `scripts/detect_opportunities.py` feeds this same
stale `product_type_distribution` into
`detect_underrepresented_product_type_signal()`
(`src/dmie/opportunity/signals.py`), so any `UNDERREPRESENTED_PRODUCT_TYPE`
signal currently in `opportunity_signals` (created 2026-09-19, before
Stage 3 existed) is based on data that was already stale the moment Stage
3 shipped, and would *still* be wrong even after a fresh re-run, until
this is fixed.

**Not fixed in this phase** per the explicit "do not modify code yet"
instruction for Phase 1. Recommend fixing in Phase 4 (pipeline re-run) or
as its own confirmed-bug fix in Phase 8, not bundled with anything else.

### Bug #2 (needs Phase 3 verification, not yet confirmed)

`review_insights.product_id` — with 0 rows in the table, there's nothing
to check yet, but the schema comment on `opportunity_signals` says
`product_id IS NULL` is valid for category-level signals; whether
`review_insights` rows (once any exist) would ever reference a
`product_id` that doesn't exist in `products` (e.g. a product that was
later merged/re-clustered by a subsequent `resolve_products.py` run) is
an untested edge case. Flagged for Phase 3, not confirmed as a bug.

### Bug #3 (needs Phase 6/7 verification)

The dashboard was last manually verified in a real browser after M12
(before Stage 3/M13/M14 existed). It was **not** re-verified in a browser
after Stage 3 populated real `product_type` values, after M13's AI-call
refactor, or after M14's schema additions. A SQL-level sanity check was
done for the Stage 3 query shape and returned correct results, but that
is not the same as loading the actual pages. Flagged for Phase 6/7, not
confirmed as a bug either way.

## 8. Technical debt

- Two test files live at `tests/` root
  (`test_ai_failure_handling.py`, `test_product_type_classifier.py`)
  instead of `tests/unit/` or `tests/integration/`, breaking the
  otherwise-consistent convention every other test file follows. Cosmetic,
  zero functional impact (pytest discovers them fine either way).
- `src/dmie/utils/config.py`/`utils/logging.py` are dead stub files —
  either build them out or delete them; right now they're pure clutter
  that could mislead a future contributor into thinking config/logging
  goes through them when it doesn't (`call_ai()` uses stdlib `logging`
  directly with its own logger name).
- No `.gitattributes` — every commit this session emitted
  `warning: LF will be replaced by CRLF` for every touched file. Harmless
  today (single-machine, single-contributor), but real friction the
  moment a second machine or contributor touches this repo.
- `market/aggregation.py` and `matching/resolution.py` both independently
  compute/read "product type for this group of listings" (Bug #1) — two
  code paths for one concept that have now demonstrably drifted out of
  sync once. Worth consolidating into one shared helper when Bug #1 is
  fixed, so this can't happen a second time silently.
- No pipeline orchestration (M16) means the *order* dependencies between
  scripts (classify → classify_product_types → resolve_products →
  calculate_market → ...) are enforced only by a human remembering to run
  them in order — Bug #1's cascade is a direct, concrete consequence of
  that gap (nothing forced `calculate_market.py` to be re-run after
  `classify_product_types.py` was introduced).

## 9. UI risks

(Full verification is Phase 6/7; this is what's visible from static
inspection alone.)

- `dashboard/components/data.py::load_product_metrics` joins `products`
  for `product_type`/`brand`/`product_name`/`representative_image` — this
  path is correct and was verified in a real browser after M12. It does
  **not** yet surface `product_type_confidence` or `product_type_conflict`
  (new in Stage 3) anywhere in the UI — not a bug (nothing asked for
  this), but worth naming as a real gap between what the Product Master
  now knows and what the dashboard shows.
- `dashboard/pages/1_Category_Overview.py` reads
  `category_market_metrics.product_type_distribution` — per Bug #1, this
  will currently render `{"unclassified": 11}` regardless of the real,
  correct classification state sitting one table over in `products`. This
  is a visible, user-facing symptom of Bug #1, not a separate UI bug.
- Market Map page's product gallery/hover data was verified working after
  M12; has not been re-checked since.

## 10. Recommended fixes (not applied — Phase 1 is inspection only)

Ordered by what Phase 4/8 should address, most impactful first:

1. ~~Fix Bug #1~~ **DONE** — `fetch_relevant_listings` now reads
   `listing_product_type_classification`; `calculate_market.py` and
   `detect_opportunities.py` re-run against the live DB and verified
   correct.
2. Re-run the remaining pipeline stages (`analyze_reviews.py` — currently
   a no-op since `review_insights` has 0 rows and no source review text
   exists; `build_dashboard_data.py`) so every derived table is
   consistent with the current code, not a mix of Sep-19 and Sep-21 runs.
   Market/opportunity tables are now current as of this fix; reviews have
   nothing to recompute.
3. Re-verify the dashboard in a real browser (Phase 6/7) — specifically
   Category Overview's product-type chart (will only be meaningful once
   fix #1 lands) and the Market Map/Product Detail pages post-M13/M14.
4. Decide on and document the actual dashboard launch command in README
   (currently missing entirely).
5. Either delete or genuinely implement `utils/config.py`/`utils/logging.py`/
   `settings.yaml`/`CHANGELOG.md` — leave them as either real or gone, not
   permanently empty.
6. Relocate the two root-level test files into `tests/unit/`/
   `tests/integration/` for consistency (cosmetic, low priority).

---

**Phase 1 status: complete.** Bug #1 was subsequently fixed (see above,
and `DECISIONS.md`/`PROGRESS.md` for the full record) per explicit
instruction. Proceeding to Phase 2.

## Phase 2 — Environment Check

**Environment status: PASS WITH ISSUES** (not a clean PASS — two real
reproducibility gaps found, neither a secret/security problem).

| Check | Result |
|---|---|
| Python version | **PASS** — 3.14.5 installed, `pyproject.toml` requires `>=3.10` |
| Core dependencies installed | **PASS** — `duckdb` 1.5.5, `PyYAML` 6.0.3, `pandas` 3.0.6, `openpyxl` 3.1.5, `pytest` 9.1.0, `streamlit` 1.64.0, `plotly` 7.1.0 — all importable |
| Virtual environment | **ISSUE** — none exists; dependencies are installed into the global user site-packages (`.gitignore`'s own comment confirms this was a deliberate, known state, not an oversight, but it's still a gap for "runs on another machine") |
| Config files valid | **PASS** — all `config/*.yaml` files parse and load correctly (confirmed implicitly: every pipeline script that reads them ran without a YAML error during Phase 1's live-DB checks); `config/settings.yaml` is a 0-byte placeholder, not a parse failure |
| Environment variables | **PASS** — exactly 2 used anywhere in the codebase (`grep`-verified): `ANTHROPIC_API_KEY` (optional, every AI call site degrades gracefully to `None` when unset) and `DMIE_DB_PATH` (optional, defaults to a repo-relative path via `Path(__file__).resolve().parents[3]` — not hardcoded). Neither is required to run the system. |
| `.env` / `.env.example` | **PASS on secrets, ISSUE on documentation** — no real `.env` file exists anywhere (confirmed) and none is tracked in git; `.env.example` is a 0-byte placeholder that should list `ANTHROPIC_API_KEY=` (optional) and `DMIE_DB_PATH=` (optional) but currently documents nothing |
| No secrets exposed | **PASS** — `grep`-scanned every `.py`/`.yaml`/`.md` file for API-key/secret/password literal patterns; zero matches. No `.duckdb`, `.env`, or credential file is tracked in git (`git ls-files` checked directly) |
| No hardcoded local paths | **PASS** — `grep`-scanned for `C:\Users`, `/home/`, `/Users/` across `src/`, `scripts/`, `dashboard/`, `config/`, `tests/`, `prompts/`; zero matches. `PROJECT_ROOT` is computed dynamically everywhere it's used |
| **Runs on another machine, following the README exactly** | **ISSUE** — README's only documented install command is `pip install -e ".[dev]"`. `streamlit`/`plotly` live under a separate `[dashboard]` extra in `pyproject.toml` that README never mentions — a fresh clone following the README literally would install the pipeline but **could not run the dashboard** without independently discovering `pip install -e ".[dev,dashboard]"`. |

**Recommended fixes (not applied — these are environment findings, not
confirmed functional bugs, so left for a dedicated fix pass):**
1. Add the `[dashboard]` extra to README's setup instructions.
2. Populate `.env.example` with the 2 real, optional variable names.
3. Consider documenting (not necessarily enforcing) a `venv` step in
   README for reproducibility, even though the current global-install
   workflow does function correctly today.

Neither issue blocks the pipeline or tests from running — both only
affect a *fresh* setup following the README literally, which is exactly
why they weren't caught until this audit.

---

Awaiting direction on Phase 3 (Database Validation) vs. addressing the 3
environment recommendations above first — both are quick, low-risk
documentation/config fixes if you'd rather clear them now.
