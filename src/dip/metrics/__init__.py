"""Metrics engine v3 (Master Prompt 5): statistically grounded, category-agnostic market metrics.

Every metric returns value, interval, n, unit and an explanation; constants live in
config/platform/metrics.yaml; methods are documented in docs/METHODOLOGY.md and validated
against synthetic ground truth (dip.metrics.synthetic) in the test suite.
"""

from functools import lru_cache

import yaml

from dip.settings import PROJECT_ROOT


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load((PROJECT_ROOT / "config" / "platform" / "metrics.yaml").read_text(encoding="utf-8"))


def opportunity_thresholds() -> tuple[float, float]:
    """(moderate_from, high_from) on the opportunity index, from config."""
    op = config()["opportunity"]
    return float(op["levels"][0][0]), float(op["high_from"])
