# Review Intelligence Methodology (Milestone 8)

## Pipeline

```
review extraction
      ↓
pain-point taxonomy
      ↓
theme normalization
      ↓
frequency
      ↓
severity
      ↓
evidence
```

Implementation: `src/dmie/reviews/{extraction,themes,aggregation}.py`.
Prompt: `prompts/review_analysis.md`. Taxonomy: `config/review_taxonomy.yaml`.

1. **Review extraction** (`extraction.py::extract_with_ai`) — AI reads one
   review's raw text and proposes a pain point, affected attribute,
   severity, complaint, evidence quote, and improvement suggestion. Same
   graceful-degradation pattern as every other AI stage in this project
   (`classifier.py`, `resolution.py`): returns `None`, never a guess, when
   no `ANTHROPIC_API_KEY` is configured.
2. **Pain-point taxonomy** (`config/review_taxonomy.yaml`) — a fixed,
   6-category controlled vocabulary (below). The model is shown the exact
   list in the prompt and told never to invent a new category or
   subcategory.
3. **Theme normalization** (`themes.py::validate_taxonomy_path`,
   `normalize_theme_key`) — every extracted `category/subcategory` pair
   is checked against the taxonomy in code, not just asked for in the
   prompt. An invented category (e.g. "RELIABILITY", which sounds
   plausible but isn't in the list) is rejected, not silently accepted.
4. **Frequency** (`aggregation.py::theme_frequency`) — deterministic count
   of successfully extracted insights per theme. No LLM performs this
   arithmetic (PRINCIPLES.md principle 4).
5. **Severity** (`aggregation.py::theme_severity`) — deterministic
   mean/max severity per theme, computed only over extracted insights
   with a severity value.
6. **Evidence** (`extraction.py::verify_evidence`) — the single most
   important guardrail in this pipeline: the model's claimed `evidence`
   quote must be an actual (case/whitespace-tolerant) substring of the
   source review text, checked in code. If it isn't, the whole insight is
   rejected — logged to `decision_log`, never stored in `review_insights`
   as if it were a verified fact. This is the concrete enforcement of
   "extract only claims supported by the supplied review" and "do not
   invent causes" — not just prompt wording, a checkable rule.

## Controlled taxonomy

```
QUALITY
├── durability
├── breakage
├── material
└── manufacturing

PERFORMANCE
├── effectiveness
├── speed
├── accuracy
└── consistency

COMPATIBILITY
├── size
├── connection
├── fit
└── system_compatibility

USABILITY
├── installation
├── instructions
├── controls
└── learning_curve

PACKAGING
├── damage
├── missing_parts
└── protection

VALUE
├── price
└── bundle_value
```

This is a fixed, cross-category vocabulary (unlike `config/categories.yaml`'s
per-category `variant_policy` from Milestone 6) — the same 6 categories
apply to any product category this system ever analyzes. Extraction maps
every insight into exactly one `category/subcategory` pair; nothing
outside this list is accepted.

## Severity scale (1-5)

| Score | Meaning |
|---|---|
| 1 | Minor/cosmetic annoyance — does not affect core function |
| 2 | Mild inconvenience — workaround exists, product still usable |
| 3 | Moderate functional problem — noticeably impairs the product's job |
| 4 | Major functional failure — product mostly fails at its job |
| 5 | Product unusable, or a safety concern |

## What happens to a rejected insight

Every non-`extracted` outcome (`rejected_no_evidence`,
`rejected_invalid_taxonomy`, `ai_unavailable`, `no_pain_point`) is written
to `decision_log` (`entity_type='review_insight'`), never silently
dropped (PRINCIPLES.md principle 8) and never written to `review_insights` as
if it were confirmed. `review_insights` only ever contains insights that
passed both guardrails.

## Status: no real review text exists yet

This pipeline is fully built and tested
(`tests/unit/test_extraction.py`, `test_themes.py`,
`test_aggregation_reviews.py`, `tests/integration/test_review_insights_pipeline.py`
— all using clearly-synthetic example reviews, not real Amazon data) but
has never run against real data. The only data source ingested so far,
SellerSprite, has **no review text at all** — not even a review count
(`docs/data_dictionary.md` §12). `scripts/analyze_reviews.py` reports this
plainly rather than fabricating example reviews for real `denture_base`
products:

```
$ python scripts/analyze_reviews.py
No review text found at .../data/raw/denture_base/reviews.json.
This dataset's only source so far (SellerSprite) has no review-text column
(see docs/data_dictionary.md #12) -- nothing to extract yet.
Supply a JSON file of [{"listing_id": ..., "product_id": ..., "review_text": ...}, ...]
to run this pipeline for real.
```

Running this for real needs a review-text source this project doesn't
have yet (e.g. a separate Amazon-review scrape or export).
