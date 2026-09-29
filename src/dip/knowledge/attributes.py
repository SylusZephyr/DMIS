"""Attribute extraction and component role (spec 10, 19 stage 5, 101, 128).

Tier 1 of the extraction pipeline -- deterministic and free: every attribute of the market's schema
(``config/platform/knowledge.yaml`` ``attribute_schemas``) is looked for in the listing's title, then its
sub-category and description.

* number -- a quantity in canonical units (``units``), the largest plausible one (spec sheets state maxima)
* enum   -- the canonical value whose synonyms appear; two different values found -> both reported and
            the confidence lowered (a conflict for review, spec 66), never silently resolved
* token  -- model / part numbers by pattern; an optional ``context`` list requires a nearby cue word

Each extracted value carries ``confidence``, the ``field`` it came from and the exact text ``span``.
Rows the deterministic tier leaves without any commercial attribute are marked ``needs_llm`` so the
optional LLM tier (``llm``) or a person can look at them -- that tier never runs by default.

Component role (spec 19 stage 5): ``accessory`` (part/consumable for another product), ``bundle``
(several products sold together), ``configuration`` (a base product with a specific component set, e.g.
N3 + H37L1, identified by the schema's configuration attributes) or ``base``.
"""

from __future__ import annotations

import json
import re
from typing import cast
from functools import lru_cache

import pandas as pd

from dip.knowledge import config, units

# canonical unit name in the schema -> quantity kind in ``units``
_QUANTITY = {"rpm": "rpm", "volt": "volt", "watt": "watt", "amp": "amp", "torque": "torque", "length": "length",
             "mass": "mass", "volume": "volume", "count": "count"}
_FIELD_CONF = {"title": 1.0, "category": 0.9, "description": 0.8}
_CJK = re.compile(r"[㐀-鿿]")


def schema_for(market: str | None) -> dict:
    """The attribute schema of a market: ``generic`` merged with the market's own schema."""
    cfg = config()
    schemas = cfg["attribute_schemas"]
    key = (cfg.get("market_schema") or {}).get(market or "", market)
    own = schemas.get(key, {}) if key in schemas else {}
    return {**schemas["generic"], **own}


def schema_name(market: str | None) -> str:
    cfg = config()
    key = (cfg.get("market_schema") or {}).get(market or "", market)
    return str(key) if key in cfg["attribute_schemas"] else "generic"


def _term_rx(terms: list[str]) -> re.Pattern:
    ascii_t = sorted({t.lower() for t in terms if not _CJK.search(t)}, key=len, reverse=True)
    cjk_t = sorted({t for t in terms if _CJK.search(t)}, key=len, reverse=True)
    parts = []
    if ascii_t:
        parts.append(r"(?<![a-z0-9])(?:" + "|".join(re.escape(t) for t in ascii_t) + r")(?![a-z0-9])")
    if cjk_t:
        parts.append("(?:" + "|".join(re.escape(t) for t in cjk_t) + ")")
    return re.compile("|".join(parts) if parts else r"(?!x)x", re.I)


@lru_cache(maxsize=32)
def _compiled(market: str | None) -> dict:
    out = {}
    for name, spec in schema_for(market).items():
        kind = spec["kind"]
        if kind in ("enum", "list"):
            out[name] = {**spec, "_rx": {v: _term_rx([str(s) for s in syns]) for v, syns in spec["values"].items()}}
        elif kind == "token":
            ctx = spec.get("context")
            out[name] = {**spec, "_rx": re.compile(spec["pattern"]), "_ctx": _term_rx(ctx) if ctx else None}
        else:
            out[name] = dict(spec)
    return out


@lru_cache(maxsize=1)
def _role_rx() -> dict:
    r = config()["component_roles"]
    return {"accessory": _term_rx(r["accessory_terms"]), "bundle": _term_rx(r["bundle_terms"]),
            "configuration": list(r.get("configuration_attributes", []))}


def _fields(row: dict) -> list[tuple[str, str]]:
    return [(f, v) for f in ("title", "category", "description") if isinstance(v := row.get(f), str) and v.strip()]


