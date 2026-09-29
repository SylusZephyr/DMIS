"""End-to-end smoke test against a running DMIS API (Docker stack or plain processes). Standard library only.

    python scripts/smoke_api.py --base http://127.0.0.1:8105 [--token TOKEN] [--timeout 900]

1. waits for GET /api/v2/health
2. uploads a small synthetic SellerSprite-like CSV (dental micromotors + non-dental noise) as market ``smoke``
3. polls GET /api/v2/jobs/{id} until the job is done (fails on ``failed`` or timeout)
4. checks GET /api/v2/markets/smoke and /markets/smoke/products return data, and that X-Request-ID is echoed

Exit code 0 on success; prints one line per step.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import sys
import time
import urllib.error
import urllib.request
import uuid

TEMPLATES = [
    ("{b} Brushless Micromotor 50000 RPM Dental Lab Polishing Handpiece", 380, 40, 0.06),
    ("{b} Brushless Micromotor 60000 RPM Dental Lab Handpiece Motor", 520, 25, 0.10),
    ("{b} Brushed Dental Lab Micromotor 35000 RPM N3 H37L1 Handpiece", 95, 120, -0.02),
    ("{b} Denture Base Plate Wax Sheets 20 pcs Dental Lab", 12, 300, 0.01),
    ("{b} Dental Impression Trays Plastic 50 pcs Disposable", 15, 200, 0.02),
]
BRANDS = ["Acme", "Zeno", "Marathon", "Dentix", "Luxor", "Orion"]
NOISE = [("Jewelry Engraving Rotary Tool Kit 35000 RPM", "Craftor", 45, 80),
         ("Electric Nail Drill Manicure Pedicure File 30000 RPM", "Nailpro", 30, 150)]
HEADER = ["Item Code", "Product Name", "Maker", "Cost to customer", "Units/Month", "Col_X", "stars", "Snapshot", "Node"]


def synthetic_csv(months: int = 4) -> bytes:
    rnd = random.Random(1)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADER)
    dates = [f"2025-{m:02d}-01" for m in range(1, months + 1)]
    for t_i, (tpl, price, sales, g) in enumerate(TEMPLATES):
        for b_i, brand in enumerate(BRANDS):
            for m_i, d in enumerate(dates):
                p = round(price * (1 + 0.08 * (b_i - 2.5) / 2.5), 2)
                s = max(1, round(sales * (1 + g) ** m_i * (1 + 0.09 * rnd.gauss(0, 1))))
                w.writerow([f"B0SYN{t_i}{b_i:02d}XY", tpl.format(b=brand), brand, p, s, round(p * s, 2),
                            round(4.6 - 0.1 * b_i, 1), d, "Dental Lab Equipment"])
    for i, (title, brand, price, sales) in enumerate(NOISE):
        for d in dates:
            w.writerow([f"B0NOISE{i}XYZ", title, brand, price, sales, price * sales, 4.1, d, "Jewelry & Crafts"])
    return buf.getvalue().encode("utf-8")


class Api:
    def __init__(self, base: str, token: str | None):
        self.base, self.token = base.rstrip("/"), token

    def call(self, method: str, path: str, body: bytes | None = None, ctype: str | None = None, headers: dict | None = None):
        req = urllib.request.Request(self.base + path, data=body, method=method)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if ctype:
            req.add_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 -- operator-given URL
            return r.status, dict(r.headers), json.loads(r.read() or b"null")


def multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (name, data) in files.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                  "Content-Type: text/csv\r\n\r\n".encode() + data + b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8105")
    ap.add_argument("--token", default=None)
    ap.add_argument("--market", default="smoke")
    ap.add_argument("--timeout", type=float, default=900, help="seconds for health + job together")
    a = ap.parse_args(argv)
    api, deadline = Api(a.base, a.token), time.monotonic() + a.timeout

    while True:                                                         # 1. health
        try:
            st, hdr, h = api.call("GET", "/api/v2/health", headers={"X-Request-ID": "smoke-health"})
            if st == 200 and h.get("status") == "ok":
                break
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        if time.monotonic() > deadline:
            print("FAIL health: API did not become healthy")
            return 1
        time.sleep(3)
    rid = {k.lower(): v for k, v in hdr.items()}.get("x-request-id")
    print(f"ok   health: version {h['version']}, storage {json.dumps(h['storage'])}, request id echoed: {rid == 'smoke-health'}")

    body, ctype = multipart({"market": a.market, "force": "true"}, {"file": ("smoke_2025-04.csv", synthetic_csv())})
    st, _, up = api.call("POST", "/api/v2/datasets", body, ctype)      # 2. upload
    print(f"ok   upload: job {up['job_id']} for market {up['market']}")

    status = None                                                       # 3. wait for the job
    while time.monotonic() < deadline:
        _, _, job = api.call("GET", f"/api/v2/jobs/{up['job_id']}")
        status = job.get("status")
        if status in ("done", "failed", "error"):
            break
        time.sleep(3)
    if status != "done":
        print(f"FAIL job: status {status!r}; error: {str(job.get('error'))[:500]}")
        return 1
    print(f"ok   job: {len(job.get('stages') or [])} stages done")

    _, _, m = api.call("GET", f"/api/v2/markets/{a.market}")          # 4. market endpoints
    _, _, prods = api.call("GET", f"/api/v2/markets/{a.market}/products?limit=5")
    items = prods.get("items") if isinstance(prods, dict) else prods
    if not items:
        print("FAIL market: no products")
        return 1
    print(f"ok   market: {m.get('name', a.market)} with {prods.get('total', len(items))} products; top: {items[0].get('title')!r}")
    if rid != "smoke-health":
        print("FAIL request id: X-Request-ID not echoed")
        return 1
    print("SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
