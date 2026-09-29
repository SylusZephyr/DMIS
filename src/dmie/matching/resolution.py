"""Listing-to-product entity resolution: Stages 1, 6, 7, plus orchestration
and clustering.

100 Amazon listings must NOT automatically become 100 products (PRINCIPLES.md
principle 2). For every candidate pair produced by blocking
(candidates.py, Stage 5), this module decides MATCH / NO_MATCH / UNCERTAIN
— then clusters confirmed matches into product_id groups.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from dmie.ai import call_ai
from dmie.market.sales import best_selling_listing
from dmie.matching.similarity import attribute_score, brand_score, quantity_signals, title_similarity

MATCHING_VERSION = "matching_v1"
MATCH_THRESHOLD = 0.90
NO_MATCH_THRESHOLD = 0.40
WEIGHTS = {"title": 0.5, "brand": 0.3, "attribute": 0.2}
AI_MODEL = "claude-sonnet-4-5"
DEFAULT_CATEGORY = "denture_base"

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PROMPT_PATH = _PROJECT_ROOT / "prompts" / "entity_resolution.md"
_CATEGORIES_PATH = _PROJECT_ROOT / "config" / "categories.yaml"


@lru_cache(maxsize=None)
def _variant_policy(category: str) -> dict:
    """Per-category variant policy (docs/entity_resolution.md "Product
    Identity Rules") — never hardcoded once for all categories. Cached:
    resolve_pair calls this once per candidate pair, and re-reading +
    re-parsing categories.yaml from disk on every single call (fine at
    pilot scale) took 470K redundant file reads at real production scale
    (22,801 real denture_base listings) -- the dominant cost behind a
    30+ minute resolution run. config/categories.yaml doesn't change
    within a process's lifetime, so caching per category is safe."""
    categories = yaml.safe_load(_CATEGORIES_PATH.read_text(encoding="utf-8"))["categories"]
    return categories.get(category, {}).get("variant_policy", {})


@dataclass
class CandidateResult:
    listing_id_a: str
    listing_id_b: str
    blocking_method: str
    match_method: str
    match_confidence: float
    decision: str        # MATCH | NO_MATCH | UNCERTAIN
    review_status: str   # auto_accepted | needs_review


def exact_identifier_match(a: dict, b: dict) -> bool | None:
    """Stage 1. Returns True/False if a shared identifier field decides
    this outright, or None if no identifier data is available. This
    dataset's SellerSprite export has no model/UPC/GTIN field (see
    docs/data_dictionary.md) — always returns None today, but the check
    is real and activates the moment such a field exists."""
    for field in ("model_number", "upc", "gtin"):
        va, vb = a.get(field), b.get(field)
        if va and vb:
            return va == vb
    return None


def score_pair(a: dict, b: dict) -> tuple[float, dict]:
    """Stages 2-4 blended into Stage 6's composite score."""
    b_score = brand_score(a.get("brand"), b.get("brand"))
    t_score = title_similarity(a.get("title"), b.get("title"))
    attr_score = attribute_score(a.get("price"), b.get("price"))
    composite = (
        WEIGHTS["title"] * t_score
        + WEIGHTS["brand"] * b_score
        + WEIGHTS["attribute"] * attr_score
    )
    return composite, {"brand_score": b_score, "title_score": t_score, "attribute_score": attr_score}


def ai_arbitrate(a: dict, b: dict) -> dict | None:
    """Stage 7. Same graceful-degradation pattern as the relevance
    classifier (src/dmie/classification/classifier.py): returns None,
    never a guessed verdict, when no AI provider is configured, or when
    the call/parse ultimately failed after retries (M13) -- a single
    failed AI request degrades this one pair's arbitration, it never
    raises out of this function to crash a resolution run."""
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        template
        .replace("{LISTING_A}", json.dumps(a, ensure_ascii=False, indent=2))
        .replace("{LISTING_B}", json.dumps(b, ensure_ascii=False, indent=2))
    )
    outcome = call_ai(prompt, model=AI_MODEL, max_tokens=300)
    return outcome.data if outcome.ok else None


