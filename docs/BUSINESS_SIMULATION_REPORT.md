# Business simulation — `implants`

Input: `implants_sellersprite.xlsx` (45 KB), no review file, no supplier list. Fresh temporary platform; every figure below is an API response.

Context markets processed first: dental_models, denture_base.


## 1. Upload

Job `49bc77952bcc4380` finished in 4.9 s through 18 stages: ingestion (0.1s) → cleaning (0.3s) → relevance (0.2s) → clustering (0.4s) → product_resolution (1.5s) → analytics (0.2s) → forecasting (0.0s) → customer_pain (0.0s) → suppliers (0.0s) → opportunity (0.0s) → history (0.0s) → confidence (0.1s) → trends (0.1s) → competitors (0.2s) → lake (0.1s) → events (0.0s) → vectors (0.3s) → knowledge_graph (0.1s).


## 2. Clean

- 279 raw rows → 279 accepted, 0 rejected.
- Schema detected by the `sellersprite` adapter; 11 columns mapped, unmapped: 图片, 二级类目, FBA($), 包装重量（单位换算）, 包装尺寸（单位换算）, 包装尺寸分段, 保底生产成本, 产品经理.
- Quality: dataset confidence 85/100, 276 of 279 records usable; issues {"outlier_isolation_forest": 4, "revenue_inconsistent": 1, "duplicate_record": 3}.

## 3. Identify dental products

- 279 relevant, 0 uncertain (review queue), 0 irrelevant — each with a per-signal explanation.
  - excluded `B0HCDRPQ1T`: low data quality: duplicate_record — GRIN Softstx, Gentle Dental Picks, 90 Count, Soft Flexible Bristles, Textured Pl
  - excluded `B0HBCKXXTT`: low data quality: duplicate_record — Locator Core Tool - 3 in 1 Universal Dental Implant Instrument for Male Cap Inse
  - excluded `B0CSYB8D8Q`: low data quality: duplicate_record — 70 Count Dental Floss Threaders for Braces, Bridges, and Implants, Orthodontic F

## 4. Cluster products

- 276 listings → **180 products** (52 with several listings).
- Hierarchy: 5 families → 20 segments → 57 models → 180 variants. Products by model basis: {"text": 81, "none": 40, "spec": 59} (none = no shared model found).
  - teeth / implants → flosser, water, gum → accepted · dental braces → 660 · aquarius · multiple
  - floss / braces → floss, threaders, braces bridges (100 pcs) → eez · dental flossers → GUM
  - elevators / pdl → pdl, elevators, restorative composite → elevators · osteotome → 60 · 77r · elevator

## 5. Market size

- Observed **$5,506,750/month** ($66,081,000/year); sales known for 34% of products, so this is a lower bound.
- Market confidence 88.8/100 (high); 60 high- and 39 low-confidence products.
- Example — Waterpik Aquarius Countertop Water Flosser with 10 Settings, 7 Tips, W: 90.0% because ✓ sellersprite source (reliability 85%); ✓ sales observed on 4 of 4 listing(s); ✓ 4 listings corroborate this product; ✓ 90% of key fields present; ✓ passes all quality checks; ✓ relevance confidence 98%; ✗ rating present but review count not in source; ✗ one snapshot — consistency measurable after the next upload.
- Trend: **Growing** (25.0% confidence) — 69 listings launched in the last 12 months vs 30 the year before; 18% of revenue from listings under 12 months old; expected 12-month growth: not estimable from a single snapshot.

## 6. Find opportunities

- **rigid, floss expanding, easythread** — opportunity 58/100 (High), $16,765/month, highly concentrated, trend Insufficient evidence; strong: competition 0.85, market size 0.75; weak: review problems 0.21
- **cleaner, foam, crowns bridges** — opportunity 50/100 (Moderate), $10,844/month, highly concentrated, trend Stable; strong: price opportunity 0.95, margin opportunity 0.70; weak: entry difficulty 0.09
- **flosser, water, gum** — opportunity 49/100 (Moderate), $5,216,214/month, highly concentrated, trend Growing; strong: market size 1.00, margin opportunity 0.95; weak: entry difficulty 0.00
- **toothbrush, tuft, soft (4 pcs)** — opportunity 48/100 (Moderate), $50,136/month, highly concentrated, trend Mature; strong: market size 0.83, demand growth 0.48; weak: margin opportunity 0.05
- **implants built, orthodontic dental, threader spongy (4 pcs)** — opportunity 47/100 (Moderate), $15,886/month, highly concentrated, trend Growing; strong: market size 0.67, competition 0.57; weak: entry difficulty 0.00

