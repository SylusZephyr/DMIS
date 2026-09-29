# Classification Guidelines — Relevance & Product Type

Derived from a manual, evidence-based review of 50 labeled listings in
`data/samples/gold_labels_pilot.xlsx` (义齿基托/denture base pilot). Every
rule below is backed by a specific ASIN in that file — cite the ASIN, don't
invent new examples, when applying or revising these rules.

## Implementation (Milestone 5)

These rules are implemented as a hybrid classifier:
`src/dmie/classification/rules.py` (deterministic, this doc's unambiguous
patterns) → `src/dmie/classification/classifier.py` (AI stage for
everything rules don't resolve, then confidence thresholding) → anything
below `config/thresholds.yaml`'s `ai_confidence_threshold`, or with no AI
provider configured at all, becomes `UNCERTAIN` / `review_status =
needs_review` — the human review queue.

**Escape hatch:** rules.py never lets an IRRELEVANT pattern fire if the
title also contains `reline`, `refit`, `full denture`, `base former`, or
`denture mold` — these are exactly this doc's AMBIGUOUS signals, so a
title matching both an IRRELEVANT trigger and an escape phrase always
defers rather than guesses.

**Known, accepted limitation:** substring rules cannot fully disambiguate
every case. `B0FGXS3NLX` (a DIY full/partial-denture kit, gold-labeled
UNCERTAIN) also contains "Fake Teeth" and "Denture Repair kit", which
trigger the WRONG_CATEGORY rule — the deterministic layer gets this one
wrong. This is documented and tested
(`tests/unit/test_rules.py::test_known_limitation_diy_full_denture_kit_collides_with_bead_pattern`)
rather than patched with more exceptions, which would overfit the rules to
this one row instead of generalizing. Real numbers, including this
mismatch, are in `docs/data_dictionary.md`'s sibling evaluation output —
run `python -m dmie.classification.evaluation` to reproduce.

**AI stage status:** `classify_with_ai` requires `ANTHROPIC_API_KEY` in
the environment; without it, every listing rules don't resolve becomes
`UNCERTAIN` (reason `ai_unavailable`) rather than a guess. As of this
milestone no key is configured, so the two reline/acrylic-liquid EXACT_MATCH
cases that were deliberately kept out of the deterministic rules
(`B0DYJMP4ZJ`, `B01I3ZIV2M`) currently resolve to UNCERTAIN too — correct
behavior for an unconfigured AI stage, not a bug.

## The question being answered

**Not** "is this dental?" or "is this denture-related?" — almost every
listing in a keyword-scraped export will be. The question is:

> Does this listing genuinely belong to the specific leaf category
> 义齿基托 / denture base — the structural base plate of a denture, or
> material/work that directly becomes or reworks that base plate?

Something can be 100% dental, 100% denture-related, and still be NO,
because it belongs to a *sibling* leaf category (adhesives, cleansers,
tooth-repair, etc. — see `data_dictionary.md`'s category-contamination
finding: most of a "denture base" keyword export is not denture base).

## Anti-pattern: do not gate on the literal word "base"

The string "base" appears in listings on both sides of the relevance line
and is not a usable signal by itself:

- **False positive risk:** a cleaning machine's "Removable Base" (device
  tray) and an adhesive's "Gum Base" (bonding putty) both contain "base"
  but are NO (`B0G6LDBD94`, `B0H14R7BBY`, `B0F8H9K4D9`, `B0H14ZK7HR`,
  `B0GCFYFTWC`).
- **False negative risk:** `B0DYJMP4ZJ` ("Silicone Reline Kit for Dentures")
  never says "base" and is still YES — reline material becomes part of the
  base plate.

Any classifier (human or AI) must reason about what the product *is*, not
pattern-match on this word.

## relevant = YES (reason code `EXACT_MATCH`)

The listing IS denture-base material, or work that directly forms/reworks
the base plate:

- Base-plate wax used to model/cast a base (`B0CXMQ7DFZ`, `B09JL2CYKR`,
  `B0DRBMXKZR`, `B0F7LKY5V7`)
- Denture base resin/acrylic — including a self-cure acrylic *liquid* sold
  under a "repair" name; it's the same material class as base resin
  regardless of which raw category SellerSprite filed it under
  (`B0CXTC26TV`, `B01I3ZIV2M`)
- Reline or explicit "denture base repair/renewal" kits — **but only when
  reline/base-rework is the listing's sole or clearly dominant described
  function** (`B0G81P76D8`, `B0FMK8XB26`, `B0DYJMP4ZJ`)

