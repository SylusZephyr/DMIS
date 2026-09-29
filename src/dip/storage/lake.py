"""Data lake: Hive-partitioned Parquet zones.

    raw/source=<kind>/dataset=<id>/data.parquet        exactly as received (strings), immutable
    std/dataset=<id>/data.parquet                      universal MarketRecord schema + quality + relevance
    curated/<table>/market=<m>/data.parquet            current products / segments / listings per market

Queries go through DuckDB ``read_parquet`` so analytics scale beyond
memory; writes use Polars/pyarrow. ``data/raw/`` (v1 immutable exports) is
never written to.

Safety:
* market names are validated (``validate_market_name``) before they become part of a path,
  and every path handed to DuckDB is a quoted, escaped SQL literal inside the lake root
* every file is written to a temporary file in the same directory and swapped in with
  ``os.replace`` (atomic), so a reader never sees a half-written Parquet file and a failed
  write leaves the previous file intact
* ``market_lock`` serialises processing runs of one market across threads and processes.
* ``batch()`` makes a run's curated writes all-or-nothing: inside it every curated table is written
  to a staged file, and only when the whole block succeeds are they swapped in (``os.replace`` each,
  milliseconds in total); if anything fails, the staged files are removed and every table keeps its
  previous version -- a failed run never leaves new and old numbers mixed. Reads inside the block
  see the previous versions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

import duckdb
import pandas as pd

from dip.settings import get_settings


def _root() -> Path:
    return get_settings().lake_dir


# ------------------------------------------------------------------ names and paths
MARKET_NAME_MAX = 64
# a letter or digit (any script), then letters, digits, spaces, '_', '-', '.'; no quotes, slashes, '=', '%', glob chars
_MARKET_RE = re.compile(r"^[^\W_][\w .\-]*$")
_PART_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")   # tables, dataset ids, source kinds (internal identifiers)


class InvalidMarketName(ValueError):
    """A market name that cannot safely become a lake path / SQL literal."""


def validate_market_name(name) -> str:
    """The stripped market name, or ``InvalidMarketName`` with a message a user can act on."""
    if not isinstance(name, str) or not name.strip():
        raise InvalidMarketName("market name is required")
    n = name.strip()
    if len(n) > MARKET_NAME_MAX:
        raise InvalidMarketName(f"market name is too long ({len(n)} characters; at most {MARKET_NAME_MAX})")
    if ".." in n or n.endswith(".") or not _MARKET_RE.match(n):
        raise InvalidMarketName(
            f"invalid market name {n!r}: use letters, digits, spaces, '_', '-' or '.', starting with a letter or "
            f"digit (no quotes, slashes or '..')")
    return n


def is_valid_market_name(name) -> bool:
    try:
        validate_market_name(name)
        return True
    except InvalidMarketName:
        return False


def _part(value: str, what: str) -> str:
    if not isinstance(value, str) or not _PART_RE.match(value):
        raise ValueError(f"invalid {what} {value!r}")
    return value


def _inside_root(path: Path) -> Path:
    """Refuse any path that resolves outside the lake root or cannot be a safe SQL literal."""
    root = _root().resolve()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path {path} is outside the data lake")
    return path


def sql_literal(path) -> str:
    """A path as a DuckDB string literal (single quotes doubled)."""
    return "'" + str(path).replace("'", "''") + "'"


def _market_dir(table: str, market: str) -> Path:
    return _inside_root(_root() / "curated" / _part(table, "table") / f"market={validate_market_name(market)}")


_STAGED: ContextVar[list | None] = ContextVar("lake_staged", default=None)


@contextmanager
def batch():
    """All-or-nothing curated writes (see the module docstring). Nested batches join the outer one."""
    if _STAGED.get() is not None:
        yield
        return
    staged: list[tuple[Path, Path]] = []
    token = _STAGED.set(staged)
    try:
        yield
    except BaseException:
        _STAGED.reset(token)
        for tmp, _ in staged:
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    _STAGED.reset(token)
    for tmp, final in staged:          # commit: each swap is atomic, the whole set takes milliseconds
        os.replace(tmp, final)


def _atomic_write(path: Path, write) -> Path:
    """``write(tmp_path)`` then an atomic swap into ``path``; a failure leaves ``path`` untouched. Inside
    ``batch()`` the swap of curated files waits until the batch succeeds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    staged = _STAGED.get()
    try:
        write(tmp)
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        if staged is not None and "curated" in path.parts:
            for t, f in [x for x in staged if x[1] == path]:          # a table written twice: the last write wins
                staged.remove((t, f))
                try:
                    t.unlink()
                except OSError:
                    pass
            staged.append((tmp, path))
            return path
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


