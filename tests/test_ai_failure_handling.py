"""M13 — AI reliability layer. Every existing AI-using module
(classifier.py, product_type_classifier.py, matching/resolution.py,
reviews/extraction.py) used to call the Anthropic SDK directly with no
exception handling at all: a network error, a rate limit, or a response
with no valid JSON in it would raise straight out of the function and
crash whatever batch script was running it. `dmie.ai.client.call_ai` is
the one place that now talks to the SDK, and it never raises -- it always
returns an `AICallResult` describing what happened.

These tests never touch the real network or require the `anthropic`
package to be installed (it isn't, in this environment) -- they patch
`anthropic.Anthropic` behind a fake module the way the SDK's shape would
require, exercising call_ai's own retry/parsing/logging logic in
isolation.
"""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from dmie.ai.client import (
    STATUS_ERROR,
    STATUS_MALFORMED,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    call_ai,
)


def _fake_response(text: str):
    message = MagicMock()
    message.content = [MagicMock(text=text)]
    return message


@pytest.fixture(autouse=True)
def _no_real_sleep():
    """Retries use a real (small) backoff -- patch it out so this test
    file doesn't spend wall-clock time sleeping."""
    with patch("dmie.ai.client.time.sleep"):
        yield


@pytest.fixture
def fake_anthropic_module():
    """`anthropic` isn't installed in this environment (by design -- see
    every classify_with_ai's try/except ImportError). Inject a fake
    module into sys.modules so `import anthropic` succeeds inside call_ai,
    with an `Anthropic` class we control per test."""
    fake_module = types.ModuleType("anthropic")
    client_factory = MagicMock()
    fake_module.Anthropic = client_factory
    with patch.dict(sys.modules, {"anthropic": fake_module}), \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        yield client_factory


# --- Not configured at all -- not a failure, an unconfigured stage ---

def test_no_api_key_is_unavailable_not_an_error():
    with patch.dict("os.environ", {}, clear=True):
        result = call_ai("prompt", model="m", max_tokens=10)
    assert result.status == STATUS_UNAVAILABLE
    assert result.data is None
    assert result.attempts == 0


def test_package_not_installed_is_unavailable():
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}), \
         patch.dict(sys.modules, {"anthropic": None}):
        result = call_ai("prompt", model="m", max_tokens=10)
    assert result.status == STATUS_UNAVAILABLE


# --- Valid response ---

def test_valid_response_is_parsed_on_first_attempt(fake_anthropic_module):
    fake_anthropic_module.return_value.messages.create.return_value = _fake_response(
        '{"decision": "MATCH", "confidence": 0.9}'
    )
    result = call_ai("prompt", model="m", max_tokens=10)
    assert result.status == STATUS_OK
    assert result.data == {"decision": "MATCH", "confidence": 0.9}
    assert result.attempts == 1


def test_valid_response_with_surrounding_prose_still_parses(fake_anthropic_module):
    """Models often wrap JSON in explanatory text -- the brace-slice
    extraction must still find it."""
    fake_anthropic_module.return_value.messages.create.return_value = _fake_response(
        'Here is my answer:\n{"product_type": "DB_RESIN", "confidence": 0.95}\nHope that helps!'
    )
    result = call_ai("prompt", model="m", max_tokens=10)
    assert result.status == STATUS_OK
    assert result.data["product_type"] == "DB_RESIN"


# --- Malformed response: no JSON object in the text at all ---

def test_malformed_response_never_raises_and_is_reported(fake_anthropic_module):
    fake_anthropic_module.return_value.messages.create.return_value = _fake_response(
        "I'm not sure how to answer that."
    )
    result = call_ai("prompt", model="m", max_tokens=10, max_retries=1)
    assert result.status == STATUS_MALFORMED
    assert result.data is None
    assert result.error is not None
    assert result.attempts == 2  # first try + 1 retry, both malformed


def test_invalid_json_syntax_never_raises(fake_anthropic_module):
    fake_anthropic_module.return_value.messages.create.return_value = _fake_response(
        '{"decision": "MATCH", confidence: }'  # unquoted key, trailing garbage
    )
    result = call_ai("prompt", model="m", max_tokens=10, max_retries=0)
    assert result.status == STATUS_MALFORMED
    assert result.data is None


# --- Empty response ---

def test_empty_response_text_never_raises(fake_anthropic_module):
    fake_anthropic_module.return_value.messages.create.return_value = _fake_response("")
    result = call_ai("prompt", model="m", max_tokens=10, max_retries=0)
    assert result.status == STATUS_MALFORMED
    assert result.data is None


# --- API failure (generic SDK/network exception) ---

def test_api_failure_never_raises_and_is_reported(fake_anthropic_module):
    fake_anthropic_module.return_value.messages.create.side_effect = ConnectionError("connection reset")
    result = call_ai("prompt", model="m", max_tokens=10, max_retries=1)
    assert result.status == STATUS_ERROR
    assert result.data is None
    assert "connection reset" in result.error
    assert result.attempts == 2


def test_rate_limit_style_failure_never_raises(fake_anthropic_module):
    class RateLimited(Exception):
        pass

    fake_anthropic_module.return_value.messages.create.side_effect = RateLimited("429 too many requests")
    result = call_ai("prompt", model="m", max_tokens=10, max_retries=1)
    assert result.status == STATUS_ERROR
    assert "429" in result.error


# --- Timeout simulation ---

def test_timeout_never_raises(fake_anthropic_module):
    fake_anthropic_module.return_value.messages.create.side_effect = TimeoutError("request timed out")
    result = call_ai("prompt", model="m", max_tokens=10, max_retries=1)
    assert result.status == STATUS_ERROR
    assert "timed out" in result.error


# --- Retry behavior: recovers if a later attempt succeeds ---

def test_recovers_after_transient_failure_then_success(fake_anthropic_module):
    fake_anthropic_module.return_value.messages.create.side_effect = [
        ConnectionError("flaky"),
        _fake_response('{"ok": true}'),
    ]
    result = call_ai("prompt", model="m", max_tokens=10, max_retries=2)
    assert result.status == STATUS_OK
    assert result.data == {"ok": True}
    assert result.attempts == 2


def test_gives_up_after_exhausting_all_retries(fake_anthropic_module):
    fake_anthropic_module.return_value.messages.create.side_effect = ConnectionError("always fails")
    result = call_ai("prompt", model="m", max_tokens=10, max_retries=2)
    assert result.status == STATUS_ERROR
    assert result.attempts == 3  # first try + 2 retries
    assert fake_anthropic_module.return_value.messages.create.call_count == 3


# --- One failed AI request must not stop the entire pipeline: proven at
# a real call-site level, not just inside call_ai itself. ---

def test_a_failed_call_does_not_crash_the_relevance_classifier():
    from dmie.classification.classifier import ListingContext, classify_listing

    with patch("dmie.classification.classifier.call_ai") as mock_call:
        mock_call.return_value.ok = False
        result = classify_listing(ListingContext(listing_id="L1", title="Something with no rule match at all"))
    assert result.relevance_class == "UNCERTAIN"
    assert result.review_status == "needs_review"


def test_a_failed_call_does_not_crash_product_type_classification():
    from dmie.classification.product_type_classifier import ListingContext, UNCERTAIN, classify_product_type

    with patch("dmie.classification.product_type_classifier.call_ai") as mock_call:
        mock_call.return_value.ok = False
        result = classify_product_type(ListingContext(listing_id="L1", title="Some ambiguous acrylic reline resin listing"))
    assert result.product_type == UNCERTAIN
    assert result.review_status == "needs_review"
