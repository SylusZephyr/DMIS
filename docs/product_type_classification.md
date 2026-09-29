# Product Type Classification (M11 Stage 2)

Classifies listings already confirmed `RELEVANT` into one type from the
frozen taxonomy (`config/taxonomy/denture_base_v1.yaml`, see
`docs/taxonomy_denture_base_v1.md` and `DECISIONS.md`'s "M11 — Taxonomy v1
frozen"). This stage never decides category membership and never touches
the taxonomy itself — it only assigns an already-RELEVANT listing to one
of the taxonomy's existing ids, or `UNCERTAIN`.

Implementation: `src/dmie/classification/product_type_classifier.py`.
Prompt: `prompts/product_type_classifier.md`. Tests:
`tests/test_product_type_classifier.py`.

## The workflow

```
Listing (relevance_class = RELEVANT)
    |
    v
Layer 1 -- deterministic keyword rules (apply_rules)
    |
    | exactly one taxonomy id scores > 0
    |    -> confidence 0.95 -> AUTO-ACCEPTED, AI never called
    |
    | zero ids score > 0, OR more than one id scores > 0 (ambiguous)
    v
Layer 2 -- AI fallback (classify_with_ai), only for what Layer 1 couldn't resolve
    |
    | no ANTHROPIC_API_KEY configured -> None, never a guessed verdict
    v
Layer 3 -- confidence-threshold routing (config/thresholds.yaml: product_type)
    |
    +-- >= 0.85 (automatic_threshold)      -> AUTO-ACCEPTED, type kept
    +-- 0.60 to 0.85 (sampling_threshold)  -> NEEDS_REVIEW, type kept (not discarded)
    +-- < 0.60, or AI unavailable          -> NEEDS_REVIEW, product_type forced to "UNCERTAIN"
```

This is the same hybrid pattern already used for relevance classification
(`classifier.py`) and entity resolution (`matching/resolution.py`):
deterministic first, AI only for what rules can't resolve, tri-state
confidence routing so "no" is never confused with "don't know."

**AI is never called when Layer 1 already produced a confident answer** —
tested directly (`test_confident_rule_match_bypasses_ai_entirely`), which
is the literal implementation of "do not use AI for obvious cases."

## Allowed outputs

Exactly the 4 values below — nothing else, ever, regardless of what an AI
response claims:

- `DB_RESIN` — Denture Base Resin
- `DB_WAX_PLATE` — Denture Base Wax Plate
- `DB_RELINE` — Denture Reline Material/Kit
- `UNCERTAIN` — cannot confidently classify (not a taxonomy type; the
  literal signal that a human needs to look at this listing)

An AI response naming anything else (including `"Other"`, which the
taxonomy freeze explicitly rejected as a bucket) is hard-rejected to
`UNCERTAIN` in code, not just discouraged by the prompt — see
`test_ai_returning_a_label_outside_the_taxonomy_is_rejected_never_trusted`.

## Layer 1 — deterministic keyword rules

`apply_rules(title, signals)` scores every taxonomy id by how many of its
configured keyword signals appear in the title (whole-word,
case-insensitive). The signal words live in `config/product_type_signals.yaml`
— a separate, editable file from the frozen taxonomy, so tuning a keyword
list is not a taxonomy change and needs no new taxonomy version:

| taxonomy id | signal words |
|---|---|
| `DB_RESIN` | resin, acrylic |
| `DB_WAX_PLATE` | wax, plate, sheet |
| `DB_RELINE` | reline, refit, repair, renewal, adjustment |

The function takes this map as a parameter and contains no taxonomy-id
literals — proven by `test_rules_layer_does_not_hardcode_taxonomy_ids_in_python`,
which feeds it a fabricated taxonomy and gets a fabricated id back. Adding
a category or a new taxonomy version means adding config, never editing
this function.

**Scoring outcome:**
- Exactly one id scores > 0 → confident, unambiguous match (confidence
  `rules_confidence` = 0.95).
- Zero or more than one id scores > 0 → not decidable by keywords alone,
  returned at `rules_ambiguous_confidence` = 0.50 (always below the
  automatic threshold, so it can never be auto-accepted) — the caller
  must defer to Layer 2.

**A title scoring more than one id is a real, expected ambiguity, not a
bug.** "Acrylic" appears in genuine `DB_RESIN` titles (fabrication
material) and in genuine `DB_RELINE` titles (repair-kit material) because
they're the same underlying acrylic chemistry used for different
purposes — the taxonomy's own definitions distinguish them by *function*
(fabrication vs. repairing an existing denture), not by material words
alone. See "Evaluation" below for the one real listing this affects.

## Layer 2 — AI fallback

`classify_with_ai(listing, taxonomy)` only runs when Layer 1 didn't clear
the automatic threshold. Returns `None` when no AI provider is configured
(no `ANTHROPIC_API_KEY`, or the `anthropic` package isn't installed) —
callers must treat `None` as "AI unavailable," never a verdict, same
graceful-degradation contract as `classifier.py::classify_with_ai` and
`matching/resolution.py::ai_arbitrate`.

The prompt (`prompts/product_type_classifier.md`) is given the frozen
taxonomy's ids, names, and definitions (loaded from config, not
hardcoded into the prompt file) and is explicitly instructed never to
invent, rename, or split a type, and to answer `UNCERTAIN` rather than
force a fit.

## Layer 3 — confidence-threshold routing / human review queue

`config/thresholds.yaml`'s new `product_type` block:

```yaml
product_type:
  rules_confidence: 0.95
  rules_ambiguous_confidence: 0.50
  automatic_threshold: 0.85
  sampling_threshold: 0.60
```

Not yet empirically calibrated — no `ANTHROPIC_API_KEY` is configured in
this environment, so there is no real AI-confidence data to calibrate
against yet (same caveat `relevance.sampling_threshold` already carries;
see `docs/human_review_workflow.md`). These are the reasoned example
values from the M11 Stage 2 instructions. Re-run
`scripts/calibrate_thresholds.py`-style calibration once the AI stage is
configured and has produced real predictions.

## Structured output

`ProductTypeResult`: `listing_id`, `taxonomy_version`, `product_type`,
`confidence`, `reason`, `classifier_method` (`"rules"` | `"ai"` |
`"unavailable"`), `review_status` (`"auto_accepted"` | `"needs_review"`).

## Evaluation

**Classification must only ever be run on listings already classified
`RELEVANT`** — this module doesn't check relevance itself (a
`ListingContext` carries no relevance field), the same division of
responsibility as `market/aggregation.py::fetch_relevant_listings` gating
what reaches product-level metrics. Enforcing this is the caller's job
(the future pipeline script, M11 Stage 3+) — every test and evaluation
here is scoped to listings already known to be RELEVANT.

