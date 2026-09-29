# Entity Resolution Evaluation Report

Real numbers from the live database (`denture_base`, 105 listings), run
2026-09-22 against the pipeline as it exists after M15. This is a
dataset-wide, quantitative pass — distinct from `docs/entity_resolution.md`'s
5 manually-verified pairs, which stay the qualitative "does the logic do
the right thing on a known case" reference. This report instead
eyeballs the *actual* real output for signs of trouble across the whole
population, per PRINCIPLES.md's core rule: **100 listings must not silently
become 100 products, and the reverse (silently merging different
products) is just as real a failure.**

No `ANTHROPIC_API_KEY` is configured in this environment, so Stage 7 (AI
arbitration) never fires — every MATCH/NO_MATCH/UNCERTAIN decision below
came from Stages 1-6 (deterministic rules + composite scoring) alone.
That's the honest current state, not a gap introduced by this report.

## Headline numbers

| Metric | Value |
|---|---|
| Listings | 105 |
| Resolved products | 92 |
| Average listings/product | 1.14 |
| Singleton products (exactly 1 listing) | 81 / 92 = **88.0%** |
| Multi-listing products | 11 / 92 = 12.0% (9 with 2 listings, 2 with 3 listings) |
| Candidate pairs generated (post-blocking) | 91 |
| — decided MATCH | 14 |
| — decided UNCERTAIN (queued for review) | 76 |
| — decided NO_MATCH | 1 |

**88% of resolved products are singletons.** For a keyword-scraped export
where most listings genuinely are distinct commercial products (only
101 of the 105 listings *not the same-brand-generic-repair-kit sub-market*
described below), a high singleton rate is the expected, correct
outcome — not evidence the clustering is too conservative. The 12% that
did cluster are concentrated in exactly the sub-markets you'd predict:
identical/near-identical generic listings (the wax-plate duplicate pair),
same-brand pack-size families (Dentemp, XLMCWT), and same-brand color
variants (Asettlekit).

## Confidence distribution (91 candidate pairs)

| Band | Count |
|---|---|
| 0.00 – 0.40 | 1 |
| 0.40 – 0.60 | 7 |
| 0.60 – 0.80 | 38 |
| 0.80 – 0.90 | 26 |
| 0.90 – 1.00 | 19 |

mean 0.775, median 0.798, std 0.134, min 0.382, max 1.000.

**Match method breakdown:**

| Method | Decision | Count |
|---|---|---|
| `fuzzy_composite` | MATCH | 14 |
| `fuzzy_composite` | UNCERTAIN | 60 |
| `fuzzy_composite` | NO_MATCH | 1 |
| `possible_pack_variant` | UNCERTAIN | 16 |
| `exact_identifier` | — | 0 (no model/UPC/GTIN field exists in this dataset) |
| `ai_arbitration` | — | 0 (no AI key configured) |

The confidence distribution is **not bimodal** the way the relevance
classifier's is (`docs/human_review_workflow.md`) — real, continuous
composite scores exist across the whole 0.4-1.0 range, because
entity-resolution scoring is a continuous title/brand/price blend, not a
binary rule match. This means `match_threshold`/`no_match_threshold`
*could* in principle be recalibrated against real data the way relevance's
`automatic_threshold` was — that hasn't been done, and isn't in scope
here, but is worth naming as a real future option now that real
distribution data exists to calibrate against.

## Suspected false merges (auto-MATCHed pairs worth a second look)

Of the 14 auto-MATCHed pairs, 11 look correct on inspection (identical or
near-identical titles, same brand, consistent pack sizes, small price
variance explained by seller/time). **3 are suspected false merges, and
all 3 trace to the same root cause: `extract_quantities()`'s pack/bundle
guard misses a real quantity difference because of gaps already
documented in its own docstring** (`src/dmie/matching/similarity.py`).

### 1. `B0F599K6BR` (1 Piece) vs `B0F598SL5Q` (2 Pack) — confidence 0.911 — **FIXED 2026-09-22**

> A: "...Finishing Kits 2.35mm for Rotary Tools **(1 Piece)**"
> B: "...Finishing Kits 2.35mm for Rotary Tools **(2 Pack)**"

Same HOYIKI brand, same product, but one unit vs. a 2-pack — per
`config/categories.yaml`'s own `denture_base.variant_policy`
(`pack_quantity: separate_products`), this should never auto-merge.