def _write_parquet(df: pd.DataFrame, path: Path) -> Path:
    return _atomic_write(path, lambda tmp: df.to_parquet(tmp, index=False))


# ------------------------------------------------------------------ per-market processing lock
class MarketBusy(RuntimeError):
    """Another job is processing this market."""


def _lock_timeout() -> float:
    try:
        return float(os.environ.get("DIP_MARKET_LOCK_TIMEOUT", "1800"))
    except ValueError:
        return 1800.0


@contextmanager
def market_lock(market: str, timeout: float | None = None):
    """Exclusive processing lock for one market (an OS file lock under <lake>/_locks, released on exit
    or when the process dies). Waits up to ``timeout`` seconds (env DIP_MARKET_LOCK_TIMEOUT, default
    30 min), then raises ``MarketBusy``."""
    market = validate_market_name(market)
    timeout = _lock_timeout() if timeout is None else timeout
    d = _root() / "_locks"
    d.mkdir(parents=True, exist_ok=True)
    fh = open(_inside_root(d / f"market={market}.lock"), "a+b")
    deadline = time.monotonic() + max(timeout, 0.0)
    try:
        while not _try_lock(fh):
            if time.monotonic() >= deadline:
                raise MarketBusy(f"market '{market}' is being processed by another job; "
                                 f"try again when that job has finished")
            time.sleep(0.2)
        try:
            yield
        finally:
            _unlock(fh)
    finally:
        fh.close()


def _try_lock(fh) -> bool:
    try:
        import fcntl

        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    except ImportError:  # Windows
        import msvcrt

        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False


def _unlock(fh) -> None:
    try:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except ImportError:
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


