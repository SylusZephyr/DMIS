"""Pilot tooling (Master Prompt 4). Measurement only -- nothing here changes pipeline results.

    python scripts/pilot.py inventory   [--out docs/pilot/DATA_INVENTORY.md]
    python scripts/pilot.py sample MARKET [--n 50] [--excluded 20] [--seed 7] [--name NAME]
    python scripts/pilot.py samples     [--market M]
    python scripts/pilot.py export-sample SAMPLE_ID --out FILE.csv          # offline labelling sheet
    python scripts/pilot.py import-labels SAMPLE_ID FILE.csv --labeller EMAIL
    python scripts/pilot.py accuracy    [--market M] [--out docs/pilot/ACCURACY_REPORT.md]
    python scripts/pilot.py sensitivity [--market M] [--out docs/pilot/COVERAGE_SENSITIVITY.md]
    python scripts/pilot.py backtest    [--market M] [--out docs/pilot/BACKTEST_REPORT.md]
    python scripts/pilot.py stability MARKET                                 # product-ID stability of the last upload

Point DIP_DATA_DIR / DIP_LAKE_DIR at the platform data to measure (defaults: data/platform, data/lake).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _write(text: str, out: str | None) -> None:
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
        print(f"written: {out}")
    else:
        print(text)


def _markets(one: str | None) -> list[str]:
    from dip.storage import business as b

    if one:
        return [one]
    with b.session() as s:
        return sorted(m.name for m in s.query(b.Market).all())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventory").add_argument("--out", default=None)
    sp = sub.add_parser("sample")
    sp.add_argument("market")
    sp.add_argument("--n", type=int, default=50)
    sp.add_argument("--excluded", type=int, default=20)
    sp.add_argument("--seed", type=int, default=7)
    sp.add_argument("--name", default=None)
    sub.add_parser("samples").add_argument("--market", default=None)
    ex = sub.add_parser("export-sample")
    ex.add_argument("sample_id")
    ex.add_argument("--out", required=True)
    im = sub.add_parser("import-labels")
    im.add_argument("sample_id")
    im.add_argument("file")
    im.add_argument("--labeller", required=True)
    for name in ("accuracy", "sensitivity", "backtest"):
        p = sub.add_parser(name)
        p.add_argument("--market", default=None)
        p.add_argument("--out", default=None)
    sub.add_parser("stability").add_argument("market")
    a = ap.parse_args()

    if a.cmd == "inventory":
        from dip.pilot.inventory import inventory, to_markdown
        _write(to_markdown(inventory()), a.out)
    elif a.cmd == "sample":
        from dip.pilot.labels import draw_sample
        print(json.dumps(draw_sample(a.market, a.n, a.excluded, a.seed, a.name, "cli"), indent=1, default=str))
    elif a.cmd == "samples":
        from dip.pilot.labels import list_samples
        for s in list_samples(a.market):
            print(f"{s['id']}  {s['market_name']:<20} {s['name']:<30} labelled {s['labelled_items']}/{s['items']}")
    elif a.cmd == "export-sample":
        from dip.pilot.labels import export_sheet
        export_sheet(a.sample_id).to_csv(a.out, index=False)
        print(f"written: {a.out}")
    elif a.cmd == "import-labels":
        import pandas as pd

        from dip.pilot.labels import import_sheet
        print(f"labels saved: {import_sheet(a.sample_id, pd.read_csv(a.file, dtype=str), a.labeller)}")
    elif a.cmd == "accuracy":
        from dip.pilot.metrics import report
        _write(report(a.market), a.out)
    elif a.cmd == "sensitivity":
        from dip.pilot.sensitivity import analyse, to_markdown
        _write(to_markdown([analyse(m) for m in _markets(a.market)]), a.out)
    elif a.cmd == "backtest":
        from dip.pilot.backtest import run, to_markdown
        _write(to_markdown([run(m) for m in _markets(a.market)]), a.out)
    elif a.cmd == "stability":
        from dip.storage import business as b
        with b.session() as s:
            m = s.get(b.Market, a.market)
            print(json.dumps((m.summary or {}).get("product_ids") if m else None, indent=1))


if __name__ == "__main__":
    main()
