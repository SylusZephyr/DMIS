"""Load the real employee/category-ownership data produced by
scripts/extract_from_merged_export.py into the database.

Usage: python scripts/load_employee_ownership.py
Defaults to data/raw/employee_category_ownership.csv.
"""

import sys
from pathlib import Path

from dmie.database.connection import PROJECT_ROOT, get_connection
from dmie.database.personnel import load_ownership_csv


def run(csv_path: Path) -> None:
    con = get_connection()
    try:
        summary = load_ownership_csv(con, csv_path)
    finally:
        con.close()
    print(f"employees: {summary['employees']}")
    print(f"ownership rows: {summary['ownership_rows']}")
    print(f"rows matched to an onboarded category: {summary['matched_category_rows']}")


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "data" / "raw" / "employee_category_ownership.csv"
    run(path)
