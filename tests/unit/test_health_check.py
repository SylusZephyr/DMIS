from dmie.database.connection import get_connection

EXPECTED_TABLES = {
    "categories",
    "listings",
    "listing_classification",
    "products",
    "product_listings",
    "review_insights",
    "decision_log",
}


def test_database_connects_and_has_expected_tables():
    con = get_connection()
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    finally:
        con.close()
    assert EXPECTED_TABLES.issubset(tables)