**Root cause, traced exactly:** both titles also contain `"12pcs"` (the
number of polishing heads *inside* one unit, unrelated to the outer
pack count). `extract_quantities("...12pcs...(1 Piece)")` → `{"12", "1"}`;
`extract_quantities("...12pcs...(2 Pack)")` → `{"12", "2"}`. The two sets
share `"12"`, so `q_a.isdisjoint(q_b)` was `False` — the guard required
*full* disjointness, and one shared, irrelevant number defeated it. This
was precisely the limitation `extract_quantities`'s own docstring already
warned about (`"3 ea (Pack of 2)"` vs plain `"3 ea"` example) — a second,
real, live instance of the same documented gap.

**Fixed:** the guard now fires on symmetric difference (`q_a ^ q_b`)
rather than requiring full disjointness — `{"12","1"}` vs `{"12","2"}`
differ on `"1"`/`"2"`, which is what actually matters, regardless of the
shared `"12"`. Re-ran the full pipeline against the live DB: this pair
now resolves `UNCERTAIN`/`possible_pack_variant` instead of `MATCH`.

**Accepted tradeoff from this same change, not a new bug:** exactly one
other real pair (`B0F9B9LHPW` "2 Pack (8 Count)" vs `B083QN94W5` "Pack of
2") moved from a correct auto-`MATCH` to `UNCERTAIN`/`possible_pack_variant`
too — both are legitimately the same 2-pack product, but one states an
"8 Count" detail the other omits, so they now differ symmetrically on
`"8"`. This is checked and accepted, not accidental: queuing an already-
correct match for a quick human confirmation is far cheaper than the
alternative (silently shipping a wrong one, which is what this fix
exists to prevent). See `DECISIONS.md` for the full before/after diff
across all 91 real candidate pairs (exactly 3 changed; the third,
`B00VQTLM74`/`B00E4MPAIW`, was already correctly `UNCERTAIN` and is now
additionally labeled `possible_pack_variant` instead of generic
`fuzzy_composite` — a labeling improvement, not a decision change).

### 2. `B0FBCQX1W1` (Hard) vs `B0FCTJKD2P` (Soft) — confidence 0.934

> A: "**Hard** Denture Reline Kit — Chairside Acrylic Repair and Rebase Conditioner..."
> B: "**Soft** Denture Reline Kit — Chairside Acrylic Repair and Rebase Conditioner..."

Same brand (House Brand), same price band ($120-125), titles differ by
exactly one word — but that word (hard vs. soft formulation) is the
functionally defining difference between two different reline products,
not a cosmetic variant. Composite scoring (title 0.5 + brand 0.3 +
attribute/price 0.2) rates this pair 0.934 — **above
`match_threshold` (0.90)**, so it auto-matched *without ever reaching
Stage 7 AI arbitration*, which is exactly the stage built to catch
"textually near-identical but semantically different" cases like this
one. This is a structural gap, not a data-quality gap: a title that's
~95% token-identical can score above the auto-match threshold purely on
title/brand/price similarity, bypassing semantic judgment entirely,
regardless of whether AI arbitration is even configured.

### 3. `B0GVB7J6PV` (White, 1 Box) vs `B0GVB48DSX` (White, 2 Bottle) — confidence 0.927 — **FIXED 2026-09-22**

> A: "...Suitable for Missing, Cracked DIY Denture Kit. (White **1 Box**)"
> B: "...Suitable for Missing, Cracked DIY Denture Kit. (White **2 Bottle**)"

Same brand (yuanyuuo), same core product — but again a pack-count
difference the guard should catch. **Root cause:** `_QTY_BEFORE_RE`'s
recognized unit words were `pcs?|pc|pack|box|ea|piece` — `"bottle"` wasn't
one of them. `extract_quantities("...1 Box)")` → `{"1"}`;
`extract_quantities("...2 Bottle)")` → `{}` (empty — "bottle" wasn't
recognized at all). The guard requires *both* sides to have a non-empty
quantity set (`if q_a and q_b and ...`); with `q_b` empty, the check never
even ran. A narrower, closed unit-word vocabulary was the cause here, not
the disjointness logic itself.

**Fixed:** widened `_QTY_BEFORE_RE` to also recognize `bottle`, `set`,
`count`, `ct`. Re-ran the full pipeline against the live DB: this exact
pair now resolves `UNCERTAIN`/`possible_pack_variant` (confidence
unchanged at 0.927, but no longer auto-accepted) instead of `MATCH` —
`match_candidates` MATCH count went 14 → 13, resolved products 92 → 93.
Regression tests: `tests/unit/test_similarity.py` (+4) and
`tests/unit/test_resolution.py::test_box_vs_bottle_pack_variant_is_no_longer_auto_merged`,
using this exact real ASIN pair. See `DECISIONS.md` "Fix: entity
resolution quantity vocabulary" for the disjointness-logic recommendation
(#2 below) that was deliberately **not** part of this fix.

**All 3 are documented, single-root-cause instances, not 3 independent
bugs** — recommended fix (not applied — this is a report, not a fix
pass): widen `_QTY_BEFORE_RE`'s unit vocabulary (at minimum: `bottle`,
`set`, `count`/`ct`), and change the guard from requiring full
disjointness to firing whenever the symmetric difference between the two
quantity sets is non-empty (i.e. either side has a quantity token the
other doesn't), not only when they share *zero* tokens. Case #2 (hard vs.
soft) is different in kind — no quantity-regex fix touches it; it needs
either a lower `match_threshold`, a lexical "contrastive-pair" check
(risks category-specific hardcoding — PRINCIPLES.md principle 9), or simply
Stage 7 AI arbitration actually being reachable for scores that currently
skip straight past it. All three are recommendations for you to decide
on, not applied here.

## Suspected missed duplicates (queued pairs worth a second look)

**Most of the 76 UNCERTAIN pairs are working exactly as designed, not
missed.** The 16 `possible_pack_variant` pairs (e.g. XLMCWT's
4PCS/3Pack/1Pack trio, Manutox's 1pc/2pcs, Dentemp's 2-Pack/3-Pack) are
correctly deferred per `denture_base`'s own `pack_quantity:
separate_products` policy — the system is designed to queue these for a
human decision rather than silently pick one answer, and it's doing
that. Not a defect.

**One real inconsistency worth flagging**, not a confirmed miss (it's
correctly sitting in the review queue, not wrongly merged or wrongly
rejected): `B083QN94W5` ("Pack of 2") vs `B000GGHS62` (no quantity
marker at all) scored 0.894 (`fuzzy_composite`, UNCERTAIN) — but the
*same* `B083QN94W5` ("Pack of 2") was separately auto-MATCHed against
`B0F9B9LHPW` ("2 Pack (8 Count)") at 0.916, correctly, since both
explicitly say 2-pack. The detection is asymmetric: a pack-size
difference against an *explicitly* different pack size gets caught
(`possible_pack_variant`) more reliably than one against a listing with
*no* quantity marker at all (which defaults to being invisible to the
guard, same root cause as false-merge #3 above — an empty extracted set
never triggers the check). Recommend a human reviewer look at this
specific pair first among the 76 queued ones; not asserting it's wrong,
since a single-unit purchase and a "Pack of 2" *could* still reasonably
be considered the same product family depending on business judgment
(this is exactly the kind of case Stage 7 exists to adjudicate).

## What's working well

- The zero-false-merge rate outside the 3 flagged cases (11/14, 79%)
  across a real, messy, keyword-scraped 105-listing export is a solid
  result for scoring with no semantic (AI) stage active at all.
- The `possible_pack_variant` mechanism is doing its job for every case
  where its (narrower-than-ideal) vocabulary actually recognizes the
  quantity terms used.
- The NO_MATCH decision (the one pair scored ≤ 0.40) is correct on
  inspection: two different-brand ultrasonic cleaners with genuinely
  different design language, correctly not matched.
- No `exact_identifier` or `ai_arbitration` decisions exist yet — an
  honest reflection of this dataset having no model/UPC field and no AI
  key configured, not a bug in the reporting.

## Recommendations

1. ~~Widen `_QTY_BEFORE_RE`'s unit vocabulary~~ **DONE 2026-09-22** —
   `bottle`, `set`, `count`, `ct` added; fixes false-merge #3 (see above).
   Does **not** fix the "asymmetric detection" case (`B083QN94W5` vs
   `B000GGHS62`) — that pair has no quantity word on one side at all, no
   vocabulary addition helps a title with zero quantity language.
2. ~~Change the pack-variant guard from "fully disjoint" to "symmetric
   difference is non-empty"~~ **DONE 2026-09-22** — fixes false-merge #1;
   see the updated write-up above and `DECISIONS.md` for the full 91-pair
   before/after diff and the one accepted tradeoff it introduced.
3. Decide how to handle false-merge #2's class of case (contrastive
   adjectives that carry all the identity-defining meaning in an
   otherwise near-identical title) — no fix is applied here since every
   option carries a real tradeoff (lowering `match_threshold` affects
   every pair, not just this pattern; a contrastive-word list risks
   category-specific hardcoding).
4. Once real AI-arbitration data exists (an `ANTHROPIC_API_KEY` gets
   configured), re-run this same query set and compare — the confidence
   distribution here is rich enough to support a real
   `match_threshold`/`no_match_threshold` calibration pass, the same way
   `scripts/calibrate_thresholds.py` did for relevance classification.
