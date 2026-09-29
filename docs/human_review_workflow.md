# Human Review Workflow — Confidence Tiers

## The workflow

```
AI / rules
    ↓
confidence
    ↓
┌───────────────────────────┐
│ >= automatic_threshold     │
│ auto_accepted              │
├───────────────────────────┤
│ sampling_threshold to      │
│ automatic_threshold        │
│ sample_for_qa              │
├───────────────────────────┤
│ < sampling_threshold       │
│ needs_review                │
└───────────────────────────┘
```

Implementation: `src/dmie/classification/classifier.py::_tier_for_confidence`
+ `config/thresholds.yaml`'s `relevance` block. Calibration tool:
`src/dmie/classification/calibration.py`, run via
`scripts/calibrate_thresholds.py`.

**The 95%/70% split in the original brief was a starting hypothesis, not
a target claimed without measurement** — this doc's whole point is
showing what was actually measured and where measurement isn't possible
yet.

- `auto_accepted`: used as-is, no human touches it.
- `sample_for_qa`: used as-is (the model's stated class is kept — it's
  more likely right than not at this confidence band), but flagged for
  periodic human audit rather than full review of every one.
- `needs_review`: `relevance_class` is **forced to `UNCERTAIN`** — at this
  confidence, the stated class isn't trusted at all, and a human decides.

## What was actually measured

Run `python scripts/calibrate_thresholds.py` to reproduce. It sweeps
every distinct confidence value the classifier has produced against the
50-row gold set (`data/validated/denture_base/gold_labels.csv`) and
reports precision at each threshold — not a formula, an actual count.

### Relevance classification (n=50, real gold set)

| threshold | n >= t | correct | precision | coverage |
|---|---|---|---|---|
| 0.00 | 50 | 47 | 94.0% | 100% |
| 0.95 | 39 | 38 | 97.4% | 78% |
| 1.00 | 0 | 0 | n/a | 0% |

**`automatic_threshold = 0.95`** — empirically the right call: 97.4%
precision (38/39) on real gold data, the highest threshold with real
coverage. The one miss is a known, already-documented rule limitation
(`B0FGXS3NLX`, see `docs/classification_guidelines.md`) — not new
information, confirms it.

**`sampling_threshold` could not be empirically calibrated.** The
calibration tool flags this explicitly rather than reporting a number
that looks measured but isn't:

> Only 1 distinct confidence value(s) observed in this data (besides the
> 0.0/1.0 sweep endpoints) — the confidence distribution is too
> sparse/bimodal to place a genuine middle tier yet.

The classifier currently only ever produces confidence `0.95` (a rule
fired) or `0.0` (AI unavailable) — there is no real data between them,
because the AI stage that would produce intermediate confidence values
has no `ANTHROPIC_API_KEY` configured (same blocker as Milestones 5-9).
`sampling_threshold = 0.80` is kept as a **reasoned default carried over
from before this calibration exercise**, explicitly not claimed as
measured. **Re-run `scripts/calibrate_thresholds.py` once the AI stage is
configured and has produced real predictions** — that's the actual
trigger for revisiting this number, not a fixed date.

### Entity resolution (n=5, the manually-verified pairs — too small to be robust, directional only)

| threshold | n >= t | correct | precision |
|---|---|---|---|
| 0.00 – 1.00 | 5 → 1 | all correct | 100% |

All 5 manually-verified pairs (`docs/entity_resolution.md`) are decided
correctly across the entire confidence range tested — informative (the
composite-score logic isn't obviously miscalibrated) but n=5 is nowhere
near enough to derive a real threshold from. The existing
`match_threshold = 0.90` / `no_match_threshold = 0.40`
(`config/thresholds.yaml`) are kept as the pre-existing, reasoned
defaults from Milestone 6 — this exercise didn't find evidence to move
them, but didn't have enough data to confidently validate them either.
Re-run once more verified pairs exist.

## Why this matters more than picking round numbers

A calibration tool that always finds a clean 3-way split would be
suspect — real classifiers producing exactly two confidence values is a
genuine, reportable finding, not a bug in the tool. `recommend_tiers()`
returns explicit `warnings` for exactly this case
(`tests/unit/test_calibration.py::test_recommend_tiers_warns_on_bimodal_real_world_distribution`)
so a caller can't mistake "no real data here" for "we checked and it's
fine".
