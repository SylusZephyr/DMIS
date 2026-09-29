# DMIE Final Architecture Review (Phase 0)

**This document is a synthesis, not a re-inspection.** The line-by-line
repository inspection this review is based on was already done and
recorded in `docs/full_system_audit_report.md` (git history, table row
counts, config/test/dashboard/script inventories, a confirmed-and-fixed
bug, and a Phase 2 environment check) earlier in this session. Re-running
every `grep`/`find`/DB query a second time under a new filename would
duplicate that work for no new information — this document instead states
what's actually true right now, post-fix, and exists so "Phase 0" has its
specifically-named deliverable. See `docs/full_system_audit_report.md` for
the granular evidence trail (table-by-table row counts, per-check
environment results, the full bug writeup).

## Read before writing this: PRINCIPLES.md, README.md, PROGRESS.md, DECISIONS.md

All 4 confirmed current and internally consistent with the actual repo
state as of commit `39b8ebc` (verified: clean `git status`, 326/326 tests
passing).

## 1. Completed components — verified, not assumed

| Component | Milestone | Verified how |
|---|---|---|
| Ingestion / normalization | M1-M3 | Existing test suite (`test_ingest_pipeline.py`) |
| Relevance classification (rules + AI fallback) | M5 | Live DB: 105 listings classified (12/65/28) |
| Entity resolution (candidate gen → scoring → clustering → AI arbitration) | M6 | Live DB: 91 candidate pairs, 92 resolved products |
| Market engine | M7 | Live DB: `product_market_metrics` (11 rows), `category_market_metrics` (1 row, **now correct** post-fix) |
| Review extraction | M8 | Code path exists and is tested; 0 real insights (source data has no review text — a data limitation, not a code gap) |
| Opportunity engine | M9 | Live DB: 29 signals, re-verified post-fix |
| Dashboard | M10 | 6 Streamlit pages, last browser-verified after M12 |
| **Product taxonomy discovery + freeze** | M11 Stage 1 | `config/taxonomy/denture_base_v1.yaml` (`status: frozen`), `docs/taxonomy_denture_base_v1.md` |
| **Product-type classifier (rules → AI → review queue)** | M11 Stage 2 | `src/dmie/classification/product_type_classifier.py`, evaluated 11/12 on confirmed-relevant listings |
| **Product Master type integration** | M11 Stage 3 | Live DB: `products.product_type` populated for 10/11 market-scoped products, majority-vote + conflict flag |
| **Product Master (identity)** | M12 | Live DB: `products` 92 rows, dashboard reads from it directly |
| **AI reliability layer** | M13 | `src/dmie/ai/client.py::call_ai()`, all 4 AI call sites refactored, 14 tests |
| **Run versioning / provenance** | M14 | `pipeline_runs`/`classification_runs`/`product_resolution_runs`, append-only, `compare_runs()` |
| **Stale product-type-distribution bug** | fix | Found in audit, fixed, re-verified against live DB same session |

**This prompt's "Phase 1 through Phase 4" (product-type classification,
Product Master intelligence, AI reliability, data versioning) are
already fully built, tested, documented, and committed** — they are M11
Stages 2-3, M12, M13, and M14 respectively, all listed above. Re-running
them from scratch would violate this prompt's own explicit instruction:
"You must NOT rebuild." Nothing further is done for those 4 phases in
this document beyond stating that they're done and pointing at the
evidence.

## 2. Incomplete components

- **M15-equivalent (second-category validation, "Phase 8" here)** —
  blocked, not started. No micromotor SellerSprite raw export exists
  anywhere in the project; `config/categories.yaml`'s micromotor entry is
  explicitly labeled "illustrative example only." Cannot proceed without
  a real file from you.
- **Product-matching quality gold dataset ("Phase 5")** — `docs/entity_resolution.md`
  documents 5 manually-verified pairs used as a directional check
  (`docs/human_review_workflow.md`'s calibration section already says
  "n=5, too small to be robust"), but no dedicated
  `gold_product_clusters_v1` dataset of the scale this phase describes
  exists yet.
- **Pipeline automation ("Phase 9")** — `scripts/run_pipeline.py` does not
  exist. Every stage is still triggered manually, in a required order —
  this is exactly how the fixed bug went undetected for 2 days (nothing
  forced downstream scripts to re-run after `classify_product_types.py`
  was introduced).
