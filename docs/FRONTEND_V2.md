# Web app v2 (`frontend-v2/`)

A new interface built next to the current one (`frontend/`). The two share the platform API and nothing else:
v2 can change, break or be dropped without touching v1, and CI checks both (`frontend-v2` and `e2e-v2` jobs).

| | v1 (`frontend/`) | v2 (`frontend-v2/`) |
|---|---|---|
| Port | 3000 | 3001 |
| Navigation | one sidebar, 4 groups, ~25 links | 6 workspaces (rail) + section panel, hub pages, breadcrumbs |
| Finding things | Search page | ⌘K command palette (pages, markets, actions, live entity search), `g`+key jumps, pins, recents |
| Market context | sidebar select, some pages have their own | one market pill in the top bar; pages follow it |
| Home | globe, KPIs, where-to-act | Mission Control: KPIs, top opportunities, needs attention, opportunity landscape, markets, activity |
| Notifications | Alerts page | bell with unread alerts and the latest events, on every page |

## Where each v1 page lives

| Workspace | Pages (same URLs as v1) |
|---|---|
| Home `/` | Mission Control `/` (new), Command Center `/command` (v1's home: globe and leading brands) |
| Discover `/discover` | Search, Market Analytics `/markets`, Intelligence Map, Knowledge Graph, Market Universe, Product Galaxy |
| Decide `/decide` | Opportunity Board, Compare, Launch Simulator, Competitors & Trends, Market Analyst, Shopping Mode |
| Execute `/execute` | Product Pipeline, Suppliers, Alerts & Events, Inbox, My Portfolio |
| Data `/quality` | Import data, Data Operations, Review queues, Accuracy, Accuracy labelling, Feedback & usage |
| Admin `/admin` | Organization, Audit Log, Methodology |

Entity pages (`/markets/[market]`, `/markets/[market]/hierarchy`, `/products/[id]`, `/suppliers/[id]`,
`/galaxy/[market]`) are unchanged in URL and content and appear under their workspace in the breadcrumbs.

## New in v2

* `components/v2/shell.tsx`: rail, section panel (collapsible, pins), top bar, breadcrumbs, mobile bottom bar and drawer, keyboard shortcuts.
* `components/v2/command-palette.tsx`: fuzzy page and market matching, actions, `/search` results.
* `components/v2/widgets.tsx`: opportunity list and landscape (opportunity engine rows across markets), attention queue,
  market cards, activity feed, pipeline funnel. Every number comes from an existing API response; nothing is re-computed
  in the browser except sorting and counting.
* `components/v2/workspace-hub.tsx` and `lib/ia.ts`: the information architecture, one definition for rail, panel,
  breadcrumbs, palette and hubs.
* `/connectors` (Data): Amazon data providers and Chinese marketplaces with what each needs configured, the active
  provider per capability, "Run now" for the selected market and the acquisition run log (requests, cache hits, cost).
  `components/v2/live-data.tsx` adds "Refresh from Amazon" to the market page and live review text to the product page.
* `/sourcing` (Execute): a product concept from a sub-category or an idea, a marketplace search, the recommended offer,
  cost-vs-reliability Pareto chart, supplier shortlist and a bilingual RFQ that records an inquiry. Board rows link here.
* `/economics` (Decide): per-unit profit and launch cash computed on the server (`POST /economics/unit`); every
  assumption is listed with its source.

## Running both

```bash
python scripts/dmis.py serve                      # API :8000
cd frontend && npm run dev                        # v1 :3000
cd frontend-v2 && npm install && npm run dev      # v2 :3001
```
