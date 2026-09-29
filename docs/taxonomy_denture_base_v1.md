# Denture Base Product Taxonomy — v1 (FROZEN)

**Status: frozen 2026-09-21.** This is the approved output of M11 Stage 1
(discovery) plus human review — not the discovery draft. For the original
candidate-by-candidate analysis (ASINs, connecting words, why clusters
formed, open issues raised), see `docs/product_taxonomy.md`; this document
records what was actually decided and why, and is the source a classifier
should be built against.

Machine-readable form: `config/taxonomy/denture_base_v1.yaml`.

## The frozen taxonomy

| id | name | definition |
|---|---|---|
| `DB_RESIN` | Denture Base Resin | Resin/acrylic materials used for fabrication of denture bases. |
| `DB_WAX_PLATE` | Denture Base Wax Plate | Wax plates or sheets used in denture fabrication workflows. |
| `DB_RELINE` | Denture Reline Material/Kit | Materials or kits used to reline, adjust, or repair existing dentures. |

Flat, 3-way, no hierarchy, no "Other" bucket.

## How each type maps to what discovery found

- **`DB_WAX_PLATE`** = discovery's CAND_01 ("Base Plate Wax," 9 listings /
  8 distinct products, 100% internal agreement on "plate"+"wax"). Renamed
  for clarity; kept as one type — pack-size/weight variance (240g–480g,
  10–20pcs) and the latent bite-wax-vs-casting-wax sub-pattern both stay
  folded into this one type for v1.
- **`DB_RELINE`** = discovery's CAND_02 ("Reline Kit," 2 listings, 2
  distinct products, 100% agreement on 10 shared words including
  "reline" itself). The algorithm's auto-suggested label ("Adjustment
  Curing") was a tie-break artifact among equally-scored words, not a
  real judgment — explicitly rejected in favor of "Reline" as the
  functional purpose the two listings actually share.
- **`DB_RESIN`** = discovery's CAND_03 (`B0CXTC26TV`, a singleton).
  Approved as its own type despite n=1 — a deliberate rejection of
  "fold small clusters into Other," on the reasoning that this is a core,
  commercially meaningful category (denture base resin/acrylic is one of
  `docs/classification_guidelines.md`'s three EXACT_MATCH sub-categories)
  and sample size should not dictate whether the *market* has this type,
  only how much evidence we currently have for it.

## Decisions made (and explicitly rejected alternatives)

1. **Keep the three types separate**, not merged — they represent
   different customer purposes/use cases (fabrication vs. preparation/
   forming vs. modification/repair), not just different vocabulary
   clusters.
2. **The `DB_RESIN` singleton is a real type, not "Other."** "Other" is
   reserved for cases where the product's purpose genuinely cannot be
   identified — not for "we only have one example so far."
3. **No hierarchy for v1.** Flat list. A subtype hierarchy (e.g. `DB_RESIN`
   split into Heat Cure / Self Cure / Flexible) was explicitly rejected as
   premature — there isn't enough data yet to justify it.
4. **No `DB_WAX_PLATE` subtypes for v1**, even though a latent
   bite-wax-vs-casting-wax split is visible in the discovery data (4 vs. 4
   listings) — rejected as premature at this sample size, not because the
   pattern is imaginary.
5. **No automatic taxonomy expansion.** New listings appearing in future
   ingests must NOT silently add, split, or rename types in this file.
   Any structural change requires a new versioned file
   (`denture_base_v2.yaml`, etc.) and a fresh discovery → review →
   approval cycle, mirroring this one — never a silent edit to v1.

## What this document does NOT cover

No classifier has been built against this taxonomy. No listing or product
in the database has been assigned any of these `id`s. Building the
classifier and running it against the full dataset is the next, separate
step (M11 Stage 2) — explicitly not part of this freeze.
