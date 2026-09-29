"""Stages 4-5: deterministic frequency and severity aggregation.

No LLM performs this arithmetic — PRINCIPLES.md principle 4. Operates on
plain dicts (e.g. ReviewInsight.__dict__) so it doesn't need a DB
connection to be tested.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict


def _theme_key(insight: dict) -> str:
    return f"{insight['pain_point_category']}/{insight['pain_point_subcategory']}"


def theme_frequency(insights: list[dict]) -> dict[str, int]:
    """Count of successfully extracted insights per taxonomy theme.
    Rejected/unavailable/no-pain-point insights are excluded — they
    aren't genuine pain-point observations."""
    counter = Counter(
        _theme_key(insight) for insight in insights if insight.get("status") == "extracted"
    )
    return dict(counter)


def theme_severity(insights: list[dict]) -> dict[str, dict]:
    """mean/max/count severity per theme, among extracted insights that
    have a severity value."""
    by_theme: dict[str, list[int]] = defaultdict(list)
    for insight in insights:
        if insight.get("status") != "extracted" or insight.get("severity") is None:
            continue
        by_theme[_theme_key(insight)].append(insight["severity"])

    return {
        theme: {
            "mean_severity": statistics.mean(values),
            "max_severity": max(values),
            "count": len(values),
        }
        for theme, values in by_theme.items()
    }


def rejection_summary(insights: list[dict]) -> dict[str, int]:
    """Count of insights by non-extracted status — visibility into how
    much was rejected/unavailable, not just what succeeded."""
    return dict(Counter(insight["status"] for insight in insights if insight.get("status") != "extracted"))
