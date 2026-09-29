"""Stage 1 driver for the discovery -> review -> freeze taxonomy workflow
(docs/product_taxonomy.md): runs product_type_discovery.py's deterministic
clustering over a category's RELEVANT listings and writes a plain-text
report for review. Never writes a taxonomy file itself — that's a
separate, deliberate step after a human (or, when none is available,
a documented autonomous decision -- see DECISIONS.md) reviews the
candidates.

Usage: python scripts/discover_product_types.py <category_id>
"""

import sys

import duckdb

from dmie.classification.product_type_discovery import discover_candidate_types
from dmie.database.connection import DEFAULT_DB_PATH, PROJECT_ROOT


def run(category: str) -> None:
    # read_only=True (unlike most scripts here, which write): this is a
    # pure SELECT, and DuckDB allows a read-only connection to coexist
    # with another process's read-write connection to the same file
    # (MVCC) -- lets discovery run concurrently with a still-in-progress
    # classify.py for a different category instead of waiting on it.
    con = duckdb.connect(str(DEFAULT_DB_PATH), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT l.listing_id, l.title
            FROM listings l
            JOIN listing_classification lc ON lc.listing_id = l.listing_id
            WHERE l.category_id = ? AND lc.relevance_class = 'RELEVANT'
            """,
            [category],
        ).fetchall()
    finally:
        con.close()

    listings = [{"listing_id": r[0], "title": r[1]} for r in rows]
    candidates = discover_candidate_types(listings)

    out_path = PROJECT_ROOT / "data" / "exports" / category / "product_type_discovery_candidates.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"Discovery input: {len(listings)} RELEVANT listings for category={category}", ""]
    for c in candidates:
        lines.append(f"{c.candidate_id}  suggested_name={c.suggested_name!r}  size={c.size}  words={c.connecting_words}")
        for title in c.member_titles[:5]:
            lines.append(f"    - {title}")
        if c.size > 5:
            lines.append(f"    ... and {c.size - 5} more")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"relevant listings: {len(listings)}")
    print(f"candidate clusters: {len(candidates)}")
    print(f"report: {out_path}")


if __name__ == "__main__":
    run(sys.argv[1])
