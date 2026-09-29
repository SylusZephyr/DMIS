"""Operations layer: notifications, daily intelligence summaries and the scheduler.

* ``notify``          internal message to an employee (+ e-mail when SMTP is configured
                      and the employee has an address)
* ``daily_summary``   what changed in an employee's markets in the last N hours, written as
                      sentences ("3 new opportunities in micromotor", "Luxor reduced prices 12%",
                      "New OEM supplier in China: ...") plus projects waiting for them
* ``run_once``        one scheduler tick: process new files dropped into data/inbox/<market>/,
                      poll configured connectors, send the daily summaries once a day
Settings: config/platform/operations.yaml; SMTP via DIP_SMTP_* environment variables.
"""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from dip.events import ops_config
from dip.settings import PROJECT_ROOT
from dip.storage import business as b

log = logging.getLogger("dip.operations")


# ------------------------------------------------------------------ notifications
def smtp_configured() -> bool:
    return bool(os.environ.get("DIP_SMTP_HOST"))


def send_email(to: str, subject: str, body: str) -> bool:
    """Send one e-mail; False (logged) when SMTP is not configured or fails."""
    host = os.environ.get("DIP_SMTP_HOST")
    if not host or not to:
        return False
    msg = EmailMessage()
    msg["From"] = os.environ.get("DIP_SMTP_FROM", "dmis@localhost")
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.environ.get("DIP_SMTP_PORT", "587")), timeout=20) as s:
            if os.environ.get("DIP_SMTP_USER"):
                s.starttls()
                s.login(os.environ["DIP_SMTP_USER"], os.environ.get("DIP_SMTP_PASSWORD", ""))
            s.send_message(msg)
        return True
    except Exception:
        log.exception("e-mail to %s failed", to)
        return False


def notify(employee_id: str, subject: str, body: str, kind: str = "message", email: bool = True) -> str:
    with b.session() as s:
        e = s.get(b.Employee, employee_id)
        address = e.email if e else None
    sent = bool(email and address and send_email(address, subject, body))
    with b.session() as s:
        m = b.Message(to_employee_id=employee_id, subject=subject[:512], body=body, kind=kind, emailed=sent)
        s.add(m)
        s.flush()
        return m.id


def deliver_alert(ev: b.Event, employee_ids: list[str]) -> None:
    """Called by the alert router: important events also become messages (+ e-mail)."""
    if ev.severity not in ops_config()["notifications"]["email_severities"]:
        return
    body = f"{describe(ev)}\n\nMarket: {ev.market_name or 'all'} · source: {ev.source} · {ev.created_at:%Y-%m-%d %H:%M}"
    for e in employee_ids:
        notify(e, f"[DMIS] {describe(ev)}"[:200], body, kind="alert")


# ------------------------------------------------------------------ daily summary
def describe(ev: b.Event) -> str:
    """One sentence per event, with its number."""
    p = ev.payload or {}
    k, m = ev.kind, ev.market_name
    pct = lambda v: f"{abs(v):.0%}" if isinstance(v, (int, float)) else "?"  # noqa: E731
    if k == "competitor.price_change":
        return f"{ev.subject} {'reduced' if p.get('change', 0) < 0 else 'raised'} prices {pct(p.get('change'))} in {m}"
    if k == "competitor.new_brand":
        return f"New competitor {ev.subject} in {m} ({p.get('share', 0):.1%} share)"
    if k == "competitor.share_change":
        return f"{ev.subject} share {p.get('change', 0) * 100:+.1f} pts in {m}"
    if k == "competitor.position_change":
        return f"{ev.subject}: {p.get('before')} → {p.get('after')} in {m}"
    if k == "opportunity.new_segment":
        return f"New opportunity in {m}: '{ev.subject}' ({p.get('opportunity_score', 0):.0f}/100)"
    if k == "opportunity.change":
        return f"Opportunity in '{ev.subject}' ({m}) {p.get('before')} → {p.get('after')}"
    if k == "market.size_change":
        return f"{m} {'revenue' if p.get('metric') == 'monthly_revenue' else 'units'} {p.get('change', 0):+.0%} since the last snapshot"
    if k == "trend.change":
        return f"{m} trend {p.get('before')} → {p.get('after')}"
    if k == "product.new_launch":
        return f"New launch in {m}: {str(ev.subject)[:80]}"
    if k == "supplier.new":
        return ev.subject or "New supplier"
    if k == "news.item":
        return f"News ({m or 'industry'}): {str(ev.subject)[:100]}"
    return ev.subject or k


