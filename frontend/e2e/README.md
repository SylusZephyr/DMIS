# Frontend tests

| Command | What | Needs |
|---|---|---|
| `npm test` | Vitest unit/component tests (`tests/unit/`: format, i18n `t()`, chart theming, typed paths, request cache) | nothing |
| `npm run test:e2e` | Playwright journeys + axe WCAG 2.1 A/AA checks in both themes (`e2e/`) | a running API **and** web app with seeded data |
| `npm run check:api` | fails if `lib/api-schema.d.ts` is stale vs the FastAPI schema | Python env of the repo |
| `npm run check:bundle` | fails if a non-3D route's first-load JS exceeds 300 KB gzip | a finished `npm run build` |

## Running the e2e suite (locally or in CI)

Run from the repository root; ports are examples.

```bash
# 1. seed a throwaway data lake (sample SellerSprite exports -> 3 markets)
export DIP_DATA_DIR=/tmp/e2e/platform DIP_LAKE_DIR=/tmp/e2e/lake
python scripts/dmis.py bootstrap

# 2. API (auth off so tests need no token)
DIP_AUTH=off python -m uvicorn dip.api.app:app --port 8000 &

# 3. web app. DIP_API_URL is baked into the /api/v2 rewrite at BUILD time.
cd frontend
npm ci
DIP_API_URL=http://127.0.0.1:8000 npm run build
DIP_API_URL=http://127.0.0.1:8000 npx next start -p 3000 &
npx wait-on http://127.0.0.1:3000/api/v2/markets   # or a curl retry loop

# 4. tests
npx playwright install --with-deps chromium        # skip if a Chromium is preinstalled (then set PW_CHROMIUM_PATH)
E2E_BASE_URL=http://127.0.0.1:3000 npm run test:e2e
```

Environment: `E2E_BASE_URL` (default `http://127.0.0.1:3103`), `PW_CHROMIUM_PATH` (use an existing Chromium binary).
With `CI=true` the suite retries once, forbids `test.only` and writes an HTML report to `playwright-report/`
(upload it, plus `test-results/` traces, as artifacts on failure).

The tests read labels from `messages/en.ts` / `messages/zh.ts` and pick the first market from `/api/v2/markets`,
so they do not depend on particular market names.
