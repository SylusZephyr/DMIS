# Spec coverage — AI-Powered Global Dental Product Intelligence & Supply Chain Discovery Platform

Status of every section of the master specification (v1.0) against this repository, as of the knowledge-layer
branch. **Built** = implemented and tested. **Partial** = the mechanism exists, a named piece is missing.
**Needs data** = built or ready, but blocked on inputs outside the code. **Not built** = not started.

The platform was extended, not rewritten: the spec's own rules (§86 "do not introduce a graph database merely
because...", §171 build order) and PRINCIPLES.md principle 12 favour evolving the working system.

| § | Topic | Status | Where / what is missing |
|---|---|---|---|
| 2, 148 | Listing ≠ product ≠ family; hierarchy | Built | Family → Segment → Model → Variant → Listing (`pipeline/clustering`), configuration split (`knowledge/identity.py`) |
| 3 A | What exists | Built | products, families, taxonomy, brands, sellers; manufacturers only via the supplier DB |
| 3 B | What is selling | Built / needs data | sales, revenue, price, rating, listing age, concentration; review volume and seller counts are not in the current exports |
| 3 C | What customers want | Built / needs data | review text uploaded with a dataset drives the customer-pain dimension and top complaints; without it the rating-based proxy is used and labelled |
| 3 D | Opportunities | Built | `knowledge/opportunity.py` (§35–36, 71, 155–156) |
| 3 E | Build / source | Partial | supplier DB, import, scoring, segment matching; global web discovery not built (§50–51) |
| 4–6 | Architecture, stack, storage layers | Built | FastAPI + Pydantic + SQLAlchemy, queue worker + Redis, PostgreSQL optional, immutable raw zone + Parquet lake (DuckDB) |
| 7–8, 79 | Core entities | Built / partial | business DB + lake tables; supplier capability and product–supplier relationship tables are partial (§84–85) |
| 9–10, 103–104 | Dynamic taxonomy, machine vs approved | Built | `knowledge/taxonomy_discovery.py`, `/taxonomy`, `/taxonomy/decision`; LLM naming of nodes not built (labels come from values) |
| 11, 13.1 | Dental relevance, Dental Confidence | Built | `knowledge/applications.py`; image component awaits a vision model |
| 12 | Multimodal understanding | Partial | text built; image only via readable references; reviews need data |
| 13.2 | Data confidence | Built | `intelligence/confidence.py`; freshness / cross-source parts limited by single-snapshot data |
| 14, 80–81 | Provenance, observations, evidence | Built | `knowledge/provenance.py`, `/why`, "Why?" drawer |
| 19–20, 146–147 | Deduplication, identity confidence | Built | resolver + configuration split + human decisions + identity confidence; image similarity is image-id only |
| 21 | Best-selling listing | Built | `knowledge/capacity.py` (sales, else modeled units; never reviews) |
| 22 | Price intelligence | Built / partial | percentiles and tiers per scope; price by country not applicable (US only) |
| 23–26 | Market capacity, kinds, no double counting | Built / needs data | offline-adjusted value stays empty until offline evidence exists |
| 27–30 | Review intelligence, pain ontology, pain score | Built / needs data | review upload on `/datasets`; aspect ontology, complaint share and sentiment per segment and product feed the opportunity engine; no real review text supplied yet |
| 31–34, 150–151 | Offline intelligence, evidence matrix | Built (seeded) | validated offline evidence loader (exhibitions, catalogs, manufacturers, trade data, surveys) on `/import`; offline evidence score fills the offline-strength dimension; seeded with researched public sources per market (`data/reference/offline_evidence/`); automated collection not built |
| 35–36, 72–73 | Opportunity score, exposed factors | Built | evidence-limited (unmeasured dimensions excluded, coverage shown); one engine drives the Opportunity Board, Intelligence Map and analyst |
| 45–47, 72 | Landed cost, margin headroom | Built / assumptions | `knowledge/landed_cost.py`: observed FBA fee and package weight, configured referral, freight and target margin → fee headroom and max-FOB sourcing ceiling; duty unset until an HTS rate is verified |
| 156 | Risk gates | Built | regulatory and hazmat risks (hazmat terms whole-word, negation-aware) can be configured to `block` a score (status `gated`) |
| 37–39 | Competition, concentration, brands | Built | competitors v3 + HHI, top-3/5/10, long tail |
| 40–42, 112–115 | Market map, leaf card, heatmap | Built | `/intelligence`: taxonomy map + leaf card, market-map bubble chart (median price × modeled revenue, size = products, colour = opportunity), opportunity heatmap |
| 43–45, 160–163 | Shopping mode, comparison | Built | `/shop` recommender (need words, Pareto, explanations), `/compare`; requirement extraction from the need (attributes with ≥ / ≤ / ≈, budget, stars) filters products that contradict a requirement and keeps those that do not state it |
| 46–49 | PM mode, ownership, history | Built | ownership with history, portfolio, alerts per owner |
| 50–56, 158 | Global supplier discovery | Not built | needs a search API and a legal/terms review of sources (§91) |
| 57–61, 125 | Grounded analyst agent | Built / partial | analyst v3 with tools and a number guard; not yet reading the new knowledge tables |
| 62–63 | Connector framework, normalized record | Built | connector registry, universal record format |
| 64–66 | Import, AI column mapping, data quality | Built | `/imports/preview`, `/import` wizard, mapping override |
| 67 | Unit normalization | Built | `knowledge/units.py` |
| 68 | Currency normalization | Built / needs rates | `knowledge/currency.py`: marketplace → currency; non-USD prices and revenue converted at a configured, dated rate (local values kept); a missing rate stops the run |
| 69–70, 153–154 | History, trends, anomalies | Built / needs data | observation history, trend engine, significance-tested alerts; data anomalies (invalid values, price outliers within a product, revenue ≠ price × units, price and sales jumps between snapshots) in the review queue; jumps need repeated snapshots |
| 74 | Opportunity lifecycle | Built | status from the linked project stage |
| 75–78, 157, 159 | Product development workspace, requirements generator | Built | projects pipeline, launch simulator, decision memo; requirements brief per segment / taxonomy node (`/markets/{m}/requirements`, JSON or Markdown): price band, must-have attributes, differentiators, configuration, pain to fix, sourcing ceiling, compliance, evidence |
| 86–87 | Knowledge graph, vector search | Built | embedded graph (Neo4j optional), Qdrant local (server optional) |
| 88 | Multilingual | Partial | EN/ZH interface, original text preserved; machine translation of text not built |
| 89–90 | Dental terminology, application ontology | Built | `config/platform/knowledge.yaml` |
| 91–93 | Lawful acquisition, SellerSprite as estimate | Built | SellerSprite values are kind *estimated* everywhere |
| 94–96 | Human-in-the-loop, feedback | Built | review queues (duplicates, taxonomy, relevance), scope decisions, labelling; decisions feed evaluation |
| 116–117 | Alerts | Built | event bus + significance + owners |
| 118–119 | Semantic search | Built | `/search`: products by meaning (vector index), sub-categories, taxonomy nodes, brands and suppliers by name, across every visible market; degrades to text results without the vector index |
| 120–122 | Security, RBAC, audit | Built | roles, market scope, audit log, rate limits, headers |
| 126–127 | Async jobs, status | Built | stage-by-stage job log; data freshness per market (latest snapshot, stale, snapshots vs what growth and seasonality need) |
| 128–129, 166 | AI tiering, routing, cost tracking | Built / needs key | deterministic tier first; `knowledge/llm_extract.py` runs on `needs_llm` rows only (Anthropic or Gemini), validates every value against the schema and its evidence text, traces tokens and cost, caches by input hash, stops at a per-run budget; reports `unavailable` without a key. Taxonomy-node naming not built |
| 130–134 | Evaluation, golden dataset | Partial | labelling workflow now covers dental, attributes and taxonomy nodes; Dental Confidence measured on the model-labelled gold benchmark; resolver precision/recall; ranking stability under weight changes; the 1,000-product human golden set still needs labelling |
| 135–136, 168 | Micromotor MVP acceptance | Needs data | every stage runs and is tested on a synthetic micromotor dataset (attributes, N3+H37L1 configurations, brushless → RPM taxonomy); a real micromotor export is needed |

## Next build steps (in order)

1. Run the micromotor vertical on a real export (§135) and tune `attribute_schemas.micromotor`.
2. Supply an `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` to run the LLM tier; add taxonomy-node naming on the same tier (§128–129).
3. Upload real review text with a dataset (§27–30); the requirements brief then lists the real complaints to fix (§77).
4. Offline evidence: the loader and score are built (`knowledge/offline.py`, `/markets/{m}/offline-evidence`); collect real exhibition, distributor and trade-data rows (§31–33).
5. Supplier discovery from permitted sources, with capability evidence (§50–56).
6. Set `currency.rates_to_usd` (with `rates_as_of`) before uploading a non-US marketplace (§68).
