# Product Taxonomy — Candidate Review (M11, Stage 1: Discovery)

**Status: DRAFT — awaiting your approval. taxonomy_v1 is NOT frozen. No
classifier exists. No listing or product has been assigned a product_type
based on anything in this document.**

Per the explicit M11 process constraint, discovery and classification must
not happen in the same step: this document is the discovery output only,
for you to approve, merge, split, or rename. Once you freeze taxonomy_v1
from this review, the next (separate) step builds a classifier against
whatever you approve here — not against these auto-generated cluster
labels.

## Scope

- Category: `denture_base` (义齿基托)
- Population: the **12 listings currently classified `RELEVANT`**
  (relevance classification, Milestone 5), snapshotted 2026-09-21. This is
  deliberately not the full 105-listing raw export — product-type
  discovery only applies to listings already confirmed in-category;
  running it over IRRELEVANT/UNCERTAIN listings would conflate "what kind
  of denture-base product is this" with "is this denture-base at all,"
  which relevance classification already answers.
- Note on grain: these 12 *listings* resolve (via entity resolution,
  M4/M12) to **11 distinct *products*** — `B0CXMQ7DFZ` and `B0H6RLQ6YZ`
  are identical listings (same title, same brand "LA MIERE") already
  merged into one product (`Pf598530a9a`). Product-type discovery runs at
  the listing grain below; see "Cross-cutting issues" for why that's fine
  here but worth remembering when the classifier is built.

## Method (for transparency only — not itself up for approval)

`src/dmie/classification/product_type_discovery.py`: fully deterministic,
no LLM. Generic (non-category-specific) stopwords are removed from each
title, then listings are clustered by union-find over "connector words" —
words shared by at least 2 listings but present in at most 90% of the
population (the upper bound excludes words like "base," which is ~100%
prevalent here since that's literally what makes these listings relevant
in the first place — see `docs/classification_guidelines.md`'s own
warning against gating on "base"). This is a starting point for your
judgment, not a semantic authority: it groups by shared *vocabulary*, not
by understanding what a product *is*.

---

## CAND_01 — proposed name: **Base Plate Wax** — 9 listings (8 distinct products)

| ASIN | Product | Brand | Price | Monthly sales |
|---|---|---|---|---|
| B0CXMQ7DFZ | General USE 20PCS 270g Medium Soft Base Plate Wax Molding Casting Wax Sheet Modeling Filling NO Denture | LA MIERE | $8.99 | 50 |
| B0H6RLQ6YZ | *(identical to B0CXMQ7DFZ — same product, `Pf598530a9a`)* | LA MIERE | — | 50 |
| B09JL2CYKR | Dental Base Plate Wax 18 PCS, Denture Red Utility Bite Casting Sheets for Orthodontic Modeling Filling Laboratory Supply | Impressive Smile | $9.99 | — |
| B0CDM8JCLH | 250g Dental Base Plate Wax Molding Casting Wax Sheet Denture Material Red Utility Wax Sheets for Dentist or Jewelry Lab Dentist (1 Box) | Misemiko | $12.99 | — |
| B0DRBMXKZR | Dental Base Plate Wax 20pcs Red Denture Base Plate Casting Modling Wax Sheet, Dental Denture Materials for Modeling Filling Lab Dentist Auxiliary Material | HARMONYDENT | $15.99 | — |
| B094YBT6VD | 10pcs Red Base Plate Wax Sheets, 2.0mm Utility Bite Wax for Jewelry Carving, Denture Casting, and Modeling, for Crafting Rings, Earrings, Bracelets, and Lab Equipment | EVTSCAN | $15.99 | — |
| B0F26TYZQD | General USE Large 20PCS 480g Base Plate Wax Molding Casting Wax Sheet Modeling Filling NO Denture | LA MIERE | $16.99 | — |
| B07DYMJ7TQ | Base Plate Wax Orthodontic Dental Wax Sheets 20PCS, Red Utility Bite Wax Denture Casting Wax Sheet Supply for Modelling|Filling|Lab Equipment - 12 Months Warranty | Annhua | $16.99 | — |
| B0F7LKY5V7 | Dental Base Plate Wax Sheets 20 Pcs 240g | For Denture Modeling, For Orthodontic Work, Dental Lab Supplies | Kouyot | $8.88 | — |

