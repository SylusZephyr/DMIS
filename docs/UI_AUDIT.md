# UI audit (current frontend, `frontend/`)

Method: every route (31) was crawled with Playwright at desktop (1440×900) in dark and light themes and at
mobile (390×844). For each route the crawl recorded console errors, failed API calls, horizontal overflow, missing
`<h1>`, tap targets under 24 px and time to settle, and took a screenshot. The screenshots were then reviewed by
hand from the point of view of an analyst or a manager using the portal day to day.

Automated result: no 4xx/5xx API errors and no horizontal overflow on any route.

Status: **fixed** = fixed in this PR; **v2** = addressed by the redesign in `frontend-v2/` (a structural change the
current frontend keeps as it is).

## Operational (numbers that disagree or mislead)

| # | Flaw | Where | Status |
|---|------|-------|--------|
| O1 | "Where to act" ranked markets and showed the top segment by the older opportunity index (54/49/23), while the Opportunity Board, analyst and memos use the explainable opportunity score. The two orders disagree. | Command Center | fixed: ranked by the opportunity score, showing the engine's best opportunity |
| O2 | Market page segments were "Ranked by opportunity index", in a different order from the board. | `/markets/[m]` | fixed: ranked by the opportunity score; the index is shown beneath for reference |
| O3 | Product page showed only the product and segment *index*, with no link to the segment's board score. | `/products/[id]` | fixed: segment opportunity score added (index in the tooltip) |
| O4 | Freshness said "2026-09-27 (0 days ago)" and "date unknown" at the same time for undated uploads. | `/data` | fixed: "uploaded 2026-09-27 · snapshot date unknown" |
| O5 | Evidence grade D was shown as a red error badge on every market and every board row. Grade D means little evidence, not a fault. | site-wide | fixed: neutral tone; on the board a grade shared by all rows is stated once |

## Visual

| # | Flaw | Where | Status |
|---|------|-------|--------|
| V1 | Raw market slugs (`denture_base`, `dental_models`) in titles, selectors, breadcrumbs, tables and cards. | site-wide (26 files) | fixed: configured category names (`leaf_category_en` / `_zh` in config/categories.yaml), in the viewer's language; the slug stays the identifier in URLs |
| V2 | Taxonomy nodes shown as keys (`repair_kit`, `full_denture_kit`); long segment names cut off with no way to read them. | Intelligence Map | fixed: humanised labels and full names on hover |
| V3 | Product image blocked or missing showed a large empty white box. | `/products/[id]`, Shop, Portfolio | fixed: placeholder with an icon; broken thumbnails hidden |
| V4 | Competitor table headers ran together ("P(#1)PRICE INDEX", "ENTRANTSSTRENGTHS / WEAKNESSES"). | `/competitors` | fixed: column padding, no header wrapping |
| V5 | Opportunity Board: 11 dense columns; an "Index (M3)" column duplicating the score; "75% of the evidence measured", "1/3 snapshots" and grade "D" repeated on every row; risks shown as unlabeled coloured dots. | `/opportunities` | fixed: index folded under the score (and in the row detail); facts shared by all rows said once above the table; growth column hidden until any row has growth; risks as labeled chips (two, then "+n"); a sort selector keeps every sort reachable |
| V6 | The 3D globe took more than half the first screen. | Command Center | fixed: shorter (320 px mobile, 440 px desktop) |
| V7 | Truncated placeholders ("market: auto (most", "unit cost $ (("). | Product Pipeline | fixed: wider fields with labels |
| V8 | Empty states with large dead areas (Analyst, Launch, Shop, Pipeline before any input). | several | v2 |

## Navigation

| # | Flaw | Where | Status |
|---|------|-------|--------|
| N1 | Sidebar of 4 groups and ~25 links; the Govern group fell below the fold and under the footer on laptop screens. | all pages | fixed: groups collapse (Govern collapsed by default, the group of the current page always open, choice remembered in this browser) |
| N2 | No command palette, no breadcrumbs on most pages, no "recent" or "pinned" places. | all pages | v2 |
| N3 | A page-level market selector duplicated the sidebar selector on Intelligence Map and Review. | `/intelligence`, `/review` | v2 (one global market context) |
| N4 | Pages are organised by data type rather than by task (e.g. launch evaluation is spread over Board, Launch, Compare, Projects). | IA | v2 (task-oriented workspaces) |

## Executional

| # | Flaw | Where | Status |
|---|------|-------|--------|
| E1 | The floating "This looks wrong" button covered table cells and the last lines of every page. | all pages | fixed: icon button that shows its label on hover or focus; page bottom padding so nothing sits beneath it |
| E2 | The getting-started checklist took the top of the Command Center (most of the first mobile screen) for returning users. | Command Center | fixed: collapses to a progress line once the first step is done; expands on click |
| E3 | Product page had no `<h1>`. | `/products/[id]` | fixed: the product title is the page heading |
| E4 | Opportunity Board takes ~6 s to settle: launch simulations for every concept run on each request. | `/opportunities` | v2 (progressive loading); backend caching is a separate change |
| E5 | 242 tap targets under 24 px on mobile (mostly inline badges and text links). | mobile | partly fixed (nav, feedback, checklist); v2 design system uses 32 px minimum |
| E6 | `THREE.Clock` deprecation warnings from the 3D library on 3D pages. | Command Center, Galaxy, Universe | library warning, no user impact |
