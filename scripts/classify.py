"""Run the hybrid relevance classifier over every listing in the DB and
write results into the listing_classification table.

Records this execution in pipeline_runs/classification_runs (Milestone
14) -- an append-only run history, never overwritten by a later run, so
"what did the last N runs of this category produce" is always answerable
even though listing_classification itself still just reflects the latest
run's output.

Usage: python scripts/classify.py [category_id]
Defaults to the denture_base pilot category. Scoped to that category's
own listings and its own relevance definition (config/categories.yaml)
-- a second real category exposed that this used to classify every
listing in the DB against the denture_base definition regardless of
`category_id` (invisible while denture_base was the only category).
"""

import sys
import time
from collections import Counter

from dmie.classification.classifier import CLASSIFIER_VERSION, ListingContext, classify_listing
from dmie.database.connection import get_connection
from dmie.database.repository import upsert_listing_classifications
from dmie.database.runs import finish_run, record_classification_run, start_run


def run(category: str = "denture_base") -> None:
    con = get_connection()
    run_id = start_run(con, category, "relevance_classification", CLASSIFIER_VERSION)
    try:
        rows = con.execute(
            "SELECT listing_id, title, brand FROM listings WHERE category_id = ?", [category]
        ).fetchall()
        contexts = [ListingContext(listing_id=r[0], title=r[1], brand=r[2]) for r in rows]
        # Real AI calls can be slow, retry, or (found once) hang on a
        # stuck connection -- a silent black box for the whole run made
        # that failure mode indistinguishable from normal progress for
        # nearly an hour. Printing per-listing keeps this observable and
        # flushes immediately so it's visible even when stdout is piped
        # to a file (Python fully buffers non-tty stdout otherwise).
        #
        # Flushed to the DB every FLUSH_EVERY listings, not only once at
        # the very end -- upsert_listing_classifications is a real UPSERT
        # (idempotent, safe to call repeatedly), and results used to only
        # live in this Python list until the whole category finished.
        # Found live: a single listing that hit a rare OS-level network
        # stall (see dmie.ai.client's hard-timeout fix) could cost hours;
        # losing every already-computed result along with it on any
        # interruption (a crash, a kill, a power loss) is a real
        # robustness gap this project shouldn't have, independent of how
        # rare the interruption is.
        FLUSH_EVERY = 20
        results = []
        flushed_count = 0
        for i, ctx in enumerate(contexts, start=1):
            t0 = time.monotonic()
            results.append(classify_listing(ctx, category=category))
            dt = time.monotonic() - t0
            if i % 10 == 0 or i == len(contexts) or dt > 15:
                print(f"[{i}/{len(contexts)}] {dt:.2f}s listing_id={ctx.listing_id}", flush=True)
            if i % FLUSH_EVERY == 0 or i == len(contexts):
                upsert_listing_classifications(con, results[flushed_count:i])
                flushed_count = i
                print(f"  (flushed to DB, {i}/{len(contexts)} total so far)", flush=True)

        counts = Counter(r.relevance_class for r in results)
        review_counts = Counter(r.review_status for r in results)
        record_classification_run(
            con, run_id, category, stage="relevance", classifier_version=CLASSIFIER_VERSION,
            total_classified=len(results), class_counts=dict(counts),
            auto_accepted_count=review_counts.get("auto_accepted", 0),
            needs_review_count=review_counts.get("needs_review", 0),
        )
        finish_run(con, run_id, summary={"class_counts": dict(counts), "review_counts": dict(review_counts)})
    except Exception as exc:
        finish_run(con, run_id, summary={}, error_message=str(exc))
        raise
    finally:
        con.close()

    print(f"run_id: {run_id}")
    print(f"classified: {len(results)} listings")
    print(f"relevance_class: {dict(counts)}")
    print(f"review_status: {dict(review_counts)}")


if __name__ == "__main__":
    category_arg = sys.argv[1] if len(sys.argv) > 1 else "denture_base"
    run(category_arg)
