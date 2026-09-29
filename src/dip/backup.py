"""Backup and restore of everything the platform owns.

    python scripts/dmis.py backup  [--out backups/]      -> dmis-backup-<utc>.tar.gz + manifest
    python scripts/dmis.py restore ARCHIVE [--force]

Embedded mode: the business DB (SQLite), analytics DuckDB, embedded graph, Qdrant local store
(``DIP_DATA_DIR``) and the whole lake (``DIP_LAKE_DIR``). Server mode:

* PostgreSQL is dumped with ``pg_dump`` when available (restore with ``psql``).
* Qdrant server (``DIP_QDRANT_URL``): a snapshot of every collection is taken through the snapshots API and
  stored as ``qdrant/<collection>.snapshot`` (restore: ``upload_qdrant_snapshot`` / docs/DEPLOYMENT_GUIDE.md).
  A failure is recorded in the manifest and does not abort the rest of the backup.
* Neo4j server (``DIP_NEO4J_URI``): not dumped online (``neo4j-admin database dump`` needs the database
  stopped, or Enterprise online backup). The manifest says so; the graph is derived data and is rebuilt by
  re-processing the datasets in the lake. See docs/DEPLOYMENT_GUIDE.md.

``data/raw/`` is source evidence under version control and is not part of the archive. Stop the API and
workers while restoring.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dip import __version__
from dip.settings import get_settings


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _qdrant_request(base: str, path: str, method: str = "GET", api_key: str | None = None, timeout: float = 600):
    req = urllib.request.Request(base.rstrip("/") + path, method=method)
    if api_key:
        req.add_header("api-key", api_key)
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 -- operator-configured DIP_QDRANT_URL


def qdrant_snapshots(out: Path, url: str, api_key: str | None = None) -> list[tuple[Path, str, str]]:
    """Snapshot every collection of a Qdrant server; returns (local file, archive name, collection)."""
    with _qdrant_request(url, "/collections", api_key=api_key, timeout=30) as r:
        names = [c["name"] for c in json.load(r)["result"]["collections"]]
    files = []
    for name in names:
        q = urllib.parse.quote(name, safe="")
        with _qdrant_request(url, f"/collections/{q}/snapshots?wait=true", "POST", api_key) as r:
            snap = json.load(r)["result"]["name"]
        dest = out / f"qdrant-{name}.snapshot"
        with _qdrant_request(url, f"/collections/{q}/snapshots/{urllib.parse.quote(snap, safe='')}", api_key=api_key) as r, \
                open(dest, "wb") as fh:
            shutil.copyfileobj(r, fh)
        try:                                                # the server keeps its copy otherwise; best effort
            _qdrant_request(url, f"/collections/{q}/snapshots/{urllib.parse.quote(snap, safe='')}", "DELETE", api_key, 30).close()
        except OSError:
            pass
        files.append((dest, f"qdrant/{name}.snapshot", name))
    return files


def upload_qdrant_snapshot(snapshot: str | Path, collection: str, url: str | None = None, api_key: str | None = None) -> dict:
    """Restore one collection on a Qdrant server from a snapshot file taken by ``backup``
    (``POST /collections/<c>/snapshots/upload?priority=snapshot``; the collection is replaced)."""
    import uuid

    st = get_settings()
    url, api_key = url or st.qdrant_url, api_key or st.qdrant_api_key
    if not url:
        raise ValueError("no Qdrant server configured (DIP_QDRANT_URL)")
    boundary = uuid.uuid4().hex
    data = Path(snapshot).read_bytes()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"snapshot\"; filename=\"{Path(snapshot).name}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{url.rstrip('/')}/collections/{urllib.parse.quote(collection, safe='')}/snapshots/upload"
                                 "?priority=snapshot&wait=true", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if api_key:
        req.add_header("api-key", api_key)
    with urllib.request.urlopen(req, timeout=3600) as r:  # noqa: S310
        return json.load(r)


def backup(out_dir: str | Path = "backups", services: bool = True) -> Path:
    """``services=False`` skips the Qdrant server snapshots (the embedded stores are always included)."""
    st = get_settings()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = out / f"dmis-backup-{stamp}.tar.gz"
    manifest = {"created_at": stamp, "version": __version__, "storage": st.describe(), "files": {}}
    extra: list[tuple[Path, str]] = []
    if st.postgres_url and shutil.which("pg_dump"):
        dump = out / f"postgres-{stamp}.sql"
        pg_url = st.postgres_url.replace("postgresql+psycopg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")
        subprocess.run(["pg_dump", "--no-owner", "-f", str(dump), pg_url], check=True)
        extra.append((dump, "postgres.sql"))
        manifest["postgres"] = "postgres.sql (restore with psql)"
    elif st.postgres_url:
        manifest["postgres"] = "NOT included: pg_dump not found -- back up PostgreSQL with its own tools"
    if st.qdrant_url and services:
        try:
            snaps = qdrant_snapshots(out, st.qdrant_url, st.qdrant_api_key)
            extra += [(f, arc) for f, arc, _ in snaps]
            manifest["qdrant"] = {c: {"file": arc, "sha256": _sha(f), "bytes": f.stat().st_size} for f, arc, c in snaps}
        except (OSError, ValueError, KeyError) as exc:     # unreachable / unexpected answer: say so, keep going
            manifest["qdrant"] = f"NOT included: snapshot failed ({exc}) -- use the Qdrant snapshots API"
    elif st.qdrant_url:
        manifest["qdrant"] = "NOT included (services=False)"
    if st.neo4j_uri:
        manifest["neo4j"] = ("NOT included: dump the server with `neo4j-admin database dump` (database stopped) or "
                             "rebuild the graph by re-processing the lake's datasets (derived data)")
    with tarfile.open(archive, "w:gz") as tar:
        for root, name in ((Path(st.data_dir), "platform"), (Path(st.lake_dir), "lake")):
            if not root.exists():
                continue
            for f in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.endswith((".lock", ".wal"))):
                arc = f"{name}/{f.relative_to(root).as_posix()}"
                tar.add(f, arcname=arc)
                manifest["files"][arc] = {"bytes": f.stat().st_size, "sha256": _sha(f)}
        for f, arc in extra:
            tar.add(f, arcname=arc)
        data = json.dumps(manifest, indent=1).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        import io

        tar.addfile(info, io.BytesIO(data))
    for f, _ in extra:
        f.unlink(missing_ok=True)
    return archive


def restore(archive: str | Path, force: bool = False) -> dict:
    """Restore into DIP_DATA_DIR / DIP_LAKE_DIR. Existing non-empty folders are moved aside to
    ``<folder>.before-restore-<utc>`` (only with force=True); every file is checked against the manifest."""
    st = get_settings()
    targets = {"platform": Path(st.data_dir), "lake": Path(st.lake_dir)}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with tarfile.open(archive, "r:gz") as tar:
        manifest = json.load(tar.extractfile("manifest.json"))
        busy = [str(p) for p in targets.values() if p.exists() and any(p.iterdir())]
        if busy and not force:
            raise FileExistsError(f"not empty: {busy}; pass force to move them aside and restore")
        moved = {}
        for name, p in targets.items():
            if p.exists() and any(p.iterdir()):
                aside = p.with_name(f"{p.name}.before-restore-{stamp}")
                p.rename(aside)
                moved[name] = str(aside)
            p.mkdir(parents=True, exist_ok=True)
        for m in tar.getmembers():
            top, _, rest = m.name.partition("/")
            if top not in targets or not rest or not m.isfile():
                continue
            dest = (targets[top] / rest).resolve()
            if targets[top].resolve() not in dest.parents:          # no path traversal
                raise ValueError(f"unsafe path in archive: {m.name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(m) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    bad = [arc for arc, meta in manifest["files"].items()
           if _sha(targets[arc.split("/", 1)[0]] / arc.split("/", 1)[1]) != meta["sha256"]]
    if bad:
        raise ValueError(f"checksum mismatch after restore: {bad[:5]}")
    get_settings.cache_clear()
    return {"restored_files": len(manifest["files"]), "created_at": manifest["created_at"], "moved_aside": moved,
            "postgres": manifest.get("postgres"), "qdrant": manifest.get("qdrant"), "neo4j": manifest.get("neo4j")}
