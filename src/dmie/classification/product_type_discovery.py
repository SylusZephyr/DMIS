"""Stage 1 of product-type classification: discover CANDIDATE product
types from data. Never the final taxonomy — a human approves, merges, or
renames these candidates (see docs/product_taxonomy.md) before anything
downstream classifies against them.

Fully deterministic, no LLM: listings are grouped by shared distinctive
title vocabulary using plain word-frequency + union-find, the same
technique already used for candidate blocking (matching/candidates.py).
This generalizes to any category without hardcoding — the same code that
surfaces "wax"/"reline" clusters for denture_base would surface
"brushless"/"brushed" clusters for micromotor, because it never assumes
which words matter (PRINCIPLES.md principle 9).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

_GENERIC_STOPWORDS = {
    # structural/domain-umbrella words, not product-type-defining for any
    # category -- NOT category-specific keywords (those are exactly what
    # this module is supposed to discover, never assume).
    "the", "for", "and", "with", "a", "an", "of", "to", "in", "on", "kit",
    "set", "denture", "dentures", "dental", "no", "use", "general",
    "pcs", "pc", "pack", "box", "sheet", "sheets", "supply", "supplies",
    "lab", "material", "materials", "auxiliary", "modeling", "filling",
    "casting", "molding",
}
_WORD_RE = re.compile(r"[a-z0-9]+")


def tokenize_significant_words(title: str | None) -> set[str]:
    words = _WORD_RE.findall((title or "").lower())
    return {w for w in words if w not in _GENERIC_STOPWORDS and len(w) > 2}


@dataclass
class CandidateProductType:
    candidate_id: str
    suggested_name: str
    connecting_words: list[str]
    listing_ids: list[str] = field(default_factory=list)
    member_titles: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.listing_ids)


def discover_candidate_types(
    listings: list[dict], min_shared_listings: int = 2, max_prevalence: float = 0.9
) -> list[CandidateProductType]:
    """listings: dicts with at least listing_id, title. Groups listings
    into candidates via connected components over words that appear in
    at least `min_shared_listings` different listings (a word appearing
    in only one listing can't define a shared "type") and at most
    `max_prevalence` of the population.

    The upper bound matters as much as the lower one: a word present in
    nearly every listing (e.g. "base" in a denture_base RELEVANT
    population, since that's literally what makes them relevant) doesn't
    distinguish one product *type* from another within that population —
    it defines category membership, already handled by relevance
    classification. Without this bound, one over-common word bridges
    every cluster into a single useless supercluster (see
    tests/unit/test_product_type_discovery.py's regression test).

    Listings sharing no connector word each become their own singleton
    candidate — genuinely unclustered, not silently dropped."""
    word_to_listings: dict[str, set[str]] = defaultdict(set)
    listing_words: dict[str, set[str]] = {}
    listing_titles: dict[str, str] = {}

    for listing in listings:
        lid = listing["listing_id"]
        words = tokenize_significant_words(listing.get("title"))
        listing_words[lid] = words
        listing_titles[lid] = listing.get("title") or ""
        for word in words:
            word_to_listings[word].add(lid)

    total = len(listing_words)
    connector_words = {
        w for w, lids in word_to_listings.items()
        if len(lids) >= min_shared_listings and (len(lids) / total if total else 0) <= max_prevalence
    }

    # union-find over listings, connected by any shared connector word
    parent = {lid: lid for lid in listing_words}

    def find(x: str) -> str:
        while parent[x] != x:
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    for word in connector_words:
        members = sorted(word_to_listings[word])
        for other in members[1:]:
            union(members[0], other)

    clusters: dict[str, list[str]] = defaultdict(list)
    for lid in listing_words:
        clusters[find(lid)].append(lid)

    candidates = []
    for i, (root, member_ids) in enumerate(sorted(clusters.items()), start=1):
        member_ids = sorted(member_ids)
        cluster_words: Counter[str] = Counter()
        for lid in member_ids:
            # sorted(): a set intersection iterates in hash order, which
            # is randomized per Python process (PYTHONHASHSEED) -- feeding
            # Counter.update() an unsorted set made its most_common() tie
            # -break non-deterministic across runs (same bug class as
            # matching/candidates.py::title_fingerprint, Milestone 6).
            cluster_words.update(sorted(listing_words[lid] & connector_words))
        ranked = sorted(cluster_words.items(), key=lambda kv: (-kv[1], kv[0]))
        top_words = [w for w, _ in ranked[:2]] or ["unclustered"]
        suggested_name = " ".join(w.capitalize() for w in top_words)

        candidates.append(CandidateProductType(
            candidate_id=f"CAND_{i:02d}",
            suggested_name=suggested_name,
            connecting_words=top_words,
            listing_ids=member_ids,
            member_titles=[listing_titles[lid] for lid in member_ids],
        ))

    return sorted(candidates, key=lambda c: -c.size)
