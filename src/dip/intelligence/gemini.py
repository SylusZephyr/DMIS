"""Google Gemini (free tier) over its REST API -- used only to *phrase* facts the platform computed.

Key: ``GEMINI_API_KEY`` (environment / .env, never committed). Model, endpoint and limits live in
config/platform/analyst.yaml (``gemini``). Without a key every call returns status "unavailable" and the
caller shows the computed answer; nothing depends on the model being reachable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dip.intelligence.analyst import config


@dataclass
class Reply:
    status: str                 # ok | unavailable | error
    text: str | None = None
    error: str | None = None
    model: str | None = None


def available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


def generate(prompt: str, system: str | None = None) -> Reply:
    cfg = config()["gemini"]
    model = cfg["model"]
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return Reply("unavailable", error="GEMINI_API_KEY is not set", model=model)
    import httpx

    body: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": cfg["temperature"], "maxOutputTokens": cfg["max_output_tokens"]}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    url = f"{cfg['endpoint'].rstrip('/')}/models/{model}:generateContent"
    try:
        r = httpx.post(url, json=body, headers={"x-goog-api-key": key}, timeout=cfg["timeout_seconds"])
    except httpx.HTTPError as exc:
        return Reply("error", error=f"{type(exc).__name__}: {exc}", model=model)
    if r.status_code != 200:
        return Reply("error", error=f"HTTP {r.status_code}: {r.text[:300]}", model=model)
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, ValueError, TypeError):
        return Reply("error", error="unexpected response shape", model=model)
    return Reply("ok" if text else "error", text or None, None if text else "empty reply", model)
