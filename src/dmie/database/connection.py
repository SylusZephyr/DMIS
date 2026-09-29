"""DuckDB connection helper for the DMIE project."""

import os
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = PROJECT_ROOT / "database" / "dmie.duckdb"


def get_connection(db_path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open a connection to the project's DuckDB database.

    Path resolution order: explicit argument, DMIE_DB_PATH env var, default project path.
    """
    path = db_path or os.environ.get("DMIE_DB_PATH") or DEFAULT_DB_PATH
    return duckdb.connect(str(path))
