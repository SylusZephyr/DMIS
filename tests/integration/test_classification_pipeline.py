"""End-to-end: classify the real denture_base listings and evaluate
against the real gold dataset, using an isolated in-memory DB so this
never touches (or depends on having already run) the real project DB.
"""

import hashlib

import duckdb
import pytest

from dmie.classification.classifier import ListingContext, classify_listing
from dmie.classification.evaluation import GOLD_PATH, evaluate
from dmie.database.connection import PROJECT_ROOT, get_connection
from dmie.database.repository import upsert_listing_classifications

SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"
# SHA-256 of the gold file as checked out (CRLF, see .gitattributes). Re-pinned once after the
# reviewer metadata columns were renamed (reviewer, review_status); every label column is unchanged.
GOLD_SHA256_BEFORE = "792305a81113a1a0676dabe93d0b5e84cdecab1de1daef6e113cca7bf0bd9661"


@pytest.fixture(scope="module")
def report():
    prod_con = get_connection()
    try:
        rows = prod_con.execute("SELECT listing_id, title, brand FROM listings").fetchall()
    finally:
        prod_con.close()

    con = duckdb.connect(":memory:")
    con.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    contexts = [ListingContext(listing_id=r[0], title=r[1], brand=r[2]) for r in rows]
    results = [classify_listing(ctx) for ctx in contexts]
    upsert_listing_classifications(con, results)

    result = evaluate(con)
    con.close()
    return result


def test_evaluates_all_50_gold_rows(report):
    assert report.n_evaluated == 50


def test_committed_predictions_are_high_precision(report):
    """Rules are designed to be conservative: whenever the system commits
    to RELEVANT or IRRELEVANT (i.e. doesn't punt to UNCERTAIN), it should
    agree with the human gold label almost all the time. UNCERTAIN is
    always an acceptable outcome and isn't bounded here."""
    committed = {m.label: m for m in report.metrics if m.label in ("RELEVANT", "IRRELEVANT")}
    assert committed["RELEVANT"].precision >= 0.9
    assert committed["IRRELEVANT"].precision >= 0.9


def test_gold_dataset_file_is_never_modified():
    digest = hashlib.sha256(GOLD_PATH.read_bytes()).hexdigest()
    assert digest == GOLD_SHA256_BEFORE