**Why grouped:** every one of the 9 listings shares both **"plate"** and
**"wax"** — a 100% internal-agreement cluster, the strongest possible
signal this algorithm can produce. Secondary shared terms (not universal,
but present in a majority): "20pcs" (5/9), "red" (5/9), "utility" (4/9),
"bite"/"orthodontic" (3/9 each).

**Possible category-definition issues:**

1. **Mixed marketing intent inside the cluster.** Two listings market
   explicitly toward general/jewelry use rather than exclusively dental
   lab use — B094YBT6VD ("...for Jewelry Carving...Crafting Rings,
   Earrings, Bracelets") and B0CDM8JCLH ("...for Dentist **or Jewelry
   Lab** Dentist"). They were correctly classified `RELEVANT` under the
   existing rules (the wax itself is denture-base-class material
   regardless of secondary marketing), but if you want market-sizing to
   distinguish "dental-specific demand" from "general-purpose wax also
   bought by non-dental buyers," this cluster currently doesn't separate
   that. Not necessarily a problem — flagging for your judgment.
2. **A latent sub-pattern exists but isn't forced by the data.** Four
   listings frame themselves as "utility/bite wax" for orthodontic bite
   registration (B07DYMJ7TQ, B094YBT6VD, B09JL2CYKR, B0DRBMXKZR) vs. four
   framed as plain "base plate wax for modeling/casting" (B0CXMQ7DFZ,
   B0H6RLQ6YZ, B0F26TYZQD, B0F7LKY5V7). Both sub-groups still share
   "plate"+"wax" universally, so the algorithm correctly did not split
   them — but if "bite wax" and "casting/modeling wax" are meaningfully
   different products to you, this is where a manual split would go.
3. **Pack size/weight is folded in, not split out** (240g/250g/270g/480g;
   10/18/20pcs). This matches `config/categories.yaml`'s existing
   `pack_quantity: separate_products` policy operating at the *product*
   level (different pack size = different product), while staying *one
   type* here — consistent, but noting it since it's a design choice, not
   an automatic consequence.
4. **B0CXMQ7DFZ / B0H6RLQ6YZ are the same product counted twice** in this
   9-listing count (see Scope note above) — 8 distinct commercial items,
   not 9.

---

## CAND_02 — proposed name: **Reline Kit** — 2 listings (2 distinct products)

| ASIN | Product | Brand | Price |
|---|---|---|---|
| B0G81P76D8 | Self-Curing Hard Denture Reline Kit for Home Use, Complete Denture Base Renewal and Fit Adjustment Set, Pink, Includes Powder, Liquid, Bonding Solution and Accessories | Generic | $77.00 |
| B0FMK8XB26 | Hard Denture Reline Kit – Long-Lasting Denture Base Repair & Fit Adjustment, Acrylic-Based, Self-Curing, Translucent Pink – Includes Powder, Liquid, Primer & Tools | Generic | $69.90 |

**Why grouped:** these two share **10 connector words** — reline, hard,
self, curing, fit, adjustment, includes, powder, liquid, pink — every one
of them at 100% internal agreement (2/2). This is a much stronger,
cleaner signal than the auto-generated label suggests: the code's
suggested name ("Adjustment Curing") is an artifact of a 10-way tie in the
word-ranking step (all 10 words tie at count=2, and the tie-break picks
alphabetically among the top 2) — not a judgment that those two words are
more meaningful than "reline." **"Reline Kit" is the recommended human
label**, directly supported by both titles and by
`docs/classification_guidelines.md`'s own framing of this listing pair as
"reline/base-rework is the sole or clearly dominant described function."

**Possible category-definition issues:**

1. **Naming artifact, not a data problem** — see above; recommend
   overriding the auto-label to "Reline Kit" (or your preferred term) when
   approving.
2. **These two were never compared by entity resolution at all.** They
   didn't survive Stage 5 blocking as a candidate pair (`match_candidates`
   has zero rows for this ASIN pair), despite sharing 10 distinctive
   words — a stronger vocabulary overlap than several pairs that *did* get
   compared. This is a blocking-strategy question for entity resolution
   (M6), not a taxonomy question — flagging it here only because it's
   directly visible from this data, not asking you to resolve it in this
   document.
3. **Small cluster, real price gap** ($69.90 vs $77.00, ~10%) on
   otherwise near-identical language — plausibly the same underlying
   formulation from different generic/white-label sellers, which is
   exactly the kind of case `docs/entity_resolution.md`'s variant policy
   is meant to adjudicate, again a separate concern from product *type*.

