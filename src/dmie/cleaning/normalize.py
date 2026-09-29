"""Deterministic normalization for raw SellerSprite listing records.

No AI/LLM involved — every transformation here is a pure function, so
results are reproducible (PRINCIPLES.md principle 4: deterministic
calculations). Every rejected or dropped value is captured as a Decision
so nothing is silently discarded (principles 6 and 8).
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from dmie.ingestion.schema import MARKETPLACE

_CONTROL_ARTIFACT_RE = re.compile(r"_x000[0-9A-Fa-f]_")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_whitespace(value) -> str | None:
    """Collapse whitespace, strip Excel control-char artifacts (e.g. _x000D_),
    and normalize blank/NaN input to None."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = _CONTROL_ARTIFACT_RE.sub(" ", str(value))
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text or None


def parse_numeric(value) -> float | None:
    """Robustly parse a numeric value from float/int/str input. Returns None
    for NaN, blank, or unparseable input rather than raising."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if isinstance(value, float) and math.isnan(value) else float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("$", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def make_listing_id(asin: str, marketplace: str = MARKETPLACE) -> str:
    """Stable, reproducible listing_id: same (marketplace, asin) always
    yields the same id, so re-ingestion is idempotent."""
    return hashlib.sha1(f"{marketplace}|{asin}".encode("utf-8")).hexdigest()


@dataclass
class Decision:
    entity_type: str
    entity_id: str
    decision_type: str
    old_value: str | None
    new_value: str | None
    reason: str
    actor: str = "normalization_pipeline"


@dataclass
class NormalizationResult:
    listings: list[dict]
    decisions: list[Decision] = field(default_factory=list)


def normalize_price(raw, asin: str, decisions: list[Decision]) -> float | None:
    value = parse_numeric(raw)
    if value is None:
        return None
    if value <= 0:
        decisions.append(Decision(
            entity_type="listing", entity_id=asin,
            decision_type="normalization_rejected_value",
            old_value=str(raw), new_value=None,
            reason="price must be > 0",
        ))
        return None
    return value


def normalize_rating(raw, asin: str, decisions: list[Decision]) -> float | None:
    value = parse_numeric(raw)
    if value is None:
        return None
    if not (0 <= value <= 5):
        decisions.append(Decision(
            entity_type="listing", entity_id=asin,
            decision_type="normalization_rejected_value",
            old_value=str(raw), new_value=None,
            reason="rating out of valid range [0, 5]",
        ))
        return None
    return value


def normalize_nonnegative(raw, field_name: str, asin: str, decisions: list[Decision]) -> float | None:
    value = parse_numeric(raw)
    if value is None:
        return None
    if value < 0:
        decisions.append(Decision(
            entity_type="listing", entity_id=asin,
            decision_type="normalization_rejected_value",
            old_value=str(raw), new_value=None,
            reason=f"{field_name} must be >= 0",
        ))
        return None
    return value


def normalize_dataframe(
    df: pd.DataFrame, raw_source_file: str, marketplace: str = MARKETPLACE, category_id: str | None = None
) -> NormalizationResult:
    decisions: list[Decision] = []
    seen_asins: set[str] = set()
    listings: list[dict] = []
    now = datetime.now(timezone.utc)

    for idx, row in df.iterrows():
        raw_asin = normalize_whitespace(row.get("ASIN"))

        if not raw_asin:
            decisions.append(Decision(
                entity_type="listing", entity_id=f"row_{idx}",
                decision_type="row_excluded",
                old_value=str(row.to_dict()), new_value=None,
                reason="missing ASIN",
            ))
            continue

        if raw_asin in seen_asins:
            decisions.append(Decision(
                entity_type="listing", entity_id=raw_asin,
                decision_type="duplicate_asin_dropped",
                old_value=str(row.to_dict()), new_value=None,
                reason="duplicate ASIN — kept first occurrence "
                       "(see docs/data_dictionary.md open question)",
            ))
            continue
        seen_asins.add(raw_asin)

        price = normalize_price(row.get("价格($)"), raw_asin, decisions)
        rating = normalize_rating(row.get("评分"), raw_asin, decisions)
        monthly_sales = normalize_nonnegative(row.get("子体销量"), "monthly_sales", raw_asin, decisions)
        monthly_revenue = normalize_nonnegative(row.get("子体销售额($)"), "monthly_revenue", raw_asin, decisions)

        listings.append({
            "listing_id": make_listing_id(raw_asin, marketplace),
            "asin": raw_asin,
            "category_id": category_id,
            "title": normalize_whitespace(row.get("商品标题")),
            "brand": normalize_whitespace(row.get("品牌")),
            "url": normalize_whitespace(row.get("商品详情页链接")),
            "image_url": normalize_whitespace(row.get("商品主图")),
            "price": price,
            "monthly_sales": monthly_sales,
            "monthly_revenue": monthly_revenue,
            "rating": rating,
            # No review-count column exists in this export — see data_dictionary.md.
            "review_count": None,
            "raw_source_file": raw_source_file,
            "created_at": now,
        })

    return NormalizationResult(listings=listings, decisions=decisions)
