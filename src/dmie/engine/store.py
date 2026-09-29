"""DuckDB persistence for the engine (``mi_*`` tables).

The engine's tables live alongside -- and never touch -- the Phase-1
tables. Each run replaces a market's current rows (so dashboards read one
coherent state) while ``mi_runs`` and ``mi_history`` keep every run, so
nothing about past runs is lost.

Tables are created from the DataFrames' own schemas; list/dict cells are
stored as JSON text. New columns are added automatically, so modules can
grow without migration scripts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from dmie.database.connection import get_connection

FEEDBACK_DDL = """
CREATE TABLE IF NOT EXISTS mi_relevance_feedback (
    domain VARCHAR,
    record_key VARCHAR,
    market_name VARCHAR,
    text VARCHAR,
    label BOOLEAN,
    corrected_by VARCHAR,
    note VARCHAR,
    created_at TIMESTAMP
)"""
RUNS_DDL = """
CREATE TABLE IF NOT EXISTS mi_runs (
    run_id VARCHAR PRIMARY KEY,
    market_name VARCHAR,
    source VARCHAR,
    adapter VARCHAR,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    status VARCHAR,
    summary VARCHAR
)"""
HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS mi_history (
    market_name VARCHAR,
    period DATE,
    subject VARCHAR,
    subject_label VARCHAR,
    revenue DOUBLE,
    sales DOUBLE,
    run_id VARCHAR
)"""


def connect(db_path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    con = get_connection(db_path)
    ensure_schema(con)
    return con


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in (FEEDBACK_DDL, RUNS_DDL, HISTORY_DDL):
        con.execute(ddl)


def _jsonable(v):
    if isinstance(v, (list, dict, tuple)):
        return json.dumps(v, default=_default, ensure_ascii=False)
    if isinstance(v, np.ndarray):
        return json.dumps(v.tolist(), default=_default)
    return v


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return str(o)


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object:
            if out[c].map(lambda v: isinstance(v, (list, dict, tuple, np.ndarray))).any():
                out[c] = out[c].map(_jsonable)
            else:
                out[c] = out[c].map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else str(v))
        elif str(out[c].dtype) == "bool":
            out[c] = out[c].astype(bool)
    return out


def _table_exists(con, table: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]).fetchone()[0])


def replace_rows(con: duckdb.DuckDBPyConnection, table: str, market_name: str, df: pd.DataFrame) -> int:
    """Replace ``market_name``'s rows in ``table`` with ``df`` (market_name column added)."""
    data = _prepare(df.assign(market_name=market_name))
    con.register("_mi_df", data)
    try:
        if not _table_exists(con, table):
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM _mi_df LIMIT 0")
        existing = {r[0] for r in con.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'").fetchall()}
        for col in data.columns:
            if col not in existing:
                dtype = con.execute(f"SELECT typeof(\"{col}\") FROM _mi_df LIMIT 1").fetchone()
                sql_type = dtype[0] if dtype and dtype[0] != "NULL" else "VARCHAR"
                con.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" {sql_type}')
        con.execute(f"DELETE FROM {table} WHERE market_name = ?", [market_name])
        if len(data):
            con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _mi_df")
    finally:
        con.unregister("_mi_df")
    return len(data)


def read_table(con: duckdb.DuckDBPyConnection, table: str, market_name: str | None = None,
               json_cols: tuple[str, ...] = ()) -> pd.DataFrame:
    if not _table_exists(con, table):
        return pd.DataFrame()
    if market_name is None:
        df = con.execute(f"SELECT * FROM {table}").df()
    else:
        df = con.execute(f"SELECT * FROM {table} WHERE market_name = ?", [market_name]).df()
    for c in json_cols:
        if c in df:
            df[c] = df[c].map(lambda v: json.loads(v) if isinstance(v, str) and v[:1] in "[{" else v)
    return df


def list_markets(con: duckdb.DuckDBPyConnection) -> list[str]:
    if not _table_exists(con, "mi_markets"):
        return []
    return [r[0] for r in con.execute("SELECT market_name FROM mi_markets ORDER BY updated_at DESC").fetchall()]


def market_summary(con: duckdb.DuckDBPyConnection, market_name: str) -> dict | None:
    if not _table_exists(con, "mi_markets"):
        return None
    row = con.execute("SELECT summary FROM mi_markets WHERE market_name = ?", [market_name]).fetchone()
    return json.loads(row[0]) if row else None


def record_run(con, run_id: str, market_name: str, source: str, adapter: str, started: datetime,
               status: str, summary: dict) -> None:
    con.execute("DELETE FROM mi_runs WHERE run_id = ?", [run_id])
    con.execute("INSERT INTO mi_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, market_name, source, adapter, started, datetime.now(timezone.utc), status,
                 json.dumps(summary, default=_default, ensure_ascii=False)])


def append_history(con, market_name: str, period, rows: list[dict], run_id: str) -> None:
    """One point per (market, period, subject); re-running the same period replaces it."""
    con.execute("DELETE FROM mi_history WHERE market_name = ? AND period = ?", [market_name, period])
    if rows:
        con.executemany("INSERT INTO mi_history VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [[market_name, period, r["subject"], r.get("subject_label"), r.get("revenue"), r.get("sales"), run_id] for r in rows])


def read_history(con, market_name: str) -> pd.DataFrame:
    return con.execute("SELECT period, subject, subject_label, revenue, sales FROM mi_history WHERE market_name = ? ORDER BY period",
                       [market_name]).df()


def add_feedback(con, domain: str, record_key: str, text: str, label: bool, market_name: str | None = None,
                 corrected_by: str = "user", note: str | None = None) -> None:
    con.execute("DELETE FROM mi_relevance_feedback WHERE domain = ? AND record_key = ?", [domain, record_key])
    con.execute("INSERT INTO mi_relevance_feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [domain, record_key, market_name, text, label, corrected_by, note, datetime.now(timezone.utc)])


def read_feedback(con, domain: str) -> pd.DataFrame:
    return con.execute("SELECT record_key, text, label FROM mi_relevance_feedback WHERE domain = ?", [domain]).df()