---

## CAND_03 — proposed name: **Denture Base Resin** — 1 listing (singleton)

| ASIN | Product | Brand | Price |
|---|---|---|---|
| B0CXTC26TV | Antinsky Denture Base Resin | Antinsky | $59.00 |

**Why grouped:** it isn't — this listing shares no connector word with any
other RELEVANT listing (the title is only 3 significant words: "antinsky,"
"resin," "base," and "base" itself is excluded from clustering as
~100%-prevalent). It is a genuine singleton, not a dropped/failed record.

**Possible category-definition issues:**

1. **Single data point.** `docs/classification_guidelines.md` treats
   "denture base resin/acrylic" as its own EXACT_MATCH sub-category
   (alongside base-plate wax and reline kits), so a dedicated "Denture
   Base Resin" type is well-supported conceptually even with n=1 — but
   with only one listing, there's no internal evidence yet to say whether
   future resin listings would actually cluster with this one on vocabulary
   (the title is generic/minimal — "Antinsky Denture Base Resin" — and
   gives the algorithm very little to match against).
2. **Alternative:** fold into a catch-all "Other / Uncategorized" type
   for v1 rather than a dedicated single-member type, if you'd rather not
   commit to a resin category on one example. Both are defensible; this
   is your call.

---

## Cross-cutting issues (apply to the taxonomy as a whole)

- **Coverage is small and category-specific.** 12 listings, 3 candidates,
  one category (`denture_base`). This taxonomy is not validated against
  any other leaf category yet (e.g. micromotor's brushed/brushless split
  from the original brief) — the discovery *code* is written to be
  category-agnostic (no denture-specific words are hardcoded), but that's
  an untested claim until it's actually run against a second category's
  data.
- **Type is orthogonal to product identity.** CAND_02's two listings share
  a type ("Reline Kit") but are two separate *products* per entity
  resolution (different singleton `product_id`s) — a concrete
  illustration of the Category → Product Type → Product → Listings
  hierarchy: same type, different product. Conversely, CAND_01's
  `B0CXMQ7DFZ`/`B0H6RLQ6YZ` are the same *product* (two listings, one
  entity) that obviously also share one type. Once a classifier exists,
  the product-level `product_type` should be assigned by aggregating
  across a product's listings (already implemented as a mode-vote in
  `matching/resolution.py::build_products`, currently always `None`
  because no listing has a classified type yet) — not decided
  independently per listing.
- **Discovery ran at listing grain, not product grain**, for a simple
  reason: at this population size, doing it per-product first would have
  meant deciding merges/splits on titles from only 11 items instead of
  12, a negligible difference here — but worth remembering if a future
  category has many more duplicate-listing products, where listing-level
  discovery could over-weight a heavily-duplicated product's vocabulary.
- **No hierarchy has been proposed** (flat 3-way list). Nothing in this
  data currently forces one (unlike the brief's brushed/brushless
  micromotor example) — CAND_01's "utility/bite wax" vs. "casting wax"
  sub-pattern (see above) is the closest candidate for a parent/child
  split, entirely your call whether it's worth it at this scale (4 vs. 4
  listings).

## Decisions needed to freeze taxonomy_v1

1. **Approve, merge, split, or rename** each of the 3 candidates. Suggested
   defaults if no changes are wanted: `Base Plate Wax` (9 listings),
   `Reline Kit` (2 listings), `Denture Base Resin` (1 listing).
2. **CAND_01 sub-split?** Keep as one "Base Plate Wax" type, or split into
   "Bite/Utility Wax" vs. "Casting/Modeling Wax"?
3. **CAND_03 handling?** Its own type (`Denture Base Resin`), or fold into
   a catch-all `Other`/`Uncategorized` type for v1 given n=1?
4. **Flat or hierarchical for v1?** A flat 3-way (or however many result
   from #1–3) list, or a parent/child structure?
5. **Where should the frozen taxonomy live?** Not decided or built yet —
   candidates are `config/categories.yaml` (alongside `variant_policy`,
   per-category) or a new dedicated `config/product_taxonomy.yaml`. No
   code changes have been made for this; deferred until you've answered
   1–4.

Once you've answered these, the next step is building the classifier
against the frozen list — a separate step, not part of this document.
