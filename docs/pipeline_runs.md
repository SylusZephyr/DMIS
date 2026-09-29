# Run Versioning and Data Provenance (Milestone 14)

## The problem

Pipeline scripts (`classify.py`, `classify_product_types.py`,
`resolve_products.py`, ...) all follow the same pattern: delete a table's
rows and regenerate them from the current input. That's correct and
intentional for the DATA tables — `products`, `listing_classification`,
`match_candidates`, etc. really are supposed to reflect the latest run's
output, not an ever-growing history. But it meant there was **no record
that a previous run ever happened at all** — no run ever left a trace
once superseded, so "did the numbers change since last time?" had no
answer beyond scrollback in a terminal.

## What this milestone adds

Three new, **append-only** tables — rows are never deleted or updated by
a later run, unlike every DATA table above:

- `pipeline_runs` — one row per script execution: `run_id`, `category_id`,
  `pipeline_stage`, `pipeline_version` (that stage's own version
  constant — `CLASSIFIER_VERSION`, `MATCHING_VERSION`, ...), `started_at`/
  `completed_at`, `status` (`running` → `success` | `failed`), and a
  `summary` JSON blob with the same numbers the script prints to stdout.
- `classification_runs` — one row per `classify.py` or
  `classify_product_types.py` run (`stage` = `relevance` | `product_type`),
  with the specific counts worth comparing directly: `total_classified`,
  `class_counts` (JSON), `auto_accepted_count`, `needs_review_count`.
- `product_resolution_runs` — one row per `resolve_products.py` run:
  `total_listings`, `candidate_pairs`, `match_count`, `no_match_count`,
  `uncertain_count`, `resulting_products`.

Implementation: `src/dmie/database/runs.py`. Every pipeline script calls
`start_run()` before doing any work and `finish_run()` in a `finally`/
`except` block, so a script that crashes mid-run still gets recorded as
`failed` rather than silently vanishing.

## What this milestone deliberately does NOT do

**The DATA tables themselves are not versioned.** `products`,
`listing_classification`, `match_candidates`, etc. still get deleted and
regenerated in place on every run — this milestone does not keep every
historical row of every table side by side. Doing that for real (so you
could query "what was `products.product_type` for `P123` as of last
Tuesday") would mean either a full slowly-changing-dimension rewrite of
every repository function and every downstream query (the dashboard,
`evaluation.py`, `build_products`, ...) or a wholesale switch to an
event-sourced/append-only data model — a genuinely large, separate
architectural change, not something to fold into a run-logging milestone
without its own review (per this project's "do not redesign architecture
unless a documented problem requires it" principle). What you get instead
is: **exactly what each run produced, permanently**, even though the
tables that run produced are themselves overwritten by the next run.

## How to compare two pipeline runs

```python
from dmie.database.connection import get_connection
from dmie.database.runs import list_runs, compare_runs

con = get_connection()

# 1. Find the runs you want to compare
runs = list_runs(con, category_id="denture_base", pipeline_stage="relevance_classification")
run_a, run_b = runs[1]["run_id"], runs[0]["run_id"]  # e.g. previous vs. latest

# 2. Diff them
comparison = compare_runs(con, run_a, run_b)
print(comparison["differences"])
# {} if identical, or e.g. {"auto_accepted_count": (77, 79), "class_counts": (..., ...)}
```

`compare_runs()` looks up each run's `pipeline_runs` row plus whichever
stage-detail row exists for it (`classification_runs` or
`product_resolution_runs`), then diffs every field the two runs share —
excluding `run_id`/`created_at`, which trivially differ between any two
runs and aren't a business difference. An empty `differences` dict means
the two runs produced identical counts (the classifier/matcher is
reproducible, as it should be per PRINCIPLES.md's "deterministic
calculations" — see `test_compare_runs_finds_no_differences_for_identical_reruns`).

**Verified against the real pipeline:** running `scripts/classify.py`
twice in a row produces two distinct `run_id`s with identical
`class_counts`/`auto_accepted_count`/`needs_review_count` — proof the
classifier's output didn't drift between runs, which is exactly the kind
of question this milestone exists to let you answer without re-reading
terminal scrollback.

## What to check when comparing runs

- **`status`** — did the run actually complete, or is it `failed`/stuck at
  `running` (crashed without reaching `finish_run`)?
- **`pipeline_version`** — did the classifier/matcher version change
  between the two runs? A version bump is expected to change counts; a
  version match with different counts means the *input data* changed
  (new listings ingested, upstream classification changed), not the code.
- **The stage-specific counts** (`class_counts`, `resulting_products`,
  etc.) — the actual business-relevant numbers `compare_runs()` diffs.
