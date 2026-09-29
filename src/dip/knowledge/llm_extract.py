"""LLM attribute tier (spec 10, 101, 166): structured extraction for rows the rules could not read.

Runs only on rows the deterministic tier marked ``needs_llm`` (no commercial attribute found), at most
``max_rows_per_run`` rows and ``budget_usd_per_run`` dollars per run. For each row:

1. the prompt lists the category's attribute schema (allowed enum values, units) and the row's own text;
2. the model must answer with JSON: ``{"attributes": {name: {"value": ..., "evidence": "<exact words>"}}}``;
3. every proposed value is validated -- the attribute must exist in the schema, the value must be allowed
   (enum / list values from config, a number inside the plausible range), and the evidence must occur in the
   row's text. Anything else is rejected and recorded, never used;
4. accepted values fill only attributes the rules left empty, with ``source: llm`` and a confidence below
   the rule tier (``llm_confidence``), so they stay visible as machine judgments;
5. every call is an ``ai_traces`` row (model, prompt version, record id, input hash, output, status, tokens,
   cost). A prompt already answered with status ok is reused from its trace, never paid twice.

Without a provider key the tier reports ``unavailable`` and changes nothing. Settings: ``llm`` in
config/platform/knowledge.yaml; keys ``ANTHROPIC_API_KEY`` / ``GEMINI_API_KEY`` (environment or .env).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from dip.knowledge import attributes, config

PURPOSE = "knowledge.extract"

SYSTEM = ("You extract product attributes from Amazon listing text for a dental-market database. Use ONLY the text given. "
          "For every attribute you can read, return its value and the exact words from the text that state it (evidence). "
          "Enum and list attributes must use one of the allowed values. Numbers must be in the stated unit. "
          "Leave out anything the text does not state. Answer with one JSON object and nothing else: "
          '{"attributes": {"<name>": {"value": <value>, "evidence": "<exact words>"}}}')


@dataclass
class LLMReply:
    status: str                      # ok | unavailable | error
    text: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


Provider = Callable[[str, str], LLMReply]      # (system, prompt) -> reply


def _cfg() -> dict:
    return config()["llm"]


# ---------------------------------------------------------------- providers
def _anthropic(system: str, prompt: str) -> LLMReply:
    c = _cfg()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return LLMReply("unavailable", error="ANTHROPIC_API_KEY is not set", model=c["model"])
    try:
        import anthropic
    except ImportError:
        return LLMReply("unavailable", error="anthropic package not installed", model=c["model"])
    try:
        r = anthropic.Anthropic(api_key=key, timeout=float(c.get("timeout_seconds", 30))).messages.create(
            model=c["model"], max_tokens=int(c["max_tokens"]), system=system, messages=[{"role": "user", "content": prompt}])
    except Exception as exc:  # noqa: BLE001 -- any SDK / network failure is a traced error, never a crash
        return LLMReply("error", error=f"{type(exc).__name__}: {exc}"[:500], model=c["model"])
    text = next((b.text for b in r.content if isinstance(getattr(b, "text", None), str)), "")
    u = getattr(r, "usage", None)
    return LLMReply("ok", text, c["model"], getattr(u, "input_tokens", None), getattr(u, "output_tokens", None))


def _gemini_model() -> str:
    """The Gemini model the analyst already uses (config/platform/analyst.yaml), unless ``llm.gemini_model`` is set."""
    from dip.intelligence.analyst import config as analyst_config

    return _cfg().get("gemini_model") or analyst_config()["gemini"]["model"]


def _gemini(system: str, prompt: str) -> LLMReply:
    c = _cfg()
    model = _gemini_model()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return LLMReply("unavailable", error="GEMINI_API_KEY is not set", model=model)
    import httpx

    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {"temperature": 0, "maxOutputTokens": int(c["max_tokens"]), "responseMimeType": "application/json"}}
    try:
        r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", json=body,
                       headers={"x-goog-api-key": key}, timeout=float(c.get("timeout_seconds", 30)))
    except httpx.HTTPError as exc:
        return LLMReply("error", error=f"{type(exc).__name__}: {exc}"[:500], model=model)
    if r.status_code != 200:
        return LLMReply("error", error=f"HTTP {r.status_code}: {r.text[:300]}", model=model)
    try:
        j = r.json()
        text = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
        u = j.get("usageMetadata") or {}
    except (KeyError, IndexError, ValueError, TypeError):
        return LLMReply("error", error="unexpected response shape", model=model)
    return LLMReply("ok", text, model, u.get("promptTokenCount"), u.get("candidatesTokenCount"))


def default_provider() -> Provider | None:
    """The configured provider when its key is set, else None (the tier is unavailable)."""
    p = _cfg().get("provider", "anthropic")
    if p == "gemini":
        return _gemini if os.environ.get("GEMINI_API_KEY", "").strip() else None
    return _anthropic if os.environ.get("ANTHROPIC_API_KEY", "").strip() else None


# ---------------------------------------------------------------- prompt and validation
def _schema_lines(market: str | None) -> list[str]:
    out = []
    for name, spec in attributes.schema_for(market).items():
        k = spec["kind"]
        if k in ("enum", "list"):
            out.append(f"- {name} ({'one of' if k == 'enum' else 'any of, as a list'}): {', '.join(map(str, spec['values']))}")
        elif k == "number":
            rng = f", plausible {spec['plausible'][0]}-{spec['plausible'][1]}" if spec.get("plausible") else ""
            out.append(f"- {name} (number in {spec.get('unit', '')}{rng})")
        else:
            out.append(f"- {name} (text code as written)")
    return out


def prompt_for(row: dict, market: str | None) -> str:
    text = "\n".join(f"{f.upper()}: {row[f]}" for f in ("title", "category", "description")
                     if isinstance(row.get(f), str) and row[f].strip())
    return "ATTRIBUTES:\n" + "\n".join(_schema_lines(market)) + "\n\nLISTING:\n" + text


def _json(text: str | None) -> dict | None:
    if not text:
        return None
    s, e = text.find("{"), text.rfind("}") + 1
    if s < 0 or e <= s:
        return None
    try:
        v = json.loads(text[s:e])
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def validate(out: dict, row: dict, market: str | None) -> tuple[dict, list[dict]]:
    """(accepted attributes, rejected proposals with the reason)."""
    schema = attributes.schema_for(market)
    hay = " ".join(str(row.get(f) or "") for f in ("title", "category", "description")).lower()
    conf = float(_cfg().get("llm_confidence", 0.6))
    acc, rej = {}, []
    raw = out.get("attributes")
    props: dict = raw if isinstance(raw, dict) else {}
    for name, p in props.items():
        spec = schema.get(name)
        v = p.get("value") if isinstance(p, dict) else None
        ev = str(p.get("evidence") or "").strip() if isinstance(p, dict) else ""
        why = None
        if spec is None:
            why = "not in the schema"
        elif v is None or v == "" or v == []:
            why = "empty value"
        elif not ev or ev.lower() not in hay:
            why = "evidence not found in the listing text"
        elif spec["kind"] == "enum" and v not in spec["values"]:
            why = f"value not allowed: {v}"
        elif spec["kind"] == "list":
            v = v if isinstance(v, list) else [v]
            if not v or any(x not in spec["values"] for x in v):
                why = f"value not allowed: {v}"
        elif spec["kind"] == "number":
            try:
                v = float(v)
            except (TypeError, ValueError):
                why = f"not a number: {v}"
            else:
                lim = spec.get("plausible")
                if not math.isfinite(v) or (lim and not lim[0] <= v <= lim[1]):
                    why = f"outside the plausible range: {v}"
        elif spec["kind"] == "token":
            v = str(v).upper()
        if why or spec is None:
            rej.append({"attribute": name, "value": v, "reason": why})
            continue
        a = {"value": v, "confidence": conf, "field": "llm", "span": ev, "source": "llm"}
        if spec["kind"] == "number":
            a["unit"] = spec.get("unit")
        acc[name] = a
    return acc, rej


# ---------------------------------------------------------------- the tier
def _cost(reply: LLMReply) -> float | None:
    p = _cfg().get("price_per_mtok") or {}
    if reply.input_tokens is None and reply.output_tokens is None:
        return None
    return round(((reply.input_tokens or 0) * float(p.get("input", 0)) + (reply.output_tokens or 0) * float(p.get("output", 0))) / 1e6, 6)


def _cached(input_hash: str) -> dict | None:
    from dip.storage import business as b

    with b.session() as s:
        t = (s.query(b.AITrace).filter(b.AITrace.purpose == PURPOSE, b.AITrace.input_hash == input_hash, b.AITrace.status == "ok")
             .order_by(b.AITrace.created_at.desc()).first())
        return dict(t.output) if t and t.output else None


def _trace(**kw) -> None:
    from dip.storage import business as b

    with b.session() as s:
        s.add(b.AITrace(purpose=PURPOSE, **kw))
        s.commit()


def _merge(at_row: pd.Series, row: dict, new: dict, market: str | None) -> dict:
    attrs = json.loads(at_row["kn_attributes"]) if isinstance(at_row["kn_attributes"], str) else {}
    added = {k: v for k, v in new.items() if k not in attrs}          # the rule tier always wins
    attrs.update(added)
    schema = attributes.schema_for(market)
    commercial = [n for n, s in schema.items() if s.get("commercial")]
    found = [n for n in commercial if n in attrs]
    role, rconf, reason = attributes.role_of(row.get("title"), attrs, None, market)
    upd = {"kn_attributes": json.dumps(attrs, ensure_ascii=False), "needs_llm": not found,
           "attribute_coverage": round(len(found) / len(commercial), 3) if commercial else None,
           "extraction_confidence": round(sum(attrs[n]["confidence"] for n in found) / len(found), 3) if found else 0.0,
           "configuration_key": attributes.configuration_key(attrs, market), "component_role": role,
           "component_role_confidence": rconf, "component_role_reason": reason, "_added": len(added)}
    for n in commercial:
        if n in added:
            upd[f"attr_{n}"] = "+".join(map(str, attrs[n]["value"])) if isinstance(attrs[n]["value"], list) else str(attrs[n]["value"])
    return upd


def fill(records: pd.DataFrame, at: pd.DataFrame, market: str | None, provider: Provider | None = None) -> tuple[pd.DataFrame, dict]:
    """Run the tier over ``needs_llm`` rows of ``at`` (the rule tier's output, same index as ``records``)."""
    c = _cfg()
    cand = at.index[at["needs_llm"].astype(bool)] if "needs_llm" in at else at.index[:0]
    summary: dict = {"candidates": int(len(cand)), "called": 0, "cached": 0, "rows_filled": 0, "attributes_added": 0,
                     "rejected": 0, "errors": 0, "cost_usd": 0.0, "budget_usd": float(c.get("budget_usd_per_run", 0)),
                     "stopped_by_budget": False}
    if not c.get("enabled") or not len(cand):
        return at, {**summary, "status": "disabled" if not c.get("enabled") else "nothing_to_do"}
    prov = provider or default_provider()
    if prov is None:
        return at, {**summary, "status": "unavailable"}
    at = at.copy()
    model = c["model"] if c.get("provider", "anthropic") != "gemini" else _gemini_model()
    for idx in cand[: int(c["max_rows_per_run"])]:
        if summary["cost_usd"] >= summary["budget_usd"]:
            summary["stopped_by_budget"] = True
            break
        row = records.loc[idx].to_dict()
        prompt = prompt_for(row, market)
        h = hashlib.sha256(f"{c['prompt_version']}|{model}|{SYSTEM}|{prompt}".encode()).hexdigest()
        ref = str(row.get("record_id") or idx)[:512]
        out = _cached(h)
        if out is not None:
            summary["cached"] += 1
            acc = out.get("accepted") or {}
        else:
            reply = prov(SYSTEM, prompt)
            summary["called"] += 1
            cost = _cost(reply)
            summary["cost_usd"] = round(summary["cost_usd"] + (cost or 0), 6)
            parsed = _json(reply.text) if reply.status == "ok" else None
            if reply.status != "ok" or parsed is None:
                summary["errors"] += 1
                _trace(model=reply.model or model or "", prompt_version=c["prompt_version"], input_ref=ref, input_hash=h,
                       output={"error": reply.error, "raw": (reply.text or "")[:1000]}, status="error" if reply.status != "ok" else "malformed",
                       input_tokens=reply.input_tokens, output_tokens=reply.output_tokens, cost_usd=cost, market_name=market)
                continue
            acc, rej = validate(parsed, row, market)
            summary["rejected"] += len(rej)
            _trace(model=reply.model or model or "", prompt_version=c["prompt_version"], input_ref=ref, input_hash=h,
                   output={"accepted": acc, "rejected": rej}, status="ok", confidence=str(c.get("llm_confidence", 0.6)),
                   input_tokens=reply.input_tokens, output_tokens=reply.output_tokens, cost_usd=cost, market_name=market)
        if acc:
            upd = _merge(at.loc[idx], row, acc, market)
            n = upd.pop("_added")
            if n:
                summary["rows_filled"] += 1
                summary["attributes_added"] += n
                for k, v in upd.items():
                    if k in at.columns:
                        at.at[idx, k] = v
    return at, {**summary, "status": "ran"}
