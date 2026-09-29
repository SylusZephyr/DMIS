"""Shopping requirement extraction (spec 43-44): a buyer's free-text need -> structured requirements.

    "brushless micromotor, at least 50k rpm, under $300, 4+ stars"
      -> technology = brushless; max_rpm >= 50000; budget_max = 300; min_rating = 4

Deterministic, reusing the category's attribute schema (config) and the unit parser:

* enum / list / token attributes found in the text become equality (or contains) requirements;
* number attributes become ``min`` / ``max`` / ``about`` requirements from the words around the number
  ("at least", "50k+", "up to", "under"...); ``about`` accepts +/- ``about_tolerance``;
* money ("under $300", "$50-100", "at least $30") becomes the budget, stars ("4+ stars") the minimum rating.

A product meets a requirement when its consensus attribute satisfies it, fails it when the attribute
contradicts it, and is ``unknown`` when the product does not state the attribute -- unknown is never
counted as a failure. Settings: ``needs`` in config/platform/knowledge.yaml.
"""

from __future__ import annotations

import json
import re

import pandas as pd

from dip.knowledge import attributes, config

_MONEY = r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?:usd|dollars?|\$)?"
_RANGE = re.compile(r"\$\s*(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*\$?\s*(\d+(?:\.\d+)?)|between\s+\$?(\d+(?:\.\d+)?)\s+and\s+\$?(\d+(?:\.\d+)?)", re.I)
_MAXP = re.compile(r"(?:under|below|less than|max(?:imum)?|up to|at most|<=?|no more than|budget(?: of)?)\s*" + _MONEY, re.I)
_MINP = re.compile(r"(?:over|above|more than|at least|min(?:imum)?|>=?)\s*\$\s*(\d+(?:\.\d+)?)", re.I)
_STARS = re.compile(r"(\d(?:\.\d)?)\s*(?:\+|or (?:more|higher|better))?\s*stars?|rated\s+(?:at least\s+)?(\d(?:\.\d)?)", re.I)
_MIN_WORDS = re.compile(r"(at least|min(?:imum)?|>=?|over|above|more than|no less than)\s*$", re.I)
_MAX_WORDS = re.compile(r"(at most|max(?:imum)?|<=?|up to|under|below|less than|no more than)\s*$", re.I)


def _cfg() -> dict:
    return config()["needs"]


def parse(need: str, market: str | None) -> dict:
    """{requirements: [...], budget_min, budget_max, min_rating} from the buyer's words."""
    text = need or ""
    out: dict = {"requirements": [], "budget_min": None, "budget_max": None, "min_rating": None}
    money_spans: list[tuple[int, int]] = []
    m = _RANGE.search(text)
    if m:
        a, b = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        out["budget_min"], out["budget_max"] = sorted([float(a), float(b)])
        money_spans.append(m.span())
    else:
        mx = _MAXP.search(text)
        if mx and ("$" in mx.group(0) or re.search(r"usd|dollar|budget", mx.group(0), re.I)):
            out["budget_max"] = float(mx.group(1).replace(",", ""))
            money_spans.append(mx.span())
        mn = _MINP.search(text)
        if mn:
            out["budget_min"] = float(mn.group(1))
            money_spans.append(mn.span())
    s = _STARS.search(text)
    if s:
        v = float(s.group(1) or s.group(2))
        if 0 < v <= 5:
            out["min_rating"] = v
            money_spans.append(s.span())
    masked = list(text)
    for a, b in money_spans:                      # money and stars are not attribute numbers
        masked[a:b] = " " * (b - a)
    clean = "".join(masked)
    found = attributes.extract_row({"title": clean}, market)["attributes"]
    schema = attributes.schema_for(market)
    for name, a in found.items():
        spec = schema.get(name, {})
        if spec.get("kind") == "number":
            span = str(a.get("span") or "")
            i = clean.find(span) if span else -1
            before = clean[max(0, i - 20): i] if i >= 0 else ""
            after = clean[i + len(span): i + len(span) + 2] if i >= 0 else ""
            op = ("min" if _MIN_WORDS.search(before) or after.startswith("+") else "max" if _MAX_WORDS.search(before) else "about")
            out["requirements"].append({"attribute": name, "op": op, "value": a["value"], "unit": a.get("unit"), "text": span})
        else:
            op = "contains" if spec.get("kind") == "list" else "eq"
            out["requirements"].append({"attribute": name, "op": op, "value": a["value"], "text": a.get("span")})
    return out


def check(req: dict, product_attrs: dict) -> str:
    """met | failed | unknown."""
    if req["attribute"] not in product_attrs:
        return "unknown"
    v = product_attrs[req["attribute"]]
    v = v.get("value") if isinstance(v, dict) else v
    want = req["value"]
    if req["op"] in ("eq", "contains"):
        have = v if isinstance(v, list) else [v]
        wants = want if isinstance(want, list) else [want]
        return "met" if all(str(w).lower() in {str(h).lower() for h in have} for w in wants) else "failed"
    try:
        x, w = float(v), float(want)
    except (TypeError, ValueError):
        return "unknown"
    tol = float(_cfg()["about_tolerance"])
    ok = x >= w if req["op"] == "min" else x <= w if req["op"] == "max" else abs(x - w) <= tol * abs(w)
    return "met" if ok else "failed"


def evaluate(products: pd.DataFrame, reqs: list[dict]) -> pd.DataFrame:
    """Per product: requirement_share (met / stated), requirements_failed, and each requirement's status."""
    rows = []
    for a in products.get("kn_attributes", pd.Series([None] * len(products), index=products.index)):
        attrs = json.loads(a) if isinstance(a, str) else {}
        st = [check(r, attrs) for r in reqs]
        known = [x for x in st if x != "unknown"]
        rows.append({"requirement_share": (sum(x == "met" for x in known) / len(known)) if known else None,
                     "requirements_failed": sum(x == "failed" for x in st), "requirement_status": st})
    return pd.DataFrame(rows, index=products.index)