def resolve_pair(a: dict, b: dict, blocking_method: str, category: str = DEFAULT_CATEGORY) -> CandidateResult:
    identifier_match = exact_identifier_match(a, b)
    if identifier_match is not None:
        decision = "MATCH" if identifier_match else "NO_MATCH"
        return CandidateResult(a["listing_id"], b["listing_id"], blocking_method,
                                "exact_identifier", 1.0, decision, "auto_accepted")

    composite, _ = score_pair(a, b)
    q_a, q_b = quantity_signals(a.get("title"), b.get("title"))
    policy = _variant_policy(category)

    # Symmetric difference, not full disjointness (see
    # docs/entity_resolution_evaluation_report.md false-merge #1 and
    # DECISIONS.md's writeup): two titles that share one quantity number
    # (e.g. both say "12pcs" for an unrelated per-kit component count)
    # used to defeat this guard entirely even when they also state a real,
    # different pack size ("1 Piece" vs "2 Pack") -- isdisjoint() requires
    # *zero* shared tokens, so one coincidental match let a genuine
    # mismatch through. Firing whenever either side has a quantity token
    # the other doesn't catches that case; it also means a pair that
    # agrees on the outer pack count but differs on an unrelated
    # sub-count mentioned on only one side (e.g. "2 Pack (8 Count)" vs
    # plain "Pack of 2") now gets queued instead of auto-matched -- a
    # real but deliberately accepted tradeoff (queuing a correct match for
    # human confirmation is far cheaper than silently shipping a wrong
    # one; see DECISIONS.md).
    if q_a and q_b and (q_a ^ q_b) and policy.get("pack_quantity") != "same_product":
        # Same product family, different pack/bundle size. Whether that
        # makes them separate products is this category's configured
        # policy, not a universal rule (docs/entity_resolution.md "Product
        # Identity Rules"). Default policy queues rather than rejects --
        # it can still be a genuine duplicate listing under different
        # quantity phrasing.
        return CandidateResult(a["listing_id"], b["listing_id"], blocking_method,
                                "possible_pack_variant", composite, "UNCERTAIN", "needs_review")

    if composite >= MATCH_THRESHOLD:
        return CandidateResult(a["listing_id"], b["listing_id"], blocking_method,
                                "fuzzy_composite", composite, "MATCH", "auto_accepted")
    if composite <= NO_MATCH_THRESHOLD:
        return CandidateResult(a["listing_id"], b["listing_id"], blocking_method,
                                "fuzzy_composite", composite, "NO_MATCH", "auto_accepted")

    ai_result = ai_arbitrate(a, b)
    if ai_result is None:
        return CandidateResult(a["listing_id"], b["listing_id"], blocking_method,
                                "fuzzy_composite", composite, "UNCERTAIN", "needs_review")

    decision = ai_result.get("decision", "UNCERTAIN")
    confidence = float(ai_result.get("confidence", composite))
    return CandidateResult(a["listing_id"], b["listing_id"], blocking_method,
                            "ai_arbitration", confidence, decision,
                            "auto_accepted" if decision != "UNCERTAIN" else "needs_review")


def cluster_products(listing_ids: list[str], match_results: list[CandidateResult]) -> dict[str, str]:
    """Union-find over MATCH-decided pairs. Every listing gets a
    product_id: clustered listings share one, and an unmatched listing
    gets a stable singleton product_id derived from its own listing_id —
    every listing belongs to exactly one product, even if that product
    currently has only one listing."""
    parent = {lid: lid for lid in listing_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)  # deterministic root choice

    for r in match_results:
        if r.decision == "MATCH":
            union(r.listing_id_a, r.listing_id_b)

    roots = {lid: find(lid) for lid in listing_ids}
    product_ids: dict[str, str] = {}
    for root in roots.values():
        if root not in product_ids:
            product_ids[root] = "P" + hashlib.sha1(root.encode()).hexdigest()[:10]
    return {lid: product_ids[roots[lid]] for lid in listing_ids}


def build_product_listings(listing_ids: list[str], match_results: list[CandidateResult]) -> list[dict]:
    """Required output rows: product_id, listing_id, match_method,
    match_confidence, review_status — one row per listing, every listing
    covered (see cluster_products)."""
    product_id_map = cluster_products(listing_ids, match_results)

    best_match: dict[str, CandidateResult] = {}
    for r in match_results:
        if r.decision != "MATCH":
            continue
        for lid in (r.listing_id_a, r.listing_id_b):
            if lid not in best_match or r.match_confidence > best_match[lid].match_confidence:
                best_match[lid] = r

    rows = []
    for lid in listing_ids:
        match = best_match.get(lid)
        rows.append({
            "product_id": product_id_map[lid],
            "listing_id": lid,
            "match_method": match.match_method if match else "singleton",
            "match_confidence": match.match_confidence if match else 1.0,
            "is_best_listing": None,
            "review_status": "auto_accepted",
        })
    return rows


