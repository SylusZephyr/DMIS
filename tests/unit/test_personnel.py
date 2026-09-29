"""Personnel Intelligence (Milestone 12) -- real employee/category
ownership data, not fabricated. See dmie.database.personnel.
"""

import csv

import duckdb
import pytest

from dmie.database.connection import PROJECT_ROOT
from dmie.database.personnel import (
    get_category_owners,
    get_employee_categories,
    list_employees,
    load_ownership_csv,
    make_employee_id,
)

SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"


@pytest.fixture
def con():
    connection = duckdb.connect(":memory:")
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield connection
    connection.close()


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["raw_category_label", "product_manager", "listing_count", "matched_category_id"]
        )
        writer.writeheader()
        writer.writerows(rows)


def test_make_employee_id_is_deterministic():
    assert make_employee_id("倪政") == make_employee_id("倪政")
    assert make_employee_id("倪政") != make_employee_id("周萍")


def test_load_ownership_csv_populates_employees_and_ownership(con, tmp_path):
    csv_path = tmp_path / "ownership.csv"
    _write_csv(csv_path, [
        {"raw_category_label": "医师椅", "product_manager": "倪政", "listing_count": 1000, "matched_category_id": ""},
        {"raw_category_label": "denture_base", "product_manager": "倪政", "listing_count": 50, "matched_category_id": "denture_base"},
        {"raw_category_label": "乳胶手套", "product_manager": "周萍", "listing_count": 1000, "matched_category_id": ""},
    ])

    summary = load_ownership_csv(con, csv_path)

    assert summary == {"employees": 2, "ownership_rows": 3, "matched_category_rows": 1}
    assert con.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM employee_category_ownership").fetchone()[0] == 3


def test_load_ownership_csv_regenerates_from_scratch(con, tmp_path):
    csv_path = tmp_path / "ownership.csv"
    _write_csv(csv_path, [{"raw_category_label": "a", "product_manager": "倪政", "listing_count": 1, "matched_category_id": ""}])
    load_ownership_csv(con, csv_path)

    _write_csv(csv_path, [{"raw_category_label": "b", "product_manager": "周萍", "listing_count": 2, "matched_category_id": ""}])
    summary = load_ownership_csv(con, csv_path)

    assert summary == {"employees": 1, "ownership_rows": 1, "matched_category_rows": 0}
    names = [row[0] for row in con.execute("SELECT name FROM employees").fetchall()]
    assert names == ["周萍"]


def test_get_employee_categories_and_category_owners(con, tmp_path):
    csv_path = tmp_path / "ownership.csv"
    _write_csv(csv_path, [
        {"raw_category_label": "denture_base", "product_manager": "倪政", "listing_count": 30, "matched_category_id": "denture_base"},
        {"raw_category_label": "denture_base", "product_manager": "周萍", "listing_count": 10, "matched_category_id": "denture_base"},
    ])
    load_ownership_csv(con, csv_path)

    ni_zheng_id = make_employee_id("倪政")
    categories = get_employee_categories(con, ni_zheng_id)
    assert categories == [{"raw_category_label": "denture_base", "matched_category_id": "denture_base", "listing_count": 30}]

    owners = get_category_owners(con, "denture_base")
    assert [o["name"] for o in owners] == ["倪政", "周萍"]  # ranked by listing_count desc


def test_list_employees_ranks_by_total_listings(con, tmp_path):
    csv_path = tmp_path / "ownership.csv"
    _write_csv(csv_path, [
        {"raw_category_label": "a", "product_manager": "周萍", "listing_count": 5, "matched_category_id": ""},
        {"raw_category_label": "a", "product_manager": "倪政", "listing_count": 500, "matched_category_id": ""},
    ])
    load_ownership_csv(con, csv_path)

    employees = list_employees(con)
    assert [e["name"] for e in employees] == ["倪政", "周萍"]
