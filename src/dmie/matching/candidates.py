"""Stage 5: candidate blocking.

Generates candidate listing pairs cheaply instead of comparing every
listing against every other listing (O(n^2)). Two independent blocking
strategies, unioned:

- brand block: listings sharing a normalized, specific (non-generic) brand
- title-fingerprint block: listings sharing their most distinctive words

Both are needed — manual review (docs/entity_resolution.md) found both
same-brand color/size variants AND cross-brand reseller duplicates of the
same underlying product, so blocking on brand alone would miss the latter.
"""

from __future__ import annotations

import re
from collections import defaultdict
from itertools import combinations

from dmie.matching.similarity import normalize_brand

_STOPWORDS = {
    "the", "for", "and", "with", "a", "an", "of", "to", "in", "on", "kit",
    "set", "denture", "dentures", "dental", "tooth", "teeth", "pack",
    "box", "pcs", "pc",
}
_WORD_RE = re.compile(r"[a-z0-9]+")


def title_fingerprint(title: str | None, n: int = 3) -> tuple[str, ...]:
    """The n longest non-stopword words in the (lowercased) title, sorted
    — a cheap blocking key that groups near-duplicate titles together
    regardless of brand."""
    words = [w for w in _WORD_RE.findall((title or "").lower()) if w not in _STOPWORDS and len(w) > 2]
    # Secondary alphabetical key makes tie-breaking deterministic -- Python's
    # set iteration order is hash-randomized per process, so sorting only by
    # -len(w) would silently vary the chosen words across runs on ties.
    longest = sorted(set(words), key=lambda w: (-len(w), w))[:n]
    return tuple(sorted(longest))


def generate_candidate_pairs(listings: list[dict]) -> list[tuple[dict, dict, str]]:
    """listings: list of dicts with at least listing_id, title, brand.
    Returns (listing_a, listing_b, blocking_method) for every pair sharing
    a block, deduplicated across both blocking strategies."""
    brand_blocks: dict[str, list[dict]] = defaultdict(list)
    title_blocks: dict[tuple[str, ...], list[dict]] = defaultdict(list)

    for listing in listings:
        brand_key = normalize_brand(listing.get("brand"))
        if brand_key not in (None, "generic"):
            brand_blocks[brand_key].append(listing)
        fp = title_fingerprint(listing.get("title"))
        if fp:
            title_blocks[fp].append(listing)

    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[dict, dict, str]] = []

    def _add(bucket: list[dict], method: str) -> None:
        for a, b in combinations(bucket, 2):
            key = tuple(sorted((a["listing_id"], b["listing_id"])))
            if key in seen:
                continue
            seen.add(key)
            pairs.append((a, b, method))

    for bucket in brand_blocks.values():
        if len(bucket) > 1:
            _add(bucket, "brand_block")
    for bucket in title_blocks.values():
        if len(bucket) > 1:
            _add(bucket, "title_fingerprint_block")

    return pairs
