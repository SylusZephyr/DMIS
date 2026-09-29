"""End-to-end: extraction -> repository write, using an isolated
in-memory DB and synthetic reviews (this dataset has no real review text
yet — see scripts/analyze_reviews.py)."""

from unittest.mock import patch

import duckdb
import pytest

from dmie.database.connection import PROJECT_ROOT
from dmie.database.repository import insert_review_insights
from dmie.reviews.extraction import extract_insight

SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"

SYNTHETIC_REVIEW = "The wax sheet cracked into pieces the very first time I tried to shape it."
VALID_AI_OUTPUT = {
    "pain_point_category": "QUALITY",
    "pain_point_subcategory": "breakage",
    "affected_attribute": "wax sheet",
    "severity": 3,
    "customer_complaint": "cracked on first use",
    "evidence": "cracked into pieces the very first time I tried to shape it",
    "potential_improvement": "improve durability",
    "confidence": 0.9,
}
INVALID_AI_OUTPUT = {**VALID_AI_OUTPUT, "evidence": "fabricated quote not in the review"}


@pytest.fixture()
def con():
    connection = duckdb.connect(":memory:")
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield connection
    connection.close()


def test_extracted_insight_is_written_to_review_insights(con):
    with patch("dmie.reviews.extraction.extract_with_ai", return_value=VALID_AI_OUTPUT):
        insight = extract_insight("L1", "P1", SYNTHETIC_REVIEW)
    insert_review_insights(con, [insight])

    row = con.execute("SELECT product_id, listing_id, pain_point, attribute, severity, evidence_text FROM review_insights").fetchone()
    assert row == ("P1", "L1", "QUALITY/breakage", "wax sheet", 3, VALID_AI_OUTPUT["evidence"])


def test_rejected_insight_is_logged_not_stored_as_fact(con):
    with patch("dmie.reviews.extraction.extract_with_ai", return_value=INVALID_AI_OUTPUT):
        insight = extract_insight("L1", "P1", SYNTHETIC_REVIEW)
    insert_review_insights(con, [insight])

    assert con.execute("SELECT COUNT(*) FROM review_insights").fetchone()[0] == 0
    log_row = con.execute(
        "SELECT entity_type, decision_type, actor FROM decision_log WHERE entity_type = 'review_insight'"
    ).fetchone()
    assert log_row == ("review_insight", "rejected_no_evidence", "review_extraction_pipeline")


def test_ai_unavailable_is_logged_not_stored(con):
    with patch("dmie.reviews.extraction.extract_with_ai", return_value=None):
        insight = extract_insight("L1", "P1", SYNTHETIC_REVIEW)
    insert_review_insights(con, [insight])

    assert con.execute("SELECT COUNT(*) FROM review_insights").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM decision_log WHERE decision_type = 'ai_unavailable'").fetchone()[0] == 1