def owned_markets(employee_id: str) -> list[str]:
    with b.session() as s:
        rows = (s.query(b.Category.market_name).join(b.Ownership, b.Ownership.category_id == b.Category.id)
                .filter(b.Ownership.employee_id == employee_id, b.Category.market_name.isnot(None)).distinct().all())
    return sorted(r[0] for r in rows)


def daily_summary(employee_id: str, hours: int | None = None) -> dict | None:
    cfg = ops_config()["summary"]
    hours = hours or cfg["window_hours"]
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with b.session() as s:
        e = s.get(b.Employee, employee_id)
        if e is None:
            return None
        name = e.name
    markets = owned_markets(employee_id)
    cutoff = since.replace(tzinfo=None) if _naive() else since
    with b.session() as s:
        evs = (s.query(b.Event).filter(b.Event.created_at >= cutoff, b.Event.severity != "info",
                                       (b.Event.market_name.in_(markets)) | (b.Event.market_name.is_(None)))
               .order_by(b.Event.created_at.desc()).all())
        projects = s.query(b.Project).filter(b.Project.owner_employee_id == employee_id,
                                             b.Project.status.in_(("active", "pending_approval", "approved"))).all()
        proj = [{"id": p.id, "title": p.title, "stage": p.stage, "status": p.status} for p in projects]
        rows = [(ev.kind, ev.market_name, describe(ev)) for ev in evs]
    headline: list[str] = []
    for mk in markets:
        opp = sum(1 for k, m, _ in rows if m == mk and k in ("opportunity.new_segment", "opportunity.change"))
        if opp:
            headline.append(f"{opp} new or changed opportunit{'y' if opp == 1 else 'ies'} in {mk}.")
        launches = sum(1 for k, m, _ in rows if m == mk and k == "product.new_launch")
        if launches:
            headline.append(f"{launches} new product launch{'es' if launches > 1 else ''} in {mk}.")
    items = [t for k, _, t in rows if k not in ("product.new_launch",)][: cfg["max_items"]]
    sup = sum(1 for k, _, _ in rows if k == "supplier.new")
    if sup:
        headline.append(f"{sup} new supplier{'s' if sup > 1 else ''} relevant to your markets.")
    pending = [p for p in proj if p["status"] == "pending_approval"]
    if pending:
        headline.append(f"{len(pending)} of your project(s) waiting for approval.")
    text = "\n".join([f"Good morning {name} — last {hours} h in your markets ({', '.join(markets) or 'none assigned'}):", ""]
                     + [f"• {h}" for h in headline] + ([""] if headline and items else [])
                     + [f"- {i}" for i in items] + ([] if headline or items else ["No changes."])
                     + (["", "Your projects:"] + [f"- {p['title']} [{p['stage']}, {p['status']}]" for p in proj] if proj else []))
    return {"employee_id": employee_id, "name": name, "hours": hours, "markets": markets, "headline": headline,
            "items": items, "projects": proj, "events": len(rows), "text": text}


def _naive() -> bool:
    return b.engine().dialect.name == "sqlite"


def send_daily_summaries(only_with_changes: bool = True) -> int:
    """One summary message (+ e-mail) per employee who owns at least one market."""
    with b.session() as s:
        emps = [e.id for e in s.query(b.Employee).all()]
    n = 0
    for eid in emps:
        if not owned_markets(eid):
            continue
        sm = daily_summary(eid)
        if sm is None or (only_with_changes and not sm["events"] and not sm["projects"]):
            continue
        notify(eid, f"[DMIS] Daily intelligence summary — {datetime.now():%Y-%m-%d}", sm["text"], kind="summary")
        n += 1
    return n


# ------------------------------------------------------------------ scheduler
EXPORT_EXT = {".xlsx", ".xls", ".csv", ".tsv", ".json", ".jsonl"}


def inbox_dir() -> Path:
    d = Path(os.environ.get("DIP_INBOX_DIR") or PROJECT_ROOT / ops_config()["scheduler"]["inbox_dir"])
    return d


