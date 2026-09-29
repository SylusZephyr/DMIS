# Product Knowledge Graph — Schema-Only Foundation (Tier 2, Milestone 4)

## What was requested

A `Company Category → Leaf Category → Product Family → Product Type →
Product Model → Variant → Listing → Seller` hierarchy, with "AI
discovers product segmentation."

## What was built, and why it stops where it does

Before adding schema, the existing levels were inventoried rather than
assumed absent:

| Hierarchy level | Status | Where |
|---|---|---|
| Company Category / Leaf Category | **Already exists** | `categories` table, `config/categories.yaml` (`parent_category`/`leaf_category`) |
| Product Family | **New, added here** — `products.product_family VARCHAR`, nullable | `schema.sql`, `matching/resolution.py::build_products` |
| Product Type | **Already exists and populated** | `products.product_type`, `config/taxonomy/*.yaml` (Milestone 11) |
| Product Model | **Already exists** — `products.model VARCHAR` has existed since Milestone 12, always `None` ("no model-number extraction exists yet") | `schema.sql` |
| Variant | **Already exists conceptually**, not a new column | `product_listings` + each category's `variant_policy` in `config/categories.yaml` (e.g. `pack_quantity: separate_products` vs `color: same_product`) — this IS the system's existing notion of "same product, different variant" |
| Listing | **Already exists** | `listings` table |
| Seller | **Does not exist and was not added** | No SellerSprite export column carries a seller/merchant identity — confirmed against `docs/data_dictionary.md`, not assumed |

Only `product_family` is new. Everything else the brief asked for
already had a real, working home in the schema — adding a second,
differently-named column for something that already exists (e.g. a
second "model" field) would have been exactly the kind of duplicate,
conflicting concept PRINCIPLES.md's "never silently change business
definitions" principle warns against.

## Why `product_family` is deliberately left `NULL` for every product today

The pilot category (`denture_base`) has 11 real resolved products
across 3 real product types (wax plate, resin, reline). There is no
natural grouping *above* product_type that these 11 products would
split into — any "family" assigned today would be invented structure,
not a real distinction found in the data. PRINCIPLES.md forbids exactly
this: presenting fabricated categorization as if it were discovered.

`product_family` is real, live schema — wired through
`matching/resolution.py::build_products()` (every product row explicitly
carries the field), `src/dmie/database/repository.py`'s `_PRODUCT_COLUMNS`,
and the dashboard's Product Detail page, which shows an honest "not yet
distinguished from product type" message rather than hiding the field
or showing a blank.

## How this gets populated for real, later

Two honest paths, neither built here:

1. **A category where family-level diversity is real and evidenced** —
   e.g. the master prompt's own `micromotor` example: brushed vs.
   brushless motors are a genuine family split, with RPM tier as the
   Type level below it. `config/categories.yaml` already has a
   placeholder `micromotor` entry with a `variant_policy` reflecting
   this (`rpm_tier: separate_products`) — once a real `micromotor` raw
   export exists, `product_family` can be populated the same way
   `product_type` is: a frozen, human-authored taxonomy file, not an
   AI invention.
2. **A future explicit config-driven family-mapping step**, analogous
   to `config/taxonomy/denture_base_v1.yaml`'s frozen product-type
   taxonomy — a per-category YAML mapping product_type values to family
   groups, applied deterministically in `build_products()`. Not built
   now because `denture_base` has nothing to map.

"AI discovers product segmentation" was explicitly not built:
`src/dmie/classification/product_type_discovery.py` already exists as
an unwired, tested module from an earlier milestone (found during the
system completion audit) attempting something adjacent to this — extend
that module when this is picked up again, rather than writing a second,
parallel discovery mechanism.