def build_products(listings: list[dict], product_rows: list[dict], match_results: list[CandidateResult]) -> list[dict]:
    """The Product Master: one row per resolved product_id (ALL of them,
    not just RELEVANT-scoped ones), so the dashboard can consume a real
    product identity record instead of reconstructing one ad hoc from a
    best listing every time it renders a product (M12).

    `listings`: dicts with listing_id, category_id, title, brand,
    image_url, monthly_sales, product_type (from
    listing_product_type_classification, may be None/UNCERTAIN-filtered-
    to-None by the caller), and product_type_confidence (that
    classification's own confidence, may be None). `product_rows`:
    build_product_listings()'s output, used only for the product_id
    grouping. `match_results`: the same CandidateResult list passed to
    cluster_products/build_product_listings.

    product_type is a majority vote across a product's listings (M11
    Stage 3) -- deliberately separate from product IDENTITY logic above
    (name/brand/model/representative_image/confidence), none of which
    this changes. product_type_confidence is the mean classification
    confidence of the listings that agree with the winning type (None if
    no listing has a classified type at all -- never a fabricated 0).
    product_type_conflict is True whenever a product's listings disagree
    on type (more than one distinct classified type present) -- surfaced
    rather than silently resolved, so a human can look at genuinely
    disputed products instead of trusting an arbitrary tie-break.
    """
    listings_by_id = {listing["listing_id"]: listing for listing in listings}
    group_by_product: dict[str, list[str]] = {}
    for row in product_rows:
        group_by_product.setdefault(row["product_id"], []).append(row["listing_id"])

    # Weakest-link identity confidence: the lowest match_confidence among
    # the MATCH edges connecting a cluster's listings. A singleton was
    # never matched to anything, so there's no merge decision to score --
    # None (not a fabricated 1.0), per PRINCIPLES.md's "None never 0/never a
    # guess" principle.
    min_match_confidence: dict[str, float] = {}
    for r in match_results:
        if r.decision != "MATCH":
            continue
        for lid in (r.listing_id_a, r.listing_id_b):
            if lid not in min_match_confidence or r.match_confidence < min_match_confidence[lid]:
                min_match_confidence[lid] = r.match_confidence

    rows = []
    for product_id, listing_ids in sorted(group_by_product.items()):
        group = [listings_by_id[lid] for lid in sorted(listing_ids)]

        named_listing = best_selling_listing(group) or min(group, key=lambda listing: listing["listing_id"])

        brands = [listing["brand"] for listing in group if listing.get("brand")]
        brand = None
        if brands:
            brand_counts = Counter(brands)
            top_count = max(brand_counts.values())
            # Tie on count: alphabetically-first, so the choice is
            # deterministic rather than dict/Counter iteration-order-dependent
            # (same bug class as title_fingerprint/product_type_discovery).
            brand = sorted(b for b, n in brand_counts.items() if n == top_count)[0]

        type_votes = [
            (listing["product_type"], listing.get("product_type_confidence"))
            for listing in group if listing.get("product_type")
        ]
        if type_votes:
            vote_counts = Counter(t for t, _ in type_votes)
            product_type = vote_counts.most_common(1)[0][0]
            product_type_conflict = len(vote_counts) > 1
            winning_confidences = [c for t, c in type_votes if t == product_type and c is not None]
            product_type_confidence = (
                sum(winning_confidences) / len(winning_confidences) if winning_confidences else None
            )
        else:
            product_type = None
            product_type_conflict = False
            product_type_confidence = None

        confidences = [min_match_confidence[lid] for lid in listing_ids if lid in min_match_confidence]
        confidence = min(confidences) if confidences else None

        rows.append({
            "product_id": product_id,
            "category_id": named_listing.get("category_id"),
            "product_name": named_listing.get("title"),
            "product_type": product_type,
            "product_type_confidence": product_type_confidence,
            "product_type_conflict": product_type_conflict,
            # Product Knowledge Graph (Tier 2, M4) -- schema-only, always
            # None: no real family-level diversity exists in this pilot's
            # data yet to derive it from. See schema.sql's comment on
            # products.product_family and docs/product_knowledge_graph.md.
            "product_family": None,
            "brand": brand,
            "model": None,  # no model-number extraction exists yet
            "representative_image": named_listing.get("image_url"),
            "confidence": confidence,
        })
    return rows


def matching_breakdown(candidates: list[dict]) -> dict:
    """Product Matching evaluation report: what fraction of candidate
    pairs were resolved automatically (rules/composite score alone,
    Stages 1-6), by AI arbitration (Stage 7), or are still pending human
    review. `candidates`: dicts with at least match_method, review_status.
    Percentages are computed only from real data — never claimed until
    measured (see docs/human_review_workflow.md)."""
    total = len(candidates)
    if total == 0:
        return {"total": 0, "automatic": 0, "ai_assisted": 0, "human_reviewed": 0,
                "automatic_pct": None, "ai_assisted_pct": None, "human_reviewed_pct": None}

    automatic = sum(1 for c in candidates if c["match_method"] != "ai_arbitration" and c["review_status"] != "needs_review")
    ai_assisted = sum(1 for c in candidates if c["match_method"] == "ai_arbitration" and c["review_status"] != "needs_review")
    human_reviewed = sum(1 for c in candidates if c["review_status"] == "needs_review")

    return {
        "total": total,
        "automatic": automatic,
        "ai_assisted": ai_assisted,
        "human_reviewed": human_reviewed,
        "automatic_pct": automatic / total,
        "ai_assisted_pct": ai_assisted / total,
        "human_reviewed_pct": human_reviewed / total,
    }