def process_inbox() -> list[dict]:
    """Files in <inbox>/<market>/ not yet uploaded for that market (by content hash) are processed
    oldest first; a file already processed is never re-run (the lake keeps it)."""
    from dip.pipeline import runner
    from dip.storage import lake

    root = inbox_dir()
    if not root.exists():
        return []
    out = []
    for mdir in sorted(p for p in root.iterdir() if p.is_dir()):
        market = mdir.name
        if not lake.is_valid_market_name(market):
            out.append({"market": market, "status": "rejected", "error": str(_name_error(market))})
            continue
        with b.session() as s:
            seen = {d.content_hash for d in s.query(b.Dataset).filter(b.Dataset.market_name == market).all()}
        files = [f for f in mdir.iterdir() if f.is_file() and f.suffix.lower() in EXPORT_EXT]
        files.sort(key=lambda f: (runner.snapshot_date_from_name(f.name) or "9999", f.stat().st_mtime, f.name))
        for f in files:
            digest = lake.content_hash(f)
            if digest in seen:
                continue
            snap = runner.snapshot_date_from_name(f.name)
            conflicts = b.duplicate_conflicts(digest, market, snap)
            if conflicts:   # same content as another market/period: never processed silently as new history
                out.append({"market": market, "file": f.name, "status": "duplicate",
                            "error": f"identical content already uploaded for market '{conflicts[0]['market']}', "
                                     f"snapshot {conflicts[0]['snapshot_date'] or '(none)'}"})
                continue
            try:
                s = runner.process_dataset(f, market, snapshot_date=runner.snapshot_date_from_name(f.name), source_name=f.name)
                out.append({"market": market, "file": f.name, "status": "processed", "change_events": s.get("change_events")})
            except Exception as exc:  # recorded on the job; keep going with the next file
                out.append({"market": market, "file": f.name, "status": "failed", "error": str(exc)[:300]})
    return out


def _name_error(market: str) -> Exception:
    from dip.storage import lake

    try:
        lake.validate_market_name(market)
    except lake.InvalidMarketName as exc:
        return exc
    return ValueError(market)


def _summary_sent_today() -> bool:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with b.session() as s:
        q = s.query(b.AuditLog).filter(b.AuditLog.action == "scheduler.daily_summary")
        q = q.filter(b.AuditLog.at >= (start.replace(tzinfo=None) if _naive() else start))
        return q.first() is not None


def run_once(send_summaries: bool | None = None) -> dict:
    """One scheduler tick. Summaries go out once per day after the configured hour."""
    from dip import audit
    from dip.connectors import REGISTRY

    cfg = ops_config()["scheduler"]
    report: dict = {"at": datetime.now(timezone.utc).isoformat(), "inbox": process_inbox(), "connectors": []}
    if cfg.get("poll_connectors", True):
        for c in REGISTRY.values():
            if c.status().configured:
                try:
                    report["connectors"].append(c.poll())
                except Exception as exc:
                    report["connectors"].append({"connector": c.name, "status": "failed", "error": str(exc)[:300]})
    # live Amazon snapshots (src/dip/acquire): each market on the configured cadence, when a provider is set up
    from dip import acquire
    report["acquisition"] = []
    if acquire.status()["ready"]:
        for m in acquire.run.due_markets():
            try:
                r = acquire.run_market(m, by="scheduler")
                report["acquisition"].append({"market": m, "status": r["status"], "listings": r.get("listings")})
            except Exception as exc:  # noqa: BLE001 -- one market's failure never stops the tick
                report["acquisition"].append({"market": m, "status": "failed", "error": str(exc)[:300]})
    # competitor watchlist: watched listings re-observed on their own (shorter) cadence
    from dip import watch
    report["watchlist"] = None
    if acquire.status()["active"].get("amazon_detail") and watch.due():
        try:
            r = watch.refresh(by="scheduler")
            report["watchlist"] = {"status": r["status"], "listings": r["listings"], "events": r["events"]}
        except Exception as exc:  # noqa: BLE001 -- never stops the tick
            report["watchlist"] = {"status": "failed", "error": str(exc)[:300]}
    due = datetime.now().hour >= int(cfg.get("summary_hour", 7)) and not _summary_sent_today()
    if send_summaries or (send_summaries is None and due):
        report["summaries_sent"] = send_daily_summaries()
        audit.record("scheduler.daily_summary", None, "summaries", None, {"sent": report["summaries_sent"]})
    audit.record("scheduler.tick", None, "scheduler", None,
                 {"inbox": len(report["inbox"]), "connectors": len(report["connectors"])})
    return report
