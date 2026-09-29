"""Real business simulation: one new market dataset through the whole platform, via the API.

    python scripts/business_simulation.py --file EXPORT.xlsx --market NAME [--reviews R.csv]
        [--suppliers S.csv] [--context-raw] [--out docs/BUSINESS_SIMULATION_REPORT.md]

Runs on a fresh, temporary platform (nothing in data/ is touched) and exercises
the ten steps a company would: upload -> clean -> identify dental products ->
cluster -> market size -> opportunities -> recommend products -> find suppliers
-> 3D views -> analyst questions. Every number in the report comes from an API
response; missing evidence (no suppliers, no reviews, one snapshot) is reported
as missing. --context-raw also processes every other data/raw export first so
the analyst and trends have several markets to compare.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True)
    ap.add_argument("--market", required=True)
    ap.add_argument("--reviews")
    ap.add_argument("--suppliers")
    ap.add_argument("--context-raw", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "docs" / "BUSINESS_SIMULATION_REPORT.md"))
    a = ap.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="dmis_sim_"))
    os.environ.update(DIP_DATA_DIR=str(tmp / "platform"), DIP_LAKE_DIR=str(tmp / "lake"), DIP_AUTH="off")
    for v in ("DIP_POSTGRES_URL", "DIP_NEO4J_URI", "DIP_QDRANT_URL"):
        os.environ.pop(v, None)
    from fastapi.testclient import TestClient

    from dip.api.app import app
    from dip.pipeline import runner

    c = TestClient(app)
    api = lambda m, p, **kw: _ok(getattr(c, m)(f"/api/v2{p}", **kw))  # noqa: E731
    out: list[str] = []
    w = out.append
    t_all = time.perf_counter()
    w(f"# Business simulation — `{a.market}`\n")
    w(f"Input: `{Path(a.file).name}` ({Path(a.file).stat().st_size / 1024:,.0f} KB)"
      + (f", reviews `{Path(a.reviews).name}`" if a.reviews else ", no review file")
      + (f", suppliers `{Path(a.suppliers).name}`" if a.suppliers else ", no supplier list")
      + ". Fresh temporary platform; every figure below is an API response.\n")

    # context: ownership + the other real markets
    runner.import_ownership_csv()
    ctx = []
    if a.context_raw:
        for d in sorted((ROOT / "data" / "raw").iterdir()):
            files = sorted(d.glob("*_sellersprite.xlsx")) if d.is_dir() else []
            if files and d.name != a.market and files[0].resolve() != Path(a.file).resolve():
                runner.process_dataset(files[0], d.name, source_name=files[0].name)
                ctx.append(d.name)
        w(f"Context markets processed first: {', '.join(ctx) or 'none'}.\n")
    if a.suppliers:
        with open(a.suppliers, "rb") as fh:
            api("post", "/suppliers/import", files={"file": (Path(a.suppliers).name, fh.read(), "text/csv")})

    # 1-2 upload + clean
    t = time.perf_counter()
    files = {"file": (Path(a.file).name, Path(a.file).read_bytes(), "application/octet-stream")}
    if a.reviews:
        files["reviews"] = (Path(a.reviews).name, Path(a.reviews).read_bytes(), "text/csv")
    job_id = api("post", "/datasets", files=files, data={"market": a.market})["job_id"]
    for _ in range(1200):
        job = api("get", f"/jobs/{job_id}")
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    if job["status"] != "done":
        w(f"\n**Processing failed:** {job.get('error')}\n")
        Path(a.out).write_text("\n".join(out), encoding="utf-8")
        return 1
    rep = job["report"]
    s = api("get", f"/markets/{a.market}")
    w("\n## 1. Upload\n")
    w(f"Job `{job_id}` finished in {time.perf_counter() - t:.1f} s through {len(job['stages'])} stages: "
      + " → ".join(f"{x['name']} ({x.get('seconds', 0):.1f}s)" for x in job["stages"]) + ".\n")
    w("\n## 2. Clean\n")
    w(f"- {rep['raw_rows']} raw rows → {rep['accepted']} accepted, {rep['rejected']} rejected"
      + (f" ({', '.join(f'{k}: {v}' for k, v in rep['rejection_reasons'].items())})" if rep["rejection_reasons"] else "") + ".")
    w(f"- Schema detected by the `{rep['adapter']}` adapter; {len(rep['column_mapping'])} columns mapped"
      + (f", unmapped: {', '.join(rep['unmapped_columns'][:8])}" if rep.get("unmapped_columns") else "") + ".")
    q = s["quality"]
    w(f"- Quality: dataset confidence {q['dataset_confidence']:.0f}/100, {q['usable_for_market']} of {q['records']} records usable; "
      f"issues {json.dumps(q['issue_counts'], ensure_ascii=False)}.")
    for warn in rep.get("warnings", []):
        w(f"- Warning: {warn}")

    # 3 identify dental products
    r = s["relevance"]
    w("\n## 3. Identify dental products\n")
    w(f"- {r['relevant']} relevant, {r['uncertain']} uncertain (review queue), {r['irrelevant']} irrelevant — each with a per-signal explanation.")
    ex = api("get", f"/markets/{a.market}/records?status=excluded&limit=5")
    for e in ex[:5]:
        w(f"  - excluded `{e.get('id')}`: {e.get('excluded_reason')} — {str(e.get('title'))[:80]}")

    # 4 cluster
    tree = api("get", f"/markets/{a.market}/hierarchy?listings=false")
    d = s["discovery"]
    models = [m for f in tree["children"] for sg in f["children"] for m in sg["children"]]
    basis = {}
    for m in models:
        basis[m.get("basis") or "?"] = basis.get(m.get("basis") or "?", 0) + m["products"]
    w("\n## 4. Cluster products\n")
    w(f"- {s['dedup']['listings']} listings → **{s['dedup']['products']} products** ({s['dedup']['multi_listing_products']} with several listings).")
    w(f"- Hierarchy: {d['families']} families → {d['segments']} segments → {len(models)} models → {tree['products']} variants. "
      f"Products by model basis: {json.dumps(basis)} (none = no shared model found).")
    for f in tree["children"][:3]:
        sg = f["children"][0]
        m = sg["children"][0]
        w(f"  - {f['label']} → {sg['label']} → {m['label']} → {m['children'][0]['label']}")

    # 5 market size
    br = api("get", f"/markets/{a.market}/brief")
    conf = api("get", f"/markets/{a.market}/confidence?limit=3")
    tr = api("get", f"/markets/{a.market}/trends")["market"]
    ms = br["market_size"]
    w("\n## 5. Market size\n")
    w(f"- Observed **{_m(ms['monthly_revenue'])}/month** ({_m(ms['annual_revenue'])}/year); sales known for "
      f"{(ms['sales_coverage'] or 0):.0%} of products, so this is a lower bound.")
    w(f"- Market confidence {conf['market']['confidence_score']}/100 ({conf['market']['confidence_level']}); "
      f"{conf['market']['high_confidence_products']} high- and {conf['market']['low_confidence_products']} low-confidence products.")
    top = conf["products"][0]
    w(f"- Example — {str(top['title'])[:70]}: {top['confidence_score']}% because "
      + "; ".join(("✓ " if x["ok"] else "✗ ") + x["detail"] for x in top["confidence_reasons"]) + ".")
    w(f"- Trend: **{tr['trend']}** ({tr['confidence']}% confidence) — {'; '.join(tr['evidence']) or 'no signal'}; "
      f"expected 12-month growth: {_p(tr['expected_growth_12m']) if tr['expected_growth_12m'] is not None else tr.get('expected_growth_basis')}.")

    # 6 opportunities
    segs = api("get", f"/markets/{a.market}/segments")
    # the v1 competitor / launch-evaluation / analyst endpoints are retired (410); their modules are called directly
    from dip.api.util import clean
    from dip.intelligence.analyst import ask as analyst_ask
    from dip.intelligence.launch import LaunchIdea, evaluate
    from dip.storage import lake
    comp = clean(lake.read_curated("competitors", a.market, order="share DESC", limit=5))
    names = [m["name"] for m in api("get", "/markets")]
    w("\n## 6. Find opportunities\n")
    for x in segs[:5]:
        w(f"- **{x['segment_label']}** — opportunity {x['opportunity_score']:.0f}/100 ({x['opportunity_level']}), "
          f"{_m(x.get('monthly_revenue'))}/month, {x['concentration']}, trend {x.get('trend_label')}; {x['opportunity_drivers']}")
    w("\nCompetitors:")
    for x in comp:
        w(f"- {x['brand']}: {x['position']}, {x['share']:.0%} share by {x['share_basis']}, price index {x['price_index']}"
          + (f"; weakness: {x['weaknesses'][0]} → {x['opportunities'][0]}" if x["weaknesses"] else ""))

    # 7 recommend products
    w("\n## 7. Recommend products\n")
    w(f"- Suggested development: **{br['suggested_development']}**.")
    best = segs[0]
    idea = {"title": f"{best['segment_label']} {best.get('dominant_specs') or ''}".strip(), "market": a.market,
            "price": round(float(best.get("price_median") or 50.0), 2)}
    ev = clean(evaluate(LaunchIdea(**idea)))
    w(f"- Launch simulation for “{idea['title']}” at ${idea['price']}: attractiveness **{ev['market_attractiveness']}/100 "
      f"({ev['verdict']})**, positioning {ev['expected_positioning']}, main risk: "
      + (f"{ev['main_risk']['risk']} ({ev['main_risk']['evidence']})" if ev["main_risk"] else "none") + ".")
    econ = ev["economics"]
    w(f"- Unit economics: margin {_m(econ['unit_margin'], 2)} per unit ({econ['unit_cost_basis'] or 'no cost data'}; fulfilment {econ['fulfilment_basis']}).")
    for st in ev["recommended_strategy"]:
        w(f"  - Strategy: {st}")
    emp = api("get", "/employees")
    owners = [e for e in emp if e["markets"]]
    if owners:
        fo = api("get", f"/employees/{owners[0]['id']}/focus")
        w(f"- Focus for {fo['name']}: " + "; ".join(f"[{x['priority']}] {x['action']}" for x in fo["recommended_actions"][:4]) + ".")

    # 8 suppliers
    w("\n## 8. Find suppliers\n")
    sup = clean(analyst_ask(f"Who can manufacture {best['segment_label']}?", names))
    w(sup["answer"].replace("\n", "\n\n") if sup["items"] else
      "- No supplier list is loaded, so no manufacturer is named (suppliers are never generated). "
      "Import one on the Suppliers page, with `dmis.py import-suppliers FILE`, or through the supplier_feed connector; "
      "matches and the supplier-availability score then fill in on the next run.")

    # 9 3D
    gal = api("get", f"/markets/{a.market}/galaxy-v3")
    uni = api("get", "/universe")
    geo = api("get", "/geo")
    graph = api("get", f"/graph/explore?node=category:{a.market}&depth=2&limit=2000")
    w("\n## 9. Display in 3D\n")
    placed = sum(1 for p in gal["products"] if p.get("gx") is not None)
    w(f"- Product Galaxy: {placed} products positioned in 3D, "
      f"{sum(1 for e in gal['edges'] if e.get('type') == 'similar')} similarity links.")
    w(f"- Market Universe: {sum(len(b['children']) for b in uni['children'])} markets in {len(uni['children'])} industry branches; "
      f"Global Earth: {len(geo['countries'])} countries from real marketplace/supplier fields.")
    kinds = {}
    for n in graph["nodes"]:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    w(f"- Knowledge graph around the category: {len(graph['nodes'])} nodes ({json.dumps(kinds)}), {len(graph['edges'])} edges.")

    # 10 analyst
    w("\n## 10. Answer analyst questions\n")
    for qu in ["What products should we develop?", "Which markets are growing?", "Who are our competitors?",
               "Who can manufacture this product?", f"What happens if we launch {best['segment_label']} at ${idea['price']}?",
               "What should each employee focus on?" if not owners else f"What should {owners[0]['name']} focus on?"]:
        res = clean(analyst_ask(qu, names))
        w(f"**Q: {qu}** — intent `{res['intent']}`, scope {res['scope']['basis']}\n")
        w("> " + res["answer"].replace("\n", "\n> ") + "\n")

    w(f"\n---\nTotal time {time.perf_counter() - t_all:.1f} s. Platform data: `{tmp}` (temporary).")
    Path(a.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"report written to {a.out}")
    return 0


def _ok(r):
    if r.status_code >= 400:
        raise SystemExit(f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text[:300]}")
    return r.json()


def _m(v, digits=0):
    return "n/a" if v is None else f"${v:,.{digits}f}"


def _p(v):
    return "n/a" if v is None else f"{v:+.0%}"


if __name__ == "__main__":
    raise SystemExit(main())