def content_hash(path_or_bytes) -> str:
    h = hashlib.sha256()
    if isinstance(path_or_bytes, (bytes, bytearray)):
        h.update(path_or_bytes)
    else:
        with open(path_or_bytes, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def _to_parquet_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Parquet needs one type per column: nested/mixed objects become JSON text.
    Plain string columns are left alone (the check runs in C, not per cell)."""
    out = df.copy()
    for c in out.columns:
        if out[c].dtype != object:
            continue
        kind = pd.api.types.infer_dtype(out[c], skipna=True)
        if kind in ("string", "empty"):
            out[c] = out[c].where(out[c].notna(), None)
            continue
        out[c] = out[c].map(lambda v: None if v is None or (isinstance(v, float) and pd.isna(v))
                            else json.dumps(v, default=str, ensure_ascii=False) if isinstance(v, (dict, list, tuple))
                            else str(v))
    out.columns = [str(c) for c in out.columns]
    return out


def write_raw(df: pd.DataFrame, source_kind: str, dataset_id: str) -> Path:
    return _write_parquet(df.astype("string"), raw_path(source_kind, dataset_id))


def raw_path(source_kind: str, dataset_id: str) -> Path:
    return _inside_root(_root() / "raw" / f"source={_part(source_kind, 'source kind')}"
                        / f"dataset={_part(dataset_id, 'dataset id')}" / "data.parquet")


def move_raw(path: Path, source_kind: str, dataset_id: str) -> Path:
    dest = raw_path(source_kind, dataset_id)
    if path != dest:
        dest.parent.mkdir(parents=True, exist_ok=True)
        path.replace(dest)
        try:
            path.parent.rmdir()
            path.parent.parent.rmdir()
        except OSError:
            pass
    return dest


def write_std(df: pd.DataFrame, dataset_id: str) -> Path:
    return _write_parquet(_to_parquet_safe(df), std_path(dataset_id))


def std_path(dataset_id: str) -> Path:
    return _inside_root(_root() / "std" / f"dataset={_part(dataset_id, 'dataset id')}" / "data.parquet")


def read_std(dataset_ids: list[str], columns: list[str], where: str | None = None) -> pd.DataFrame:
    """Records of earlier datasets (the std zone keeps every upload). Missing files are skipped;
    columns absent from an older file come back as NULL."""
    files = [str(std_path(d)) for d in dataset_ids if std_path(d).exists()]
    if not files:
        return pd.DataFrame(columns=columns + ["dataset_id"])
    lst = "[" + ", ".join(sql_literal(f) for f in files) + "]"
    con = duckdb.connect()
    try:
        have = set(con.execute(f"DESCRIBE SELECT * FROM read_parquet({lst}, union_by_name=true)").df()["column_name"])
        sel = ", ".join(f'"{c}"' if c in have else f'NULL AS "{c}"' for c in columns)
        sql = f"SELECT {sel}, regexp_extract(filename, 'dataset=([^/\\\\]+)', 1) AS dataset_id " \
              f"FROM read_parquet({lst}, union_by_name=true, filename=true)"
        if where:
            sql += f" WHERE {where}"
        return con.execute(sql).df()
    finally:
        con.close()


def write_curated(table: str, market: str, df: pd.DataFrame) -> Path:
    return _write_parquet(_to_parquet_safe(df), _market_dir(table, market) / "data.parquet")


def copy_to_curated(src: Path, table: str, market: str) -> Path:
    import shutil

    return _atomic_write(_market_dir(table, market) / "data.parquet", lambda tmp: shutil.copyfile(src, tmp))


def curated_path(table: str, market: str | None = None) -> str:
    """Path (or glob over markets) of a curated table. Raises for an unsafe market name, so the
    result is always safe inside a single-quoted DuckDB literal."""
    if market:
        path = str(_market_dir(table, market) / "data.parquet")
    else:
        path = str(_inside_root(_root() / "curated" / _part(table, "table")) / "*" / "data.parquet")
    if "'" in path:
        raise ValueError("the lake directory path contains a quote; configure DIP_LAKE_DIR without quotes")
    return path


def has_curated(table: str, market: str | None = None) -> bool:
    if market:
        if not is_valid_market_name(market):
            return False     # such a market can never have been written
        return (_market_dir(table, market) / "data.parquet").exists()
    base = _root() / "curated" / _part(table, "table")
    return base.exists() and any(base.glob("*/data.parquet"))


def query(sql: str, params: list | None = None) -> pd.DataFrame:
    """DuckDB SQL over the lake. Use {curated:<table>} / {curated:<table>:<market>} placeholders."""
    con = duckdb.connect()
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()


def curated_columns(table: str, market: str | None = None) -> set[str]:
    """Column names of a curated table (empty set when it does not exist)."""
    if not has_curated(table, market):
        return set()
    df = query(f"DESCRIBE SELECT * FROM read_parquet({sql_literal(curated_path(table, market))}, "
               f"hive_partitioning=true, union_by_name=true)")
    return set(df["column_name"])


def read_curated(table: str, market: str | None = None, columns: list[str] | None = None,
                 where: str | None = None, order: str | None = None, limit: int | None = None,
                 offset: int = 0, params: list | None = None) -> pd.DataFrame:
    if not has_curated(table, market):
        return pd.DataFrame()
    cols = ", ".join(f'"{c}"' for c in columns) if columns else "*"
    sql = f"SELECT {cols} FROM read_parquet({sql_literal(curated_path(table, market))}, hive_partitioning=true, union_by_name=true)"
    if where:
        sql += f" WHERE {where}"
    if order:
        sql += f" ORDER BY {order}"
    if limit is not None:
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    return query(sql, params)


def decode_json(df: pd.DataFrame, cols: tuple[str, ...]) -> pd.DataFrame:
    for c in cols:
        if c in df:
            df[c] = df[c].map(lambda v: json.loads(v) if isinstance(v, str) and v[:1] in "[{" else v)
    return df
