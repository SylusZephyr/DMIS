# Business Workflow

How a company uses DMIS in place of a market-research cycle, from a new export to a product
decision. It is organised around the six business questions and the people who ask them.

## The operating loop

```text
Monthly (or when an export arrives)            Daily / on demand
────────────────────────────────────           ─────────────────────────────────────
1. Upload the new export per market            5. Product managers open My Portfolio:
   (or a connector delivers it)                    "What to focus on" + alerts
2. Platform processes it (18 stages),          6. Evaluate candidate launches in the
   skips unchanged files                          Launch Simulator
3. Change detection → alerts to the            7. Ask the Market Analyst ad-hoc questions
   category owners                             8. Shortlist suppliers; correct relevance
4. Data owner checks the ingestion report          mistakes in the review queue
   and the uncertain-listing queue
```

Every new snapshot makes the next answers stronger: trends get a growth number, confidence
gains history, and competitor changes appear.

## The six business questions

| # | Question | Where | How the answer is built |
|---|---|---|---|
| 1 | **What products should we develop?** | Analyst ("What should we develop?"), Portfolio → *What to focus on*, Market page → Segment opportunity, `GET /markets/{m}/brief` | Segments are ranked by opportunity. The suggested development is the best segment's leading model (specification or title model). Reasons cover trend, competition, demand and customer pain. |
| 2 | **Which markets are growing?** | Competitors & Trends, Analyst, `GET /trends` | Trend per market and segment from snapshots, launch dates, new-entrant share and review velocity, each with a confidence percentage and the evidence. |
| 3 | **Who are our competitors?** | Competitors & Trends, Analyst ("Tell me about <brand>"), `GET /markets/{m}/competitors` | Each brand's position (Leader, Challenger, Follower or Niche), share, price index, rating, breadth and launches. Its weaknesses map to opportunities, and changes are shown since the last upload. |
| 4 | **Who can manufacture this product?** | Suppliers, Launch Simulator → *Who can make it*, Analyst | Imported suppliers are scored on certifications, OEM/ODM and category fit, then matched to segments. Without a supplier list the answer says so; nothing is invented. |
| 5 | **What happens if we launch this product?** | Launch Simulator, Analyst ("What happens if we launch X at $P?"), `POST /launch/evaluate` | The idea is placed among its comparables and gets a market-fit score, risks, attractiveness and verdict. Positioning comes from the price percentile. The strategy follows from the evidence. Unit margin uses the comparables' cost and FBA fields when present, plus a Monte-Carlo profit distribution. |
| 6 | **What should each employee focus on?** | My Portfolio, Alerts, Analyst ("What should <name> focus on?"), `GET /employees/{id}/focus` | Actions are ranked high, medium or low, each with its reason. The possible actions are:<br>• evaluate a launch in each high-opportunity segment<br>• review important alerts<br>• find manufacturers<br>• upload the next snapshot<br>• improve coverage<br>• review uncertain listings<br>• counter the leader's weakness<br>• map categories that have no market yet |

## Roles in the workflow

| Role | Typical user | Does |
|---|---|---|
| admin | platform owner | Creates users and tokens and configures connectors; sees everything |
| manager | head of product / sourcing | Uploads data, manages suppliers and ownership, sees all markets |
| product_manager | category owner | Sees only their categories' markets, uploads their exports, receives their alerts, runs launch evaluations |
| analyst | market analyst | Reads all markets, uploads data, asks the analyst, corrects relevance |
| viewer | stakeholder | Read-only dashboards |
| customer | clinic / lab buyer | Shopping Mode only |

## Decision example (from the implants export)

`docs/BUSINESS_SIMULATION_REPORT.md` walks one real export through all ten steps. In short:

1. The Water flosser segment is large and growing on launch activity, but Waterpik holds nearly all of it and is priced 38% above the segment median.
2. The competitor view therefore suggests an affordable alternative.
3. The Launch Simulator shows the catch: at $39–45 the comparables' own cost and FBA fields give a negative unit margin. That rules the idea out before any sourcing effort, and the reason is shown next to the numbers.

The same flow applies to any category. Upload a micromotor export and it runs unchanged:
`python scripts/business_simulation.py --file <micromotor export> --market micromotor --context-raw`.

## Governance

- **Traceability:** every excluded record has a reason (`/markets/{m}/records?status=excluded`), and every AI call is logged in `ai_traces` (model, prompt version, input, output, status, time).
- **Human in the loop:** uncertain relevance goes to the review queue, and a human decision overrides the model on the next run.
- **Evidence:** confidence scores and "unavailable" markers show where evidence is thin. Decisions on low-confidence segments should wait for the next snapshot, or for supplier and review data.
- **Configuration:** all thresholds are in `config/` and versioned with the code. Changing one changes the input fingerprint, so the next upload recomputes.
