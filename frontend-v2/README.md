# DMIS web app v2 — task-oriented interface

A second, independent web app over the same platform API (`/api/v2`). The first interface lives in
`../frontend/` and is unaffected by anything here; both can run side by side.

```bash
npm install
npm run dev        # http://localhost:3001 (expects the API on :8000 -> python scripts/dmis.py serve)
npm run build && npm run start    # also port 3001
```

`/api/v2/*` is proxied to the platform API by `next.config.ts` (`DIP_API_URL`, default `http://127.0.0.1:8000`).

## What is different from v1

* **Workspaces instead of one long menu.** Six workspaces (Home, Discover, Decide, Execute, Data, Admin) in a
  rail on the left; the section panel shows the current workspace's pages. Each workspace has a hub page
  (`/discover`, `/decide`, `/execute`, `/quality`, `/admin`) that explains its tools with a live figure
  and a workspace-specific widget. One definition drives all of this: `lib/ia.ts`.
* **Command palette** (`⌘K` / `Ctrl+K` or `/`): pages, markets, actions and live search over products,
  sub-categories, taxonomy nodes, brands and suppliers. `g` then a letter jumps (`g h` home, `g o` board,
  `g m` markets, `g s` search, `g c` decide, `g e` execute, `g q` data, `g a` admin).
* **Top bar**: breadcrumbs, the market context (one global market, with its size and best score), a
  notification bell with the latest events and unread alerts, language and theme.
* **Mission Control** (`/`): key figures, top opportunities across all markets (explainable opportunity
  score), a "needs attention" queue (duplicates, taxonomy approvals, anomalies, data gaps, approvals,
  unread alerts), the opportunity landscape (demand vs score, click to open), market cards and activity.
  The v1 command center (globe, leading brands) is at `/command`.
* **Pins and recents**: pin any page to the section panel; recent pages appear first in the palette
  (both kept in this browser only).
* **Design system**: glass surfaces, aurora backdrop, motion (count-up figures, staggered reveal, reduced
  motion respected). Text tokens keep their AA-validated values in both themes.

Every v1 route exists here with the same URL and features (see `docs/FRONTEND_V2.md` at the repository root).

## Checks

```bash
npm run lint && npx tsc --noEmit && npm run check:i18n && npm run test
E2E_BASE_URL=http://127.0.0.1:3001 npm run test:e2e     # with the API and web app running (e2e/README.md)
```