- **Opportunity signal type coverage ("Phase 7")** — the existing engine's
  signal types (`PRODUCT_IMPROVEMENT`, `BUNDLE`, `PRICE_SEGMENT`,
  `UNDERREPRESENTED_PRODUCT_TYPE`, `CUSTOMER_PAIN_POINT`,
  `COMPETITIVE_CONCENTRATION`) are a real, working, evidence-based
  taxonomy (`src/dmie/opportunity/signals.py::SIGNAL_TYPES`) but do not
  1:1 match the example names in this phase's brief
  (`HIGH_DEMAND_LOW_DIFFERENTIATION`, `HIGH_COMPLAINT_PRODUCT`,
  `BUNDLE_OPPORTUNITY`, `PRICE_GAP`, `UNDERREPRESENTED_SEGMENT`). This
  needs a decision: rename/remap the existing taxonomy to match the new
  names (a real "silently change business definitions" risk if done
  carelessly — PRINCIPLES.md), or treat the brief's names as illustrative and
  keep the existing, already-evidence-based taxonomy. Not decided here.
- **CI ("Phase 12" implies but doesn't name)** — no CI configuration
  exists.
- **Dashboard finalization pass ("Phase 11")** — the pages exist and were
  browser-verified after M12; they have not been re-verified since M13/
  M14/the Stage-3 population + bug fix. `product_type_confidence`/
  `product_type_conflict` (new in Stage 3) aren't surfaced in the UI
  anywhere yet.
- **Full documentation pass ("Phase 13")** — README is missing the
  `[dashboard]` install extra and any dashboard-launch command (found in
  the environment check); no standalone "user guide" or "business
  interpretation guide" document exists (methodology.md and
  data_dictionary.md cover adjacent ground but aren't the same thing).

## 3. Technical debt (carried over from `full_system_audit_report.md`, still open)

- `src/dmie/utils/config.py`, `utils/logging.py`, `config/settings.yaml`,
  `.env.example`, `CHANGELOG.md` are all still 0-byte stubs.
- Two test files live at `tests/` root instead of `tests/unit/`/
  `tests/integration/` (cosmetic).
- No `.gitattributes` — every commit emits CRLF/LF warnings.
- `market/aggregation.py` and `matching/resolution.py` independently
  compute "product type for this group of listings" — the exact
  duplication that caused the bug this session found and fixed. Worth
  consolidating so it can't drift a second time, though not urgent now
  that both are correct.

## 4. Risks

- **Manual pipeline ordering is the single biggest operational risk.**
  The bug fixed this session existed for 2 days specifically because
  nothing enforced "re-run `calculate_market.py` after
  `classify_product_types.py` changes." Every day this project runs
  without `scripts/run_pipeline.py` (or at least a documented run order
  with an enforced check), the same failure mode can recur in a new
  place.
- **The AI reliability layer (M13) has never been exercised against a
  real model response.** No `ANTHROPIC_API_KEY` has ever been configured.
  The retry/timeout/malformed-response handling is real and tested
  against mocks, but "one failed AI request cannot stop the pipeline" is
  unverified against an actual failure from the actual API.
- **Second-category genericity is unverified.** M11-M14's code was
  written to be category-agnostic (taxonomy/signals loaded from config,
  no hardcoded category strings in the classification/matching logic),
  but that's a design intent, not a proven fact — it has only ever run
  against `denture_base`. Phase 8/M15 is the actual test of this claim
  and is blocked on data.

## 5. Incorrect assumptions found (and corrected)

- **The assumption that `market/aggregation.py` and
  `matching/resolution.py::build_products()` shared one source of truth
  for "listing → product type" was false.** They were two independent
  read paths into two different tables, and only one of them got updated
  when the real table changed in M11 Stage 3. This was the root cause of
  the bug fixed this session — recorded here so the same wrong assumption
  isn't repeated when Phase 7's signal-type work or Phase 8's
  second-category work touches product-type-dependent code again.
- **This prompt's own "Starting State" section assumes M11 is only
  "taxonomy discovery completed"** — i.e., that Stages 2-3 (classifier +
  Product Master integration) haven't happened. They have (§1). Continuing
  to plan against the prompt's stated starting state instead of the
  actual one would mean redoing real, tested, committed work.

## What's next

Per this prompt's own "do not complete everything in one uncontrolled
run" rule, and given Phases 0-4 are done, the next genuinely-open items
in sequence are: **Phase 5 (matching-quality gold dataset)**, **Phase 6
(review-intelligence completion — currently blocked on the same
no-review-text source-data limitation as M8, revisit only if new data
arrives)**, **Phase 7 (opportunity signal-type decision above)**, and
**Phase 9 (pipeline automation)** — Phase 8 (second category) stays
blocked pending real micromotor data. Awaiting direction on which to
start.
