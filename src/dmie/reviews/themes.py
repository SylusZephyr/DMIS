"""Stages 2-3: pain-point taxonomy loading, validation, and theme
normalization.

The controlled taxonomy (config/review_taxonomy.yaml) exists specifically
so extraction can't invent an unbounded number of complaint categories —
every extracted insight must map onto one of these fixed category/
subcategory pairs, or be rejected.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_TAXONOMY_PATH = _PROJECT_ROOT / "config" / "review_taxonomy.yaml"


def load_taxonomy() -> dict[str, list[str]]:
    return yaml.safe_load(_TAXONOMY_PATH.read_text(encoding="utf-8"))["taxonomy"]


def validate_taxonomy_path(category: str | None, subcategory: str | None) -> bool:
    if not category or not subcategory:
        return False
    taxonomy = load_taxonomy()
    return category in taxonomy and subcategory in taxonomy[category]


def normalize_theme_key(category: str, subcategory: str) -> str:
    """Canonical key for grouping/aggregation, e.g. 'QUALITY/durability'."""
    return f"{category}/{subcategory}"