def _extract_one(name: str, spec: dict, fields: list[tuple[str, str]]) -> dict | None:
    kind = spec["kind"]
    if kind == "number":
        q = _QUANTITY.get(spec.get("unit", ""), spec.get("unit"))
        lim = tuple(spec["plausible"]) if spec.get("plausible") else None
        for fname, text in fields:
            # a spec sheet states maxima (max RPM); a pack size is the first count stated
            hit = units.first(text, q, lim) if q == "count" else units.best(text, cast(str, q), lim)
            if hit:
                return {"value": hit.value, "unit": hit.unit, "confidence": round(0.9 * _FIELD_CONF[fname], 3),
                        "field": fname, "span": hit.raw}
        return None
    if kind in ("enum", "list"):
        for fname, text in fields:
            found, pos = {}, {}
            for value, rx in spec["_rx"].items():
                m = rx.search(text)
                if m:
                    found[value], pos[value] = m.group(0), m.start()
            if found:
                vals = sorted(found, key=lambda v: (pos[v], v))     # first mentioned first
                base = 0.85 * _FIELD_CONF[fname]
                if kind == "list" or len(vals) == 1:
                    v = vals if kind == "list" else vals[0]
                    return {"value": v, "confidence": round(base, 3), "field": fname, "span": "; ".join(found.values())}
                # conflicting values: report all, lower confidence -> review (spec 66)
                return {"value": vals[0], "alternatives": vals[1:], "confidence": round(base * 0.6, 3), "field": fname,
                        "span": "; ".join(found.values()), "conflict": True}
        return None
    if kind == "token":
        for fname, text in fields:
            vals = []
            for m in spec["_rx"].finditer(text):
                if spec.get("_ctx") is not None:
                    window = text[max(0, m.start() - 40): m.end() + 40]
                    if not spec["_ctx"].search(window):
                        continue
                vals.append((m.group(1) if m.re.groups else m.group(0)).upper())
            if vals:
                uniq = sorted(set(vals))
                return {"value": uniq[0] if len(uniq) == 1 else uniq, "confidence": round(0.8 * _FIELD_CONF[fname], 3),
                        "field": fname, "span": ", ".join(uniq)}
        return None
    return None


def _config_attrs(market: str | None) -> list[str]:
    own = [n for n, spec in schema_for(market).items() if spec.get("configuration")]
    return list(dict.fromkeys([*_role_rx()["configuration"], *own]))


def role_of(title: str | None, attrs: dict, pack_count: float | None = None, market: str | None = None) -> tuple[str, float, str]:
    """(role, confidence, reason) -- accessory, bundle, configuration or base."""
    t = title if isinstance(title, str) else ""
    rx = _role_rx()
    m = rx["accessory"].search(t)
    if m:
        return "accessory", 0.7, f"accessory cue '{m.group(0)}'"
    m = rx["bundle"].search(t)
    if m:
        return "bundle", 0.7, f"bundle cue '{m.group(0)}'"
    comps = [a for a in _config_attrs(market) if a in attrs]
    if comps:
        return "configuration", 0.75, "component set: " + ", ".join(f"{a}={_fmt(attrs[a]['value'])}" for a in comps)
    return "base", 0.6, "no accessory, bundle or component cue"


def _fmt(v) -> str:
    return "+".join(map(str, v)) if isinstance(v, list) else str(v)


def configuration_key(attrs: dict, market: str | None = None) -> str | None:
    """Stable key of the component set (``control_unit=N3|handpiece=H37L1``); None when there is none."""
    comps = [a for a in _config_attrs(market) if a in attrs]
    return "|".join(f"{a}={_fmt(attrs[a]['value'])}" for a in sorted(comps)) or None


def extract_row(row: dict, market: str | None) -> dict:
    schema = _compiled(market)
    fields = _fields(row)
    attrs = {}
    for name, spec in schema.items():
        got = _extract_one(name, spec, fields)
        if got is not None:
            attrs[name] = got
    commercial = [n for n, s in schema.items() if s.get("commercial")]
    found = [n for n in commercial if n in attrs]
    pack = attrs.get("pack_count", {}).get("value")
    role, role_conf, role_reason = role_of(row.get("title"), attrs, pack, market)
    conf = sum(attrs[n]["confidence"] for n in found) / len(found) if found else 0.0
    return {"attributes": attrs, "commercial_found": found, "commercial_total": len(commercial),
            "extraction_confidence": round(conf, 3), "attribute_coverage": round(len(found) / len(commercial), 3) if commercial else None,
            "role": role, "role_confidence": role_conf, "role_reason": role_reason,
            "configuration_key": configuration_key(attrs, market), "needs_llm": not found,
            "conflicts": sorted(n for n, a in attrs.items() if a.get("conflict"))}


def extract(frame: pd.DataFrame, market: str | None) -> pd.DataFrame:
    """One row per input row (same index): JSON ``kn_attributes`` plus flat columns ``attr_<name>`` for
    commercial attributes (scalars; lists joined with '+')."""
    cols = [c for c in ("title", "category", "description") if c in frame]
    rows = frame[cols].to_dict("records")
    res = [extract_row(r, market) for r in rows]
    out = pd.DataFrame({
        "kn_attributes": [json.dumps(r["attributes"], ensure_ascii=False) for r in res],
        "attribute_coverage": [r["attribute_coverage"] for r in res],
        "extraction_confidence": [r["extraction_confidence"] for r in res],
        "component_role": [r["role"] for r in res],
        "component_role_confidence": [r["role_confidence"] for r in res],
        "component_role_reason": [r["role_reason"] for r in res],
        "configuration_key": [r["configuration_key"] for r in res],
        "attribute_conflicts": [",".join(r["conflicts"]) or None for r in res],
        "needs_llm": [r["needs_llm"] for r in res],
    }, index=frame.index)
    for name, spec in schema_for(market).items():
        if spec.get("commercial"):
            out[f"attr_{name}"] = [(_fmt(r["attributes"][name]["value"]) if name in r["attributes"] else None) for r in res]
    return out