Competitors:
- Waterpik: Leader, 95% share by revenue, price index 1.384; weakness: High price: median +38% vs segment median → Affordable alternative at or below the segment median price
- GUM: Niche, 2% share by revenue, price index 1.609; weakness: High price: median +61% vs segment median → Affordable alternative at or below the segment median price
- DenTek: Niche, 1% share by revenue, price index 0.91; weakness: Narrow range: 1 of 20 segments → Adjacent segments it does not cover
- Veexio: Niche, 0% share by revenue, price index 1.095; weakness: Narrow range: 1 of 20 segments → Adjacent segments it does not cover
- VINSULLA: Niche, 0% share by revenue, price index 1.089

## 7. Recommend products

- Suggested development: **rigid, floss expanding, easythread — floss · braces**.
- Launch simulation for “rigid, floss expanding, easythread” at $18.6: attractiveness **32/100 (Unattractive)**, positioning None, main risk: Existing dominance (GUM holds 100% of the segment).
- Unit economics: margin $10.21 per unit (median '保底生产成本' of 19 comparable listings; fulfilment median 'FBA($)' of 19 comparable listings).
  - Strategy: Position in the middle tier: it earns 2.0x its share of products in revenue
  - Strategy: Against GUM: affordable alternative at or below the segment median price
  - Strategy: Import a supplier list to find manufacturers for this segment
- Focus for 覃柳敏: [high] Evaluate a launch in 'wire, braces, teeth storage'; [high] Evaluate a launch in 'silicone, upper lower, set'; [high] Evaluate a launch in 'lab, bonding, glue'; [medium] Find manufacturers for dental_models.

## 8. Find suppliers

- No supplier list is loaded, so no manufacturer is named (suppliers are never generated). Import one on the Suppliers page, with `dmis.py import-suppliers FILE`, or through the supplier_feed connector; matches and the supplier-availability score then fill in on the next run.

## 9. Display in 3D

- Product Galaxy: 180 products positioned in 3D (size = sales, colour = opportunity, brightness = growth — 159 with a growth value), 112 similarity links.
- Market Universe: 3 markets in 5 industry branches; Global Earth: 1 countries from real marketplace/supplier fields.
- Knowledge graph around the category: 208 nodes ({"Product": 180, "Segment": 20, "Industry": 2, "ProductFamily": 5, "Category": 1}), 623 edges.

## 10. Answer analyst questions

**Q: What products should we develop?** — intent `opportunities`, scope implants

> Top opportunities in implants:
> 1. **rigid, floss expanding, easythread** (implants) — opportunity 58/100
>    Reason: highly concentrated competition (top brand 100%); demand $16,765/month observed
>    Suggested development: rigid, floss expanding, easythread — floss · braces
> 2. **cleaner, foam, crowns bridges** (implants) — opportunity 50/100
>    Reason: trend Stable; highly concentrated competition (top brand 91%); demand $10,844/month observed
>    Suggested development: cleaner, foam, crowns bridges — 50 ml
> 3. **flosser, water, gum** (implants) — opportunity 49/100
>    Reason: trend Growing; highly concentrated competition (top brand 100%); demand $5,216,214/month observed
>    Suggested development: flosser, water, gum — accepted · dental braces
> 4. **toothbrush, tuft, soft (4 pcs)** (implants) — opportunity 48/100
>    Reason: trend Mature; highly concentrated competition (top brand 56%); demand $50,136/month observed
>    Suggested development: toothbrush, tuft, soft (4 pcs) — 1 pcs (4 pcs)
> 5. **implants built, orthodontic dental, threader spongy (4 pcs)** (implants) — opportunity 47/100
>    Reason: trend Growing; highly concentrated competition (top brand 100%); demand $15,886/month observed
>    Suggested development: implants built, orthodontic dental, threader spongy (4 pcs) — floss · braces implants (4 pcs)

**Q: Which markets are growing?** — intent `growth`, scope all markets

> Market trends in all markets:
> - **implants**: Growing (confidence 25%) — 69 listings launched in the last 12 months vs 30 the year before; 18% of revenue from listings under 12 months old
> - **dental_models**: Mature (confidence 22%) — 52 listings launched in the last 12 months vs 73 the year before; 12% of revenue from listings under 12 months old
> - **denture_base**: Insufficient evidence (confidence 0%)
> 
> Growing segments:
> - wire, braces, teeth storage (dental_models): Emerging, confidence 35%
> - tooth gaps, kids, pre size (65 pcs) (implants): Emerging, confidence 32%
> - flosser, water, gum (implants): Growing, confidence 30%
> - implants built, orthodontic dental, threader spongy (4 pcs) (implants): Growing, confidence 29%
> - fixed, mount, training (dental_models): Emerging, confidence 22%

