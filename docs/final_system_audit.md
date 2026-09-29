# DMIE Final System Audit (Phase 1)

**This is the third near-identical "start from a system audit" request in
a row this session** (previous two produced `docs/full_system_audit_report.md`
and `docs/final_architecture_review.md`). Nothing in the repository has
changed since those were written and committed — verified again just now:
`git status` clean, `HEAD` at `3d93fc0`, 326/326 tests passing. Re-running
the full inspection a third time would produce the same findings from the
same evidence. This document is a short pointer to what's already there,
not a third independent derivation — see those two files for the full
detail (table-by-table row counts, per-check environment results, the
bug that was found and fixed, every completed/incomplete component with
evidence).

## 1. Current architecture

Exactly as diagrammed in this prompt and the last two:
`raw xlsx → ingest → classify (relevance) → classify_product_types →
resolve_products (entity resolution + Product Master) → calculate_market
→ analyze_reviews → detect_opportunities → dashboard`. Plus two
cross-cutting layers added this session: `src/dmie/ai/client.py` (single
AI-call choke point, M13) and `src/dmie/database/runs.py` (append-only
run log, M14). Full diagram: `docs/final_architecture_review.md` §Read.

## 2. Completed milestones (real, verified against the live DB and git log)

M1-M10, M11 (all 3 stages — discovery, taxonomy freeze, classifier,
Product Master type integration), M12, M13, M14, plus one confirmed bug
found and fixed this session (`category_market_metrics.product_type_distribution`
was reading a permanently-empty column). Full table:
`docs/final_architecture_review.md` §1.

**This prompt's Phase 2 (2.1 product-type classification, 2.2 Product
Master completion, 2.3 entity-resolution quality) is describing work
that is already done** — 2.1 = M11 Stage 2, 2.2 = M12 + M11 Stage 3. 2.3
("product matching evaluation report") is the one piece of Phase 2 that
is genuinely NOT done yet — see §3.

## 3. Missing modules (genuinely open, not yet built)

- **Product-matching evaluation report** — no dedicated report exists
  scoring entity-resolution quality (duplicate-detection accuracy,
  incorrect-merge rate, missed-match rate) at scale. Only 5
  manually-verified pairs exist today (`docs/entity_resolution.md`),
  explicitly documented as "too small to be robust."
- **`scripts/run_pipeline.py`** — no orchestration script exists. This is
  the single highest-value missing piece: the bug fixed this session
  existed specifically because nothing enforced pipeline run order.
- **Second-category validation** — blocked, no micromotor raw
  SellerSprite export exists anywhere in the project.
- **Dashboard re-verification** — pages were last checked in a real
  browser after M12; not since M13/M14/the bug fix. `product_type_confidence`/
  `product_type_conflict` aren't surfaced in the UI at all yet.
- **CI, `.gitattributes`, filled-in `utils/config.py`/`utils/logging.py`/
  `settings.yaml`/`.env.example`/`CHANGELOG.md`** — all still stubs/absent.
- **README gaps** — no dashboard-launch command, no `[dashboard]` install
  extra mentioned.

## 4. Broken assumptions

The one real one, already found and fixed this session: `market/aggregation.py`
and `matching/resolution.py::build_products()` were assumed to share one
source of truth for "listing → product type." They didn't — two
independent read paths into two different tables, one updated when M11
Stage 3 changed the schema, one not. Full writeup:
`DECISIONS.md` "Bug fix — category_market_metrics.product_type_distribution
was permanently stale."

## 5. Data quality risks

- ~~Opportunity-engine signal-type naming~~ **Resolved:** decision made
  to keep the existing names (`PRODUCT_IMPROVEMENT`, `BUNDLE`, etc.) —
  see `DECISIONS.md` "Decision: opportunity signal type names stay as
  they are." No rename.
- `review_insights` has 0 rows — not a pipeline defect, SellerSprite's
  export has no review-text field at all.
- AI-dependent paths (relevance/product-type/entity-resolution AI stages)
  have never been exercised against a real model response — no
  `ANTHROPIC_API_KEY` has ever been configured in this environment.

## 6. UI risks

Dashboard not re-verified since M13/M14 (see §3). SQL-level checks after
the bug fix confirm the underlying data is now correct
(`category_market_metrics.product_type_distribution` returns real values),
but that's not the same as loading the Category Overview chart that reads
it.

## 7. Production risks

No pipeline orchestration (§3) is the top one — manual run-order
dependencies are exactly how the bug got 2 days stale before this audit
caught it. No CI. No venv/dependency lock (functional today, not
reproducible-by-default on a second machine). Full detail:
`docs/full_system_audit_report.md` §"Phase 2 — Environment Check."

---

**No code was modified producing this document.**
