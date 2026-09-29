"""Shared test configuration.

Every test in this suite was written and has always run against
`dmie.ai.client.call_ai()` naturally returning STATUS_UNAVAILABLE --
until now, no `ANTHROPIC_API_KEY` had ever been configured in this
environment, so any code path that fell through to a real AI call
degraded safely and deterministically (see PRINCIPLES.md, M13's AI
reliability layer). Tests that specifically want to exercise the AI
path (tests/test_ai_failure_handling.py, and unit tests that mock
`classify_with_ai`/`ai_arbitrate` directly) already patch what they
need explicitly.

Now that a real key can be configured (`.env`, Tier 2's AI Market
Analyst work), every OTHER test that calls `classify_listing()`/
`classify_product_type()`/`resolve_pair()` without mocking the AI
boundary would otherwise start making real, slow, costly network
calls on every test run -- exactly what happened once, crashing the
SSL stack mid-run on 50 real gold-set classifications
(test_classification_pipeline.py). Clearing the key here, before every
test, restores the deterministic default this whole suite was always
designed around. A test that legitimately wants a real (or fake) key
present sets it back within its own fixture/test body -- monkeypatch
and unittest.mock.patch.dict both correctly layer on top of this
autouse fixture's teardown, restoring the real environment afterward.
"""

import pytest


@pytest.fixture(autouse=True, scope="session")
def _no_real_ai_calls_by_default():
    """Session-scoped, not function-scoped: several existing fixtures
    that call classify_listing()/classify_product_type()/resolve_pair()
    without mocking the AI boundary are themselves module-scoped (e.g.
    test_classification_pipeline.py's `report`) -- a function-scoped
    autouse fixture runs AFTER a module-scoped fixture it shares a test
    with (broader scope always sets up first), so it would never clear
    the key in time. Session scope guarantees this runs before anything
    else. Uses pytest.MonkeyPatch() directly (not the function-scoped
    `monkeypatch` fixture, which a session fixture cannot depend on)."""
    mp = pytest.MonkeyPatch()
    mp.delenv("ANTHROPIC_API_KEY", raising=False)
    mp.delenv("GEMINI_API_KEY", raising=False)          # the knowledge LLM tier can use either provider
    yield
    mp.undo()
