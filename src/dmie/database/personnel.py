"""Personnel Intelligence (Milestone 12) -- real employee/category
ownership data, not fabricated. See schema.sql's comments on `employees`
and `employee_category_ownership`, and docs/personnel_intelligence.md
for provenance and scope.
"""

from __future__ import annotations

import csv
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb


def make_employee_id(name: str) -> str:
    """Deterministic, reproducible id from a real employee name -- the
    same name always yields the same id (idempotent re-loads), and the
    id itself carries no invented meaning (just a stable hash), unlike
    e.g. a sequential integer that would depend on load order."""
    return hashlib.sha1(name.strip().encode("utf-8")).hexdigest()[:16]


def load_ownership_csv(con: duckdb.DuckDBPyConnection, csv_path: Path) -> dict:
    """Reads scripts/extract_from_merged_export.py's real ownership CSV
    and (re)populates employees + employee_category_ownership from
    scratch (regenerate-from-scratch pattern, same as match_candidates/
    products -- a re-run with an updated export must not leave stale
    rows behind). Returns a summary dict for the caller to print."""
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    now = datetime.now(timezone.utc)

    employees: dict[str, str] = {}  # name -> employee_id
    for row in rows:
        name = row["product_manager"].strip()
        if name and name not in employees:
            employees[name] = make_employee_id(name)

    con.execute("DELETE FROM employee_category_ownership")
    con.execute("DELETE FROM employees")

    if employees:
        con.executemany(
            "INSERT INTO employees (employee_id, name, created_at) VALUES (?, ?, ?)",
            [[emp_id, name, now] for name, emp_id in employees.items()],
        )

    ownership_rows = []
    for row in rows:
        name = row["product_manager"].strip()
        if not name:
            continue
        ownership_rows.append([
            uuid.uuid4().hex, employees[name], row["raw_category_label"],
            row["matched_category_id"] or None, int(row["listing_count"]),
            str(csv_path.name), now,
        ])
    if ownership_rows:
        con.executemany(
            "INSERT INTO employee_category_ownership (ownership_id, employee_id, raw_category_label, "
            "matched_category_id, listing_count, source_file, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ownership_rows,
        )

    return {
        "employees": len(employees),
        "ownership_rows": len(ownership_rows),
        "matched_category_rows": sum(1 for r in ownership_rows if r[3]),
    }


def list_employees(con: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = con.execute(
        """
        SELECT e.employee_id, e.name,
               COUNT(*) AS raw_category_count,
               COUNT(DISTINCT o.matched_category_id) FILTER (WHERE o.matched_category_id IS NOT NULL) AS onboarded_category_count,
               SUM(o.listing_count) AS total_listings
        FROM employees e
        JOIN employee_category_ownership o ON o.employee_id = e.employee_id
        GROUP BY e.employee_id, e.name
        ORDER BY total_listings DESC
        """
    ).fetchall()
    cols = ["employee_id", "name", "raw_category_count", "onboarded_category_count", "total_listings"]
    return [dict(zip(cols, r)) for r in rows]


def get_employee_categories(con: duckdb.DuckDBPyConnection, employee_id: str) -> list[dict]:
    rows = con.execute(
        "SELECT raw_category_label, matched_category_id, listing_count FROM employee_category_ownership "
        "WHERE employee_id = ? ORDER BY listing_count DESC",
        [employee_id],
    ).fetchall()
    cols = ["raw_category_label", "matched_category_id", "listing_count"]
    return [dict(zip(cols, r)) for r in rows]


def get_category_owners(con: duckdb.DuckDBPyConnection, category_id: str) -> list[dict]:
    """Which real employee(s) handle this onboarded category, ranked by
    real listing_count share -- the primary owner is whoever handles the
    most listings for it, not an assumption."""
    rows = con.execute(
        "SELECT e.employee_id, e.name, o.listing_count FROM employee_category_ownership o "
        "JOIN employees e ON e.employee_id = o.employee_id "
        "WHERE o.matched_category_id = ? ORDER BY o.listing_count DESC",
        [category_id],
    ).fetchall()
    cols = ["employee_id", "name", "listing_count"]
    return [dict(zip(cols, r)) for r in rows]
