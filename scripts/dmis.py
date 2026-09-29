"""Intelligence Platform v2 command line.

    python scripts/dmis.py bootstrap            # ownership file + every data/raw/<market>/ export -> v2
    python scripts/dmis.py process FILE --market NAME [--snapshot-date YYYY-MM-DD] [--reviews FILE] [--marketplace US]
    python scripts/dmis.py process-dir FOLDER --market NAME [--force]   # batch: successive snapshots of one market
    python scripts/dmis.py import-suppliers FILE
    python scripts/dmis.py serve [--port 8000] [--workers N]  # API (frontend: cd frontend && npm run dev)
                                              # N > 1 (or DIP_API_WORKERS): uvicorn worker processes; needs
                                              # PostgreSQL + Neo4j + Qdrant servers, DIP_JOB_MODE=queue, DIP_REDIS_URL
    python scripts/dmis.py poll [CONNECTOR ...]  # poll configured live sources (all when none given)
    python scripts/dmis.py connectors           # which live sources are configured
    python scripts/dmis.py schedule [--once] [--every-minutes 60] [--send-summaries]
                                              # inbox data/inbox/<market>/ + connectors + daily summaries
    python scripts/dmis.py daily-summary [--send]  # print (or send) every owner's daily summary
    python scripts/dmis.py worker [--once]       # process queued jobs (DIP_JOB_MODE=queue)
    python scripts/dmis.py backup [--out DIR]    # archive of the business DB, analytics, graph, vectors and lake
    python scripts/dmis.py restore ARCHIVE [--force]
    python scripts/dmis.py create-org NAME [--plan starter|professional|enterprise]
    python scripts/dmis.py create-user EMAIL NAME [--role admin|manager|product_manager|analyst|viewer|customer]
                                              [--employee "Employee Name"]   # prints an API token once
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]


def multi_worker_problems() -> list[str]:
    """What is not shared between API worker processes in the current configuration."""
    import os

    from dip.settings import get_settings
    st, out = get_settings(), []
    if not st.postgres_url:
        out.append("the business DB is SQLite (set DIP_POSTGRES_URL); concurrent writers will block")
    if not st.qdrant_url:
        out.append("Qdrant runs in embedded local mode, which one process at a time can open (set DIP_QDRANT_URL)")
    if not st.neo4j_uri:
        out.append("the embedded graph is not shared between processes (set DIP_NEO4J_URI)")
    if not st.redis_url:
        out.append("each worker keeps its own response cache (set DIP_REDIS_URL to share it)")
    if os.environ.get("DIP_JOB_MODE", "thread").strip().lower() != "queue":
        out.append("uploads run in API threads (set DIP_JOB_MODE=queue and run `dmis.py worker`)")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap")
    p = sub.add_parser("process")
    p.add_argument("file")
    p.add_argument("--market", required=True)
    p.add_argument("--snapshot-date")
    p.add_argument("--reviews")
    p.add_argument("--marketplace")
    pd_ = sub.add_parser("process-dir")
    pd_.add_argument("folder")
    pd_.add_argument("--market", required=True)
    pd_.add_argument("--force", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--allow-duplicate", action="store_true",
                   help="accept content already uploaded for another market/snapshot (requires --reason; audited)")
    p.add_argument("--reason", default=None, help="why a duplicate upload is intended")
    s = sub.add_parser("import-suppliers")
    s.add_argument("file")
    u = sub.add_parser("create-user")
    u.add_argument("email")
    u.add_argument("name")
    u.add_argument("--role", default="analyst", choices=["admin", "manager", "product_manager", "supplier_manager", "analyst", "viewer", "customer"])
    u.add_argument("--employee", default=None, help="link to an employee (product managers see that employee's categories)")
    pl = sub.add_parser("poll")
    pl.add_argument("names", nargs="*")
    sub.add_parser("connectors")
    sc = sub.add_parser("schedule")
    sc.add_argument("--once", action="store_true")
    sc.add_argument("--every-minutes", type=int, default=60)
    sc.add_argument("--send-summaries", action="store_true")
    ds = sub.add_parser("daily-summary")
    ds.add_argument("--send", action="store_true")
    wk = sub.add_parser("worker")
    wk.add_argument("--once", action="store_true")
    wk.add_argument("--poll-seconds", type=float, default=5)
    bk = sub.add_parser("backup")
    bk.add_argument("--out", default="backups")
    rs = sub.add_parser("restore")
    rs.add_argument("archive")
    rs.add_argument("--force", action="store_true")
    og = sub.add_parser("create-org")
    og.add_argument("name")
    og.add_argument("--plan", default=None)
    u.add_argument("--org", default=None, help="organization id (tenant) of the user")
    v = sub.add_parser("serve")
    v.add_argument("--port", type=int, default=8000)
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--workers", type=int, default=None, help="uvicorn worker processes (default: DIP_API_WORKERS or 1)")
    a = ap.parse_args(argv)
    if getattr(a, "market", None) is not None:
        from dip.storage.lake import InvalidMarketName, validate_market_name
        try:
            a.market = validate_market_name(a.market)
        except InvalidMarketName as exc:
            ap.error(str(exc))

    if a.cmd == "serve":
        import os

        import uvicorn
        workers = a.workers if a.workers is not None else int(os.environ.get("DIP_API_WORKERS") or 1)
        if workers > 1:
            for problem in multi_worker_problems():
                print(f"warning: --workers {workers}: {problem}", file=sys.stderr)
        # dip.api.asgi = the platform app + request ids, DIP_LOG_FORMAT=json and optional SENTRY_DSN
        uvicorn.run("dip.api.asgi:app", host=a.host, port=a.port, workers=max(1, workers))
        return 0
    if a.cmd == "create-user":
        from dip.auth import auth_enabled, create_user
        from dip.storage import business as b
        emp = None
        if a.employee:
            emp = b.stable_id("emp", a.employee)
            with b.session() as s:
                if s.get(b.Employee, emp) is None:
                    s.add(b.Employee(id=emp, name=a.employee))
        uid, token = create_user(a.email, a.name, a.role, employee_id=emp, org_id=a.org)
        print(f"user {a.email} ({a.role}) id={uid}\nAPI token (shown once): {token}")
        if not auth_enabled():
            print("note: auth is currently OFF (embedded mode). Set DIP_AUTH=on to enforce tokens.")
        return 0
    if a.cmd in ("poll", "connectors"):
        import os

        from dip.connectors import REGISTRY
        if a.cmd == "connectors":
            for c in REGISTRY.values():
                st = c.status()
                print(f"  {st.name:18} {'configured' if st.configured else 'missing ' + ', '.join(st.missing)}  -- {st.description}")
            return 0
        os.environ["DIP_EVENTS_SYNC"] = "1"   # process delivered datasets before exiting
        for name in a.names or list(REGISTRY):
            if name not in REGISTRY:
                print(f"unknown connector '{name}'")
                return 2
            print(json.dumps(REGISTRY[name].poll(), default=str))
        return 0
    if a.cmd == "schedule":
        import os
        import time as _t

        os.environ["DIP_EVENTS_SYNC"] = "1"
        from dip.logging_setup import configure_logging, init_sentry
        from dip.operations import run_once
        configure_logging()
        init_sentry()
        while True:
            print(json.dumps(run_once(send_summaries=a.send_summaries or None), default=str))
            if a.once:
                return 0
            _t.sleep(max(1, a.every_minutes) * 60)
    if a.cmd == "worker":
        from dip.logging_setup import configure_logging, init_sentry
        from dip.worker import work
        configure_logging()
        init_sentry()
        print(f"processed {work(a.poll_seconds, once=a.once)} job(s)")
        return 0
    if a.cmd == "backup":
        from dip.backup import backup
        print(f"backup written: {backup(a.out)}")
        return 0
    if a.cmd == "restore":
        from dip.backup import restore
        print(json.dumps(restore(a.archive, force=a.force), indent=1))
        return 0
    if a.cmd == "create-org":
        from dip.storage import business as b
        from dip.tenancy import plans_config
        with b.session() as s:
            o = b.Organization(name=a.name, plan=a.plan or plans_config()["default_plan"])
            s.add(o)
            s.flush()
            print(f"organization {a.name} id={o.id} plan={o.plan}")
        return 0
    if a.cmd == "daily-summary":
        from dip.operations import daily_summary, owned_markets, send_daily_summaries
        from dip.storage import business as b
        if a.send:
            print(f"sent {send_daily_summaries()} summaries")
            return 0
        with b.session() as s:
            ids = [e.id for e in s.query(b.Employee).all()]
        for eid in ids:
            if owned_markets(eid):
                print(daily_summary(eid)["text"], "\n")
        return 0
    from dip.pipeline import runner
    if a.cmd == "bootstrap":
        print(f"ownership links: {runner.import_ownership_csv()}")
        for r in runner.import_raw_folder():
            print(f"  {r['market']}: {r['products']} products, {r['segments']} segments")
    elif a.cmd == "process":
        from dmie.engine.ingestion import read_table
        rv = read_table(a.reviews) if a.reviews else None
        from dip.storage.business import DuplicateContent
        if a.allow_duplicate and not (a.reason or "").strip():
            ap.error("--allow-duplicate requires --reason")
        try:
            s = runner.process_dataset(Path(a.file), a.market, snapshot_date=a.snapshot_date, reviews=rv,
                                       source_name=Path(a.file).name, marketplace=a.marketplace, force=a.force,
                                       allow_duplicate=a.allow_duplicate, duplicate_reason=a.reason)
        except DuplicateContent as exc:
            print(f"rejected: {exc}", file=sys.stderr)
            return 3
        print(json.dumps({k: s[k] for k in ("ingestion", "discovery", "dedup", "opportunity", "horizons")}, indent=1, default=str, ensure_ascii=False))
    elif a.cmd == "process-dir":
        for r in runner.process_folder(Path(a.folder), a.market, force=a.force):
            print(json.dumps(r, default=str))
    elif a.cmd == "import-suppliers":
        from dmie.engine.ingestion import read_table
        from dip.pipeline.supplier import import_suppliers
        print(f"imported {import_suppliers(read_table(a.file), Path(a.file).name)} suppliers "
              "(re-process markets to refresh matches)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
