# Entity Resolution Guidelines — When Two Listings Are the Same Product

Derived from a manual review of `data/samples/gold_labels_pilot.xlsx`.
Core principle (PRINCIPLES.md): **Listing != Product.** An Amazon ASIN is not
automatically a unique product; multiple ASINs can sell the same
underlying commercial product. The reverse mistake is just as real: two
listings that *look* similar because they share a generic template title
are not automatically the same product either.

## Product Identity Rules

### Same product

**Same brand + same model + same core configuration.**

"Model" here is not necessarily a literal model-number field — this
dataset has none (see `docs/data_dictionary.md`). Until a real
model/UPC/GTIN field exists, "model" is approximated by normalized title
identity (Stage 3) with brand (Stage 2) as a corroborating signal. The
moment a real identifier field exists, Stage 1 uses it directly and takes
priority over this approximation.

"Core configuration" means the attributes that define *what the product
fundamentally is* — not every difference between two listings. A color
swatch, a cosmetic label change, or (for some categories) a size grading
within the same functional class can be the same core configuration. This
is exactly the part that must be configurable per category, not assumed
universally — see Product Family below.

### Different product

**Different model, OR a materially different configuration, OR a
different functional product.**

- Different model: a real model/UPC field disagrees (Stage 1), or the
  normalized title identity is clearly a different item.
- Materially different configuration: a configuration attribute that
  *this category's* configuration marks as identity-defining differs
  between the two listings (see Product Family below) — e.g. pack/bundle
  quantity for `denture_base` (a 2-pack is a different commercial SKU from
  the single unit, even from the same brand with a near-identical title).
