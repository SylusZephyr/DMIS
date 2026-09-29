# DMIS frontend — 3D Intelligence Command Center

Next.js 16 (App Router, Turbopack) · React 19 · React Three Fiber / three.js ·
Sigma.js + graphology · Apache ECharts · D3 scales · Tailwind CSS 4 · shadcn-style components.

```bash
npm install
npm run dev        # http://localhost:3000 (expects the API on :8000 -> python scripts/dmis.py serve)
npm run build && npm run start
```

`/api/v2/*` is proxied to the platform API by `next.config.ts` (`DIP_API_URL`, default
`http://127.0.0.1:8000`), so the browser never needs CORS.

| Route | Screen |
|---|---|
| `/` | Global Command Center — 3D globe, KPIs, markets, brands |
| `/universe` | Market Universe — industry → branches → categories → segments in 3D |
| `/galaxy/[market]` | Product Galaxy — products as instanced stars, similarity links |
| `/markets/[market]` | Market Analytics — matrix, segment opportunity, price tiers, forecast, pain |
| `/products/[id]` | Product Detail — listings, competition, suppliers, complaints, opportunity, similar |
| `/graph` | Knowledge Graph Explorer (Sigma.js) |
| `/data` | Data Operations — upload, live job stages, ingestion report, relevance corrections |
| `/suppliers` | Supplier Intelligence |
| `/portfolio` | Product-manager / ownership dashboard |
| `/shop` | Guided Shopping Mode |

Design: magnitudes use one sequential hue (dim navy → bright cyan = more opportunity);
entity colours are fixed per node kind (`lib/colors.ts`).
