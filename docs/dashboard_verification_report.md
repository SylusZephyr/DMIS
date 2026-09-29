# Dashboard Verification Report

Real browser check (Streamlit, localhost:8501), 2026-09-22, against `HEAD`
(`05c1b0a`) — first re-check since M12; specifically verifying the M11
Stage 3 product-type data and the `category_market_metrics` bug fix
actually render correctly, not just query correctly in SQL.

## Pages checked

| Page | Result |
|---|---|
| Home | PASS — real counts (11 products, 12 relevant listings, 105 raw), no errors |
| Category Overview | **PASS — confirms the bug fix visually.** Product-Type Distribution pie chart now shows `DB_WAX_PLATE 72.7%`, `DB_RESIN 9.09%`, `DB_RELINE 9.09%`, `unclassified 9.09%` — previously would have shown 100% unclassified. Price/sales distributions render correctly. |
| Market Map | PASS — X=price, Y=observed monthly sales, "1 of 11 **products** plotted" (confirms product-level, not listing-level, per the core "listing != product" requirement). Hover tooltip shows product image context (name, price, sales, listing count, product_id, brand, rating). Clicking a gallery "Select" button correctly navigates to Product Detail with that exact product loaded. Product image gallery renders real images for all 11 products, not broken links. Products with no sales data are explicitly listed separately ("10 products with no observed sales data (not plotted)") rather than plotted at a fabricated 0 — the "None never 0" rule visibly enforced in the UI, not just in code. |
| Product Detail | PASS — image, product type (`DB_WAX_PLATE`, real, post-fix), brand, price, rating, listing count, best-listing logic, pain points/opportunity sections all render with real data or honest "no data" messages. |
| Listing Explorer | PASS — full listing → classification → product-assignment trace renders correctly; `category_id: "denture_base"` confirms the M15 `category_id` bug fix is holding. |
| Data Quality Center | PASS — real counts matching the live DB exactly (105/12/65/28, 92 products, 28 needs-review, 16% clusters reviewed). |
| Evaluation Report | PASS — real precision/recall/F1 against the 50-row gold set, real matching breakdown (16% automatic / 0% AI-assisted / 84% pending), both with honest explanations for why AI-assisted is 0%. |

**Console/network:** the only errors on every page are `404`s for
`/<Page_Name>/_stcore/health` and `/<Page_Name>/_stcore/host-config` —
Streamlit's own internal health-check requesting a page-relative path
before falling back to the root (which immediately succeeds, `200 OK`,
visible in the same network log). This is a Streamlit framework
artifact of direct-URL navigation to a sub-page, not an application bug —
confirmed by it appearing identically on every page regardless of what
that page actually does.

## Confirmed working (explicitly required by the original brief)

- Market Map bubbles represent **products**, never listings.
- Bubble hover shows image context, name, brand, type, price, sales,
  revenue-adjacent data, listing count.
- Click-through from a Market Map product to its Product Detail page
  works.
- Product images load (gallery and detail page both).
- Missing data is shown as an explicit "no data" state, never silently
  defaulted to 0.

## Gap found (not a bug — a missing feature, not fixed here)

**No filter controls exist on the Market Map** — no product-type,
price-range, sales-range, brand, or rating filter widgets, confirmed by
reading the page's interactive elements directly (only a category
selector and Plotly's own built-in zoom/pan/box-select/lasso/download
controls exist). Several earlier prompts this session asked for these
filters explicitly. This is a real, honest gap against that ask, but it's
a **missing feature, not a defect** — nothing is broken, incorrect, or
misleading. Per this session's own established discipline ("only fix
confirmed bugs, don't add new features without being asked" — reinforced
multiple times across the last several turns), I did not build filter
UI as part of this verification pass. Flagging it here rather than
silently building it or silently ignoring it; say the word if you want
it built next.

## Not re-verified

Mobile/responsive layout, and behavior with `ANTHROPIC_API_KEY`
configured (no real AI response has ever rendered in any AI-dependent UI
element, since none has ever been available to render).