- Different functional product: the listings do a different job, even if
  superficially similar in text (this is what Stages 2-4's low scores and
  Stage 7's semantic judgment are for).

### Product family — configurable per category, never hardcoded

A product family is a set of listings/products that share a brand and
core identity but differ along one or more variant dimensions. Whether a
given variant dimension makes each value a **separate product** or keeps
them as **one product family** is a business decision that differs by
category — it must never be hardcoded once for all categories.

Example from the original project brief:

```
Micromotor
 ├── 35K RPM
 ├── 40K RPM
 └── 50K RPM
```

For `micromotor`, RPM tier is plausibly identity-defining — a 35K RPM and
a 50K RPM micromotor are different products for market-sizing purposes,
not variants of one SKU. For `denture_base`, the equivalent case is color
(`B0GJTGD6HH` pink vs. `B0GKB2KPHM` "Flesh Colored", same brand
Asettlekit) — color is treated as a variant of the *same* product family,
not a separate product.

This is configured per category in `config/categories.yaml`'s
`variant_policy` block, not decided once in code:

```yaml
categories:
  denture_base:
    variant_policy:
      pack_quantity: separate_products   # 2-pack bundle != single unit
      color: same_product                # color-only variants stay one product

  micromotor:   # illustrative example -- see PROGRESS.md, no data ingested yet
    variant_policy:
      rpm_tier: separate_products        # 35K/40K/50K RPM are different products here
      color: same_product
```

`src/dmie/matching/resolution.py::resolve_pair` reads this policy for the
one variant dimension it currently has a concrete detector for
(`pack_quantity`, via `similarity.py::extract_quantities`) — see
`tests/unit/test_resolution.py` for both policy settings exercised
against the same pair. Other dimensions (color, size, RPM-style tiers)
are documented here as the intended extension point but have no dedicated
detector yet; they currently fall through to Stage 6's composite score
rather than a category-configured rule.

## Implementation (Milestone 6)

A 7-stage pipeline implements the rules below:

1. **Exact identifiers** (`resolution.py::exact_identifier_match`) — checks
   for a shared model/UPC/GTIN field. This dataset has none (see
   `docs/data_dictionary.md`), so this stage always defers today; it
   activates automatically the moment such a field exists.
2. **Normalized brand matching** (`similarity.py::brand_score`) — brand is
   the only identifier-adjacent signal available (no model field).
3. **Normalized title similarity** (`similarity.py::title_similarity`) —
   `difflib` ratio on lowercased, punctuation-stripped titles.
4. **Technical attribute similarity** (`similarity.py::attribute_score`) —
   scoped to price. Package weight/dimensions exist in the raw
   SellerSprite export but were never brought into the normalized
   `listings` schema; extending this stage to use them is a natural
   follow-up, not required to correctly resolve every case below.
5. **Candidate blocking** (`candidates.py::generate_candidate_pairs`) —
   two blocking keys, unioned: normalized brand, and a title fingerprint
   (the 3 longest non-stopword words, alphabetically tie-broken for
   reproducibility). On the 105-listing pilot this cuts ~5,460 possible
   pairs down to ~90 — over 98% fewer comparisons — while still catching
   every manually-verified case below.
6. **Fuzzy composite scoring** (`resolution.py::score_pair`) — weighted
   blend: title 0.5, brand 0.3, attribute 0.2 (`config/thresholds.yaml`).
   `>= 0.90` → confirmed MATCH. `<= 0.40` → confirmed NO_MATCH. Between
   the two → Stage 7. A separate quantity-mismatch check
   (`similarity.py::extract_quantities`) can force UNCERTAIN even above
   0.90 — see the bundle policy below.
7. **AI arbitration** (`resolution.py::ai_arbitrate`) — same
   graceful-degradation pattern as the relevance classifier: returns
   `None`, never a guessed verdict, when no AI provider is configured, and
   the pair stays `UNCERTAIN`/`needs_review`.

Confirmed MATCH pairs are clustered into `product_id` groups via
union-find (`resolution.py::cluster_products`). Every listing gets exactly
one `product_id` — clustered listings share one, and an unmatched listing
gets its own stable singleton `product_id`, so "listing != product" holds
for the whole dataset, not just the pairs that happened to get compared.

**On the real 105-listing pilot:** 91 candidate pairs survived blocking;
14 confirmed MATCH, 1 confirmed NO_MATCH, 76 UNCERTAIN (the uncertainty
queue — exported to
`data/exports/denture_base/product_matching_uncertainty_queue.csv`). 105
listings resolved to 92 products, 24 of them via a confirmed multi-listing
match. Reproduce with `python scripts/resolve_products.py`.

## Confirmed merge (shared `product_id`, `status = confirmed`)

Require **all** of: near-identical/identical title, same brand, and
matching price (and rating, if available). This is the bar for a
zero-doubt merge — same listing content duplicated under two ASINs.

- Example: `B0H14R7BBY` and `B0H14ZK7HR` — identical title, brand (JEDIA),
  price ($33.99), and rating (4.4). Same product, two ASINs.

## Candidate merge — flag `needs_review`, do not auto-merge

Near-identical or identical title, but **brand and/or price differ** in a
way the title doesn't explain. This is a real pattern in this market —
the same white-label product resold under different tiny storefront
"brand" names — but it cannot be confirmed from title/price/brand alone.

- `B0GVB7J6PV` (yuanyuuo, $5.49, "1 Box") vs. `B0GW5CNJ6F` (Generic,
  $8.49, "2 Box") — near-identical title, brand differs, price differs by
  more than the stated pack-size difference explains.
- `B0FQ1LTCSS` (Mzcarewr, $6.97) vs. `B0GWTP6GQF` (XLMCWT, $9.99) —
  title differs only by a "1Pack" suffix, brand differs.
- `B0GJTGD6HH` (Asettlekit, $22.99, pink) vs. `B0GKB2KPHM` (Asettlekit,
  $39.99, flesh-colored) — same brand, title differs only by color, but
  the ~1.7x price gap isn't explained by color alone. Same brand pushes
  this toward "plausible variant," but the unexplained price gap keeps it
  out of the confirmed bucket.

**Rule:** any brand mismatch, or any price gap not explained by an
explicit pack-size/quantity difference stated in the title, keeps a pair
at `needs_review` rather than a confirmed merge. Don't fabricate
certainty a human hasn't verified against the actual listing pages.

## Explicit non-merge: bundles and multi-packs

A multi-pack/bundle listing of the same base product is **not** merged
with its single-unit counterpart, even when brand and base title match
exactly — bundle-vs-single is its own market-analysis dimension (see the
project brief's "sell as single product or with accessories" question),
not a listing-deduplication case. Merging them would erase that signal
before the bundle-analysis milestone ever gets to use it.

- `B00VQTLM74` ("D.O.C. Repair-It ... 3 ea") and `B00E4MPAIW` (same product,
  "... 3 ea (Pack of 2)") — same brand, explicit bundle suffix in the
  title. Kept as separate products; noted as the same underlying base
  product for later bundle analysis, not given a shared `product_id`.

## Weak-similarity guard: don't merge on genre alone

A saturated sub-market (here: "moldable thermal beads for tooth repair")
produces many competing products from different brands that all copy a
similar SEO title template. High textual similarity from shared generic
phrasing is **not** evidence of being the same product unless it comes
with a brand and/or price match (or an explicit pack/color-only
difference). Measured pairwise title similarity in this sample:

| Similarity | Brand match? | Price match? | Verdict |
|---|---|---|---|
| 1.00 | yes | yes | confirmed merge |
| 0.95–0.98 | no / same-but-unexplained-price | no | needs_review |
| 0.90 | yes | no (explicit bundle) | explicit non-merge (bundle) |
| ≤0.84 | no | no | not a cluster — generic template overlap only |

Pairs at or below ~0.84 similarity in this sample (e.g. two different
"Dental Lab Denture Repair Glue Kit" adhesives from different brands, or
two different "Tooth Repair Kit" bead products) were **not** flagged as
candidates at all — that similarity level is explained by market
saturation and copied listing templates, not shared product identity.

## What "same product" means here (and what it doesn't)

- Same product = same underlying commercial item, possibly listed under
  more than one ASIN (duplicate listing, or a plausible reseller/private-label
  duplicate pending confirmation).
- **Not** the same product: a different pack size/bundle of the same base
  item (kept separate deliberately), or two different brands' competing
  items that merely describe themselves similarly.
- A confirmed cluster gets one shared `product_id` across all its
  listings. A candidate cluster gets `status = needs_review` and a `notes`
  entry explaining exactly what doesn't line up — never a guessed
  `product_id`.
