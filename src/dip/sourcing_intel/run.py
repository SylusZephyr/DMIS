"""A sourcing run: concept -> queries on every configured marketplace -> scored offers -> suppliers -> best pick.

Chinese platforms (1688, Taobao) are searched with the Chinese queries, international ones (Alibaba.com,
AliExpress, Made-in-China) with the English ones; 1688 also runs an image search with the concept's top
Amazon listing images when the platform supports it. Every offer is kept (``<data_dir>/sourcing/<run>.csv``)
with its score components and reasons; the run is a ``sourcing_runs`` row (concept, queries sent per platform,
counts, recommended offer, shortlist, cost, errors). A platform that fails does not stop the others.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone


from dip.acquire.base import AcquireError, BudgetExceeded, Ledger
from dip.settings import get_settings
from dip.sourcing_intel import score as sc
from dip.sourcing_intel.concept import Concept
from dip.sourcing_intel.platforms import Ali1688, Offer, config, make
from dip.sourcing_intel.platforms import status as platform_status
from dip.storage import business as b

ZH_PLATFORMS = {"1688", "taobao"}


def run_concept(c: Concept, *, platforms: list[str] | None = None, by: str | None = None, project_id: str | None = None,
                ledger: Ledger | None = None, http_kw: dict | None = None, image_search: bool = True) -> dict:
    rc = config()["run"]
    ledger = ledger or Ledger(budget_usd=float(rc["budget_usd_per_run"]))
    configured = [p["name"] for p in platform_status() if p["configured"] and (platforms is None or p["name"] in platforms)]
    queries: dict[str, list[str]] = {}
    with b.session() as s:
        row = b.SourcingRun(market_name=c.market, concept=c.to_dict(), by=by, project_id=project_id, status="running")
        s.add(row)
        s.flush()
        run_id = row.id
    offers: list[Offer] = []
    notes: list[str] = []
    if not configured:
        notes.append("no marketplace configured: set the credentials listed in config/platform/sourcing_intel.yaml")
    for name in configured:
        plat = make(name, ledger, **(http_kw or {}))
        qs = (c.terms_zh or c.queries_en) if name in ZH_PLATFORMS else (c.queries_en or c.terms_zh)
        queries[name] = list(qs)
        got: list[Offer] = []
        try:
            for q in qs:
                for page in range(1, int(rc["pages_per_query"]) + 1):
                    batch = plat.search(q, page)
                    got += batch
                    if not batch or len(got) >= int(rc["max_offers_per_platform"]):
                        break
                if len(got) >= int(rc["max_offers_per_platform"]):
                    break
            if image_search and isinstance(plat, Ali1688) and c.image_urls:
                queries[name].append(f"image:{c.image_urls[0]}")
                got += plat.image_search(c.image_urls[0])
        except BudgetExceeded as exc:
            notes.append(str(exc))
        except AcquireError as exc:
            notes.append(f"{name}: {exc}")
        offers += got[: int(rc["max_offers_per_platform"])]
    # the same offer seen by several queries once
    uniq: dict[tuple[str, str], Offer] = {}
    for o in offers:
        uniq.setdefault((o.platform, o.offer_id), o)
    scored = [sc.score_offer(c, o) for o in uniq.values()]
    sc.pareto(scored)
    top = sc.best(scored)
    sups = sc.suppliers(scored)
    rows = [r.to_dict() for r in sorted(scored, key=lambda r: (not r.match, -(r.score or 0)))]
    path = None
    if rows:
        d = get_settings().data_dir / "sourcing"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{run_id}.json"
        # plain JSON, not a DataFrame: missing fields stay null (not NaN) and ids keep their type
        path.write_text(json.dumps(rows, ensure_ascii=False, default=str), encoding="utf-8")
    result = {"offers": len(scored), "matching": sum(r.match for r in scored), "pareto": sum(r.pareto for r in scored),
              "suppliers": len(sups), "platforms": {p: sum(1 for o in uniq.values() if o.platform == p) for p in configured},
              "best": top.to_dict() if top else None, "shortlist": sups[: int(rc["shortlist"])], "notes": notes,
              "offers_path": str(path) if path else None, "fx_rates_as_of": config()["fx"]["rates_as_of"]}
    status = "done" if scored and not notes else ("partial" if scored else "failed")
    with b.session() as s:
        r = s.get(b.SourcingRun, run_id)
        r.queries, r.result, r.ledger, r.status = queries, result, ledger.to_dict(), status
        r.error = None if scored else "; ".join(notes) or "no offers found"
        r.finished_at = datetime.now(timezone.utc)
    from dip import audit
    audit.record("sourcing.run", None, "sourcing_runs", run_id, {"market": c.market, "offers": len(scored), "status": status})
    return {"id": run_id, "status": status, "concept": c.to_dict(), "queries": queries, **result, "ledger": ledger.to_dict()}


def offers_of(run_id: str) -> list[dict]:
    p = get_settings().data_dir / "sourcing" / f"{run_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def runs(market: str | None = None, limit: int = 20) -> list[dict]:
    with b.session() as s:
        q = s.query(b.SourcingRun)
        if market:
            q = q.filter(b.SourcingRun.market_name == market)
        return [b.row_dict(r) for r in q.order_by(b.SourcingRun.started_at.desc()).limit(limit)]


# ---------------------------------------------------------------- reaching out
def rfq(concept: dict, offer: dict) -> dict:
    """An inquiry (EN + ZH) from computed facts: spec, quantity, target price, certifications, questions."""
    qty = concept.get("first_order_qty")
    target = concept.get("max_fob")
    musts = "; ".join(f"{m['attribute']}: {m['value']}" for m in concept.get("must_have") or []) or "as in your listing"
    feats = ", ".join(concept.get("features") or [])
    certs = ", ".join(concept.get("required_certs") or []) or "-"
    ref = offer.get("url") or offer.get("title") or offer.get("offer_id")
    en = (f"Hello {offer.get('supplier') or ''},\n\n"
          f"We are sourcing: {concept.get('label')} (your listing: {ref}).\n"
          f"Required: {musts}." + (f" Differentiating features we want: {feats}." if feats else "") + "\n"
          f"First order: {qty} units, then repeat orders. "
          + (f"Our target price is up to ${target:.2f} per unit FOB. " if target else "")
          + f"Please confirm: price tiers for {qty} and {qty * 3 if qty else 'larger'} units, MOQ, production lead time, "
          f"sample cost and lead time, packaging (retail-ready for Amazon FBA with FNSKU labels), "
          f"and certificates ({certs}) with copies.\n\nThank you.")
    zh = (f"您好 {offer.get('supplier') or ''}：\n\n"
          f"我们正在采购：{concept.get('label')}（参考您的商品：{ref}）。\n"
          f"要求：{musts}。" + (f"希望具备的特色：{feats}。" if feats else "") + "\n"
          f"首单数量：{qty} 件，之后持续返单。"
          + (f"目标单价不超过 {target:.2f} 美元（FOB）。" if target else "")
          + f"请确认：{qty} 件及 {qty * 3 if qty else '更大'} 件的阶梯价格、起订量、生产周期、样品费用与样品周期、"
          f"包装（可直接用于亚马逊 FBA，贴 FNSKU 标签），以及相关证书（{certs}）并提供副本。\n\n谢谢！")
    return {"en": en, "zh": zh}


def reach_out(run_id: str, offer_id: str, platform: str, by: str | None = None, project_id: str | None = None) -> dict:
    """Register the offer's supplier (if new) and record an inquiry with the RFQ text."""
    from dip import sourcing

    with b.session() as s:
        run = s.get(b.SourcingRun, run_id)
        if run is None:
            raise KeyError(run_id)
        concept = dict(run.concept or {})
        market = run.market_name
    offer = next((o for o in offers_of(run_id) if str(o["offer_id"]) == str(offer_id) and o["platform"] == platform), None)
    if offer is None:
        raise KeyError(f"offer {platform}:{offer_id} not in run {run_id}")
    msg = rfq(concept, offer)
    key = sc.supplier_key(offer.get("supplier"))
    with b.session() as s:
        sup = None
        if key:
            for x in s.query(b.Supplier).filter(b.Supplier.source.like("sourcing:%")).all():
                if sc.supplier_key(x.name) == key:
                    sup = x
                    break
        if sup is None:
            sup = b.Supplier(id=b.new_id(), name=offer.get("supplier") or f"{platform} seller {offer.get('supplier_id')}",
                             country="China", city=offer.get("location"), website=offer.get("url"),
                             business_type="manufacturer" if offer.get("verified") else None,
                             certifications=", ".join(offer.get("certifications") or []) or None,
                             product_categories=concept.get("label"), source=f"sourcing:{platform}")
            s.add(sup)
            s.flush()
        sid, sname = sup.id, sup.name
    inter = sourcing.add_interaction(sid, {"kind": "inquiry", "product": concept.get("label"), "market_name": market,
                                           "project_id": project_id, "unit_price": offer.get("price_usd"), "currency": "USD",
                                           "moq": offer.get("moq"), "note": msg["en"]})
    return {"supplier_id": sid, "supplier": sname, "interaction": inter, "rfq": msg, "offer": offer}