**Reline dilution rule:** if "reline" is mentioned once alongside competing
framing (a general "false teeth repair system", "temporary denture kit") or
the listing explicitly bundles a separately-named general repair kit with a
separately-named reliner (e.g. "Repair-it" + "Reline-it"), that's not a
clean EXACT_MATCH — see AMBIGUOUS below (`B0H7HF45WN`, `B087YB281L`).

## relevant = NO (reason code `WRONG_CATEGORY`)

Dental/denture-related, but a different function — belongs to a sibling
leaf category, not this one:

- **Adhesives** — bonds denture to gum tissue; doesn't become the base
  material (`B0GCFYFTWC`, `B0GRVW9J4Z`, `B0FRZG95G5`, `B0GJ46TT4B`,
  `B0FHPR4YY1`, `B0FHP2WVSZ`, `B0FY1QR38L`, `B0G6KFMMX8`)
- **Cleaning appliances** — ultrasonic retainer/denture cleaner machines;
  used on a *finished* denture in daily use, a wholly separate retail leaf
  category from base fabrication (`B0G6LDBD94`, `B0H14R7BBY`, `B0F8H9K4D9`,
  `B0H14ZK7HR`)
- **Tooth-replacement/repair beads and kits** — fix or replace individual
  teeth, not the base plate (`B0GVB7J6PV`, `B0FSZNLGDJ`, `B09GVRZYQ8`, and
  the rest of the "moldable thermal beads" cluster)
- **General repair kits** with no reline/base-specific material — fixes
  cracks/loose teeth generically, no base-rework claim
  (`B0DLHDCBH3`, `B00VQTLM74`, `B00E4MPAIW`, `B006305CZ4`, `B00E4MNC5A`)

## relevant = NO (reason code `ACCESSORY_ONLY`)

A tool/component used **on** the base during fabrication or finishing, but
is not the base material itself and not the base plate:

- Polishing/rotary-tool burs for finishing acrylic denture surfaces
  (`B0F598SL5Q`)

**Distinction from WRONG_CATEGORY:** ACCESSORY_ONLY is for fabrication-step
tools that touch the base being made. A cleaning machine touches a
*finished* denture during ordinary use and belongs to its own separate
product family (denture cleansers) — that's WRONG_CATEGORY, not an
accessory to this leaf category.

## relevant = NO (reason code `UNRELATED`)

Not genuinely dental at all — miscategorized into this export by keyword
overlap:

- A screwdriver-bit holder novelty desk gadget shaped like a mouth
  (`B0CM6QG71K`) — "base" here is the item's stand, "bit holder" collided
  with dental "bite"/tooth terminology in the search keyword

## relevant = UNCERTAIN (reason code `AMBIGUOUS`)

Genuine boundary cases where the listing text does not resolve the
question — route to human review, do not force YES/NO:

- **Reline diluted by competing framing or bundling** (see rule above):
  `B0H7HF45WN`, `B087YB281L`, `B0DYNFP23H` (mentions "refit liners" amid
  otherwise tooth-filling-bead framing)
- **DIY full-denture-making kits** — build base + teeth together at home;
  genuinely touches the base but is marketed and sold as a complete
  denture kit, not base material specifically: `B0D6LP1JTJ`, `B0D4CLS6S1`,
  `B0GJTGD6HH`, `B0FGXS3NLX`, `B0GKB2KPHM`. This is a real category-boundary
  question (denture base vs. complete/full denture) that text alone can't
  settle — it needs a business decision on where the leaf-category line is.
- **Finished product vs. base material**: a full silicone denture set is a
  complete prosthetic, not raw base material — but the base is
  structurally most of what it is (`B0GTV2TZY6`)
- **Ambiguous forming tools**: "Base Former Kit"/mold products that could
  be a lab tool for forming bases or a generic full-mouth teaching model
  (`B0FL1WX4KQ`)

## What NOT to use as a signal

- The word "denture" alone — present in nearly every title in this export,
  including the wrong-category majority (see `data_dictionary.md`: 73.6%
  of the raw export is 义齿修复材料, not 义齿基托, and nearly every one of
  those rows also says "denture").
- SellerSprite's own `二级类目`/`小类目` fields — useful context, but shown
  to be wrong often enough (`B01I3ZIV2M` is filed under repair material but
  is base-material-class; 3 rows in the full export are filed under this
  category despite being screwdriver/jewelry-wax products) that they can't
  be trusted as ground truth.
