"""Production-safe wrapper around the Anthropic SDK call every AI-using
module in this codebase needs: config check, the API call itself, and
parsing the model's response into a dict -- all in one place, all
non-raising.

Callers (classifier.py, product_type_classifier.py, matching/resolution.py,
reviews/extraction.py) keep their own business logic (prompt building,
result post-processing, business-specific fallback fields) exactly as
before -- this module only owns "talk to the SDK and get a dict back
without crashing," not what the dict means.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("dmie.ai")

# A single stuck call was found to block for 2.8 hours despite
# anthropic.Anthropic(timeout=30.0) -- the hang happens in the OS
# network stack below where httpx's own timeout logic ever starts
# counting (a Windows-specific connect()-level stall, not an HTTP
# response timeout), so the SDK's own timeout parameter cannot bound
# it. A background thread pool gives call_ai a hard wall-clock ceiling
# regardless of where the underlying stall actually is: Python cannot
# forcibly kill a blocked thread, so a request that hits this pathology
# leaks its worker thread rather than ever returning -- an accepted
# tradeoff (a few leaked idle threads over a long batch run) against
# the alternative (the whole batch blocking for hours). If this proves
# frequent rather than rare, the real fix is diagnosing the Windows-level
# connect() stall directly, not a larger thread pool.
_CALL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="dmie-ai-call")
_HARD_TIMEOUT_SECONDS = 35.0

# Loads ANTHROPIC_API_KEY (and anything else) from a local .env at the
# project root into os.environ, if one exists -- .env is gitignored
# (never committed, see .gitignore) and was previously read by nothing
# in this codebase, so a real key had to be re-exported in every new
# terminal session. override=False: an already-set real environment
# variable always wins over the file, so CI/deployment env vars aren't
# silently shadowed by a stray local .env.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)
except ImportError:
    pass  # python-dotenv is optional -- a real environment variable still works without it

DEFAULT_MAX_RETRIES = 2  # additional attempts after the first -- 3 tries total
DEFAULT_BACKOFF_SECONDS = 0.5

# Every possible outcome -- exactly one of these, never left to infer from
# combinations of data/error being None or not.
STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"    # no API key / SDK not installed -- not a failure, an unconfigured stage
STATUS_ERROR = "error"                # every attempt raised (network, rate limit, timeout, auth, ...)
STATUS_MALFORMED = "malformed"        # got a response, but couldn't find/parse a JSON object in it


@dataclass
class AICallResult:
    status: str  # STATUS_OK | STATUS_UNAVAILABLE | STATUS_ERROR | STATUS_MALFORMED
    data: dict | None = None
    raw_text: str | None = None
    error: str | None = None
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def _parse_json_object(text: str) -> dict:
    """Raises ValueError (never a bare exception type from json/str
    internals) on anything that isn't a parseable JSON object -- callers
    of call_ai never see this, it's caught internally."""
    if not text or not text.strip():
        raise ValueError("empty response text")
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in response text")
    return json.loads(text[start:end])


def call_ai(
    prompt: str,
    model: str,
    max_tokens: int,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
) -> AICallResult:
    """Checks for a configured provider, calls the model, parses the
    response as a JSON object -- retrying transient failures (any
    exception from the API call, or a response that didn't parse) up to
    `max_retries` times with a linear backoff. Never raises: every
    outcome, including total failure, comes back as an AICallResult.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return AICallResult(status=STATUS_UNAVAILABLE, error="ANTHROPIC_API_KEY not configured")
    try:
        import anthropic
    except ImportError:
        return AICallResult(status=STATUS_UNAVAILABLE, error="anthropic package not installed")

    # Explicit timeout (SDK default is much longer / effectively
    # unbounded on a hung connection) -- a single stuck request was found
    # to block a whole classify.py run (hundreds of listings) for 50+
    # minutes with zero progress and zero visibility, no crash, no retry,
    # nothing to observe or kill except the whole process. 30s is
    # generous for a ~300-token structured completion; a genuinely slow
    # response should still succeed well within it, and a truly hung
    # connection now fails fast into the existing retry loop instead of
    # hanging forever.
    client = anthropic.Anthropic(api_key=api_key, timeout=30.0)
    last_error: Exception | None = None
    last_text: str | None = None

    for attempt in range(1, max_retries + 2):  # first try + max_retries retries
        try:
            future = _CALL_EXECUTOR.submit(
                client.messages.create, model=model, max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            response = future.result(timeout=_HARD_TIMEOUT_SECONDS)
            # current models may return a thinking block before the text: skip thinking blocks and
            # take the first block that carries text
            text = next((b.text for b in response.content
                         if getattr(b, "type", None) not in ("thinking", "redacted_thinking")
                         and isinstance(getattr(b, "text", None), str)), "")
            last_text = text
            data = _parse_json_object(text)
            if attempt > 1:
                logger.info("AI call succeeded on attempt %d/%d", attempt, max_retries + 1)
            return AICallResult(status=STATUS_OK, data=data, raw_text=text, attempts=attempt)
        except ValueError as exc:
            # A real response came back, it just didn't parse -- worth a
            # retry (the model may answer differently next time), but
            # this is a "malformed", not "error" outcome if we ultimately
            # give up on it.
            last_error = exc
            logger.warning("AI response did not parse as JSON (attempt %d/%d): %s", attempt, max_retries + 1, exc)
        except Exception as exc:  # noqa: BLE001 -- the SDK's own exception hierarchy isn't
            # available without the package installed; any failure here
            # (timeout, rate limit, connection error, auth failure, ...)
            # is equally "the AI call itself failed," and equally
            # something a retry might recover from.
            last_error = exc
            logger.warning("AI call failed (attempt %d/%d): %s: %s", attempt, max_retries + 1, type(exc).__name__, exc)

        if attempt <= max_retries:
            time.sleep(backoff_seconds * attempt)

    logger.error("AI call exhausted all %d attempts, giving up: %s", max_retries + 1, last_error)
    status = STATUS_MALFORMED if isinstance(last_error, ValueError) else STATUS_ERROR
    return AICallResult(status=status, raw_text=last_text, error=str(last_error), attempts=max_retries + 1)