**First validation target: the 12 listings currently classified RELEVANT
for denture_base, against the human-approved taxonomy_v1 mapping**
(`docs/product_taxonomy.md`'s 3 approved candidates), with no AI key
configured:

| ASIN | Human Type | Predicted | Correct |
|---|---|---|---|
| B07DYMJ7TQ | DB_WAX_PLATE | DB_WAX_PLATE | YES |
| B094YBT6VD | DB_WAX_PLATE | DB_WAX_PLATE | YES |
| B09JL2CYKR | DB_WAX_PLATE | DB_WAX_PLATE | YES |
| B0CDM8JCLH | DB_WAX_PLATE | DB_WAX_PLATE | YES |
| B0CXMQ7DFZ | DB_WAX_PLATE | DB_WAX_PLATE | YES |
| B0CXTC26TV | DB_RESIN | DB_RESIN | YES |
| B0DRBMXKZR | DB_WAX_PLATE | DB_WAX_PLATE | YES |
| B0F26TYZQD | DB_WAX_PLATE | DB_WAX_PLATE | YES |
| B0F7LKY5V7 | DB_WAX_PLATE | DB_WAX_PLATE | YES |
| B0FMK8XB26 | DB_RELINE | UNCERTAIN | NO |
| B0G81P76D8 | DB_RELINE | DB_RELINE | YES |
| B0H6RLQ6YZ | DB_WAX_PLATE | DB_WAX_PLATE | YES |

**Accuracy: 11/12 (91.7%)**

**The one miss is the documented ambiguity, not a new failure:**
`B0FMK8XB26`'s title contains "Acrylic-Based" alongside "reline"/
"repair"/"adjustment" — Layer 1 correctly detected the tie between
`DB_RESIN` and `DB_RELINE` and deferred (`rule_ambiguous:DB_RELINE,DB_RESIN`,
confidence 0.50); with no AI key configured, Layer 2 returned `None` and
the result correctly landed on `UNCERTAIN` rather than a silent wrong
guess.

**Extended check (widening `DB_RESIN` beyond one example):** 2 more
hand-reviewed `YES` listings from the 50-row relevance gold set
(`data/samples/gold_labels_pilot.xlsx`) that aren't among the 12 above —
`B01I3ZIV2M` and `B0DYJMP4ZJ` — with types grounded directly in
`docs/classification_guidelines.md`'s own description of them (not
invented for this evaluation). Result: 12/14 (85.7%), with `B01I3ZIV2M`
reproducing the *exact same* acrylic/repair ambiguity as `B0FMK8XB26` —
no new failure pattern.

Reproduce both tables: `pytest tests/test_product_type_classifier.py -k evaluation -s`.

## What this stage does NOT do

- Does not write to the database. `listing_classification.product_type`
  has not been touched for any real row.
- Does not run against the other ~80 listings outside the 12+2 evaluated
  here (no `scripts/classify_product_types.py` exists yet).
- Does not modify the taxonomy, entity resolution, or the
  `products`/`product_listings` schema.
- Does not decide relevance — only ever classifies listings the relevance
  stage has already confirmed `RELEVANT`.

Wiring this into the real pipeline and populating `products.product_type`
via `matching/resolution.py::build_products()` (already capable of
mode-voting a `product_type` across a product's listings, per M12) is
M11 Stage 3 — deliberately not started here.