**Q: Who are our competitors?** — intent `competitors`, scope all markets

> Leading competitors in all markets:
> - **Ultrassist** (dental_models): Leader, 21% share by revenue
> - **Dentalmall** (dental_models): Challenger, 17% share by revenue; weakness: High price: median +28% vs segment median; opportunity: Affordable alternative at or below the segment median price
> - **Ceonam** (dental_models): Follower, 8% share by revenue; weakness: Lower rating: 4.1 vs market 4.3; opportunity: Premium / better-specified alternative
> - **ROTADENT PROCARE** (dental_models): Follower, 7% share by revenue; weakness: Lower rating: 4.0 vs market 4.3; opportunity: Better-quality alternative
> - **NEW HORIZON** (dental_models): Follower, 7% share by revenue; weakness: Narrow range: 1 of 27 segments; opportunity: Adjacent segments it does not cover
> - **EXECELLENT & ECONOMICAL** (denture_base): Leader, 26% share by revenue; weakness: High price: median +151% vs segment median; opportunity: Affordable alternative at or below the segment median price
> - **Dentemp** (denture_base): Challenger, 24% share by revenue; opportunity: Premium / better-specified alternative
> - **Generic** (denture_base): Challenger, 8% share by revenue
> - **MitreClamp** (denture_base): Challenger, 8% share by revenue; weakness: Narrow range: 1 of 11 segments; opportunity: Adjacent segments it does not cover
> - **Kedicare** (denture_base): Follower, 6% share by revenue; weakness: Narrow range: 1 of 11 segments; opportunity: Adjacent segments it does not cover
> - **Waterpik** (implants): Leader, 95% share by revenue; weakness: High price: median +38% vs segment median; opportunity: Affordable alternative at or below the segment median price
> - **GUM** (implants): Niche, 2% share by revenue; weakness: High price: median +61% vs segment median; opportunity: Affordable alternative at or below the segment median price
> - **DenTek** (implants): Niche, 1% share by revenue; weakness: Narrow range: 1 of 20 segments; opportunity: Adjacent segments it does not cover
> - **Veexio** (implants): Niche, 0% share by revenue; weakness: Narrow range: 1 of 20 segments; opportunity: Adjacent segments it does not cover
> - **VINSULLA** (implants): Niche, 0% share by revenue

**Q: Who can manufacture this product?** — intent `suppliers`, scope denture_base

> No suppliers in the database yet. Import a supplier list (Suppliers page, `dmis.py import-suppliers`, or the supplier_feed connector) — suppliers are never invented.

**Q: What happens if we launch rigid, floss expanding, easythread at $18.6?** — intent `launch`, scope implants

> **rigid floss expanding easythread** at $18.60 → implants / rigid, floss expanding, easythread
> Market attractiveness: **32/100** (Unattractive)
> Expected positioning: n/a
> Main risk: Existing dominance — GUM holds 100% of the segment
> Recommended strategy:
> - Position in the middle tier: it earns 2.0x its share of products in revenue
> - Against GUM: affordable alternative at or below the segment median price
> - Import a supplier list to find manufacturers for this segment
> Unit margin: $10.21 (median '保底生产成本' of 19 comparable listings)

**Q: What should 覃柳敏 focus on?** — intent `employee`, scope employee 覃柳敏

> Focus for **覃柳敏** (37 categories, 2 new alerts):
> - [high] Evaluate a launch in 'wire, braces, teeth storage' — opportunity 58/100, trend Emerging, $3,145/month
> - [high] Evaluate a launch in 'silicone, upper lower, set' — opportunity 67/100, trend Insufficient evidence, $34,183/month
> - [high] Evaluate a launch in 'lab, bonding, glue' — opportunity 57/100, trend Insufficient evidence, $29,087/month
> - [medium] Find manufacturers for dental_models — no supplier in the database matches this market's segments
> - [medium] Upload next month's dental_models export — one snapshot only: trends, forecasts and consistency need a second one
> - [medium] Find manufacturers for denture_base — no supplier in the database matches this market's segments
> - [medium] Upload next month's denture_base export — one snapshot only: trends, forecasts and consistency need a second one
> - [medium] Counter EXECELLENT & ECONOMICAL: affordable alternative at or below the segment median price — High price: median +151% vs segment median
> - [low] Review 2 uncertain dental_models listing(s) — relevance could not be decided automatically
> - [low] Review 1 uncertain denture_base listing(s) — relevance could not be decided automatically


---
Total time 19.0 s. Platform data: `/tmp/dmis_sim_8paxv0zt` (temporary).
