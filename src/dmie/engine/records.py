"""Universal MarketRecord schema (Module 1).

Every source -- SellerSprite, a generic CSV, an API response -- is
converted into a DataFrame with exactly these columns. Unmapped source
columns are preserved in ``attributes`` (a dict per row) so nothing is
lost.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

TEXT_FIELDS = ["id", "title", "brand", "image", "seller", "category", "url", "description", "review_text"]
NUMERIC_FIELDS = ["price", "sales", "revenue", "rating", "reviews", "bsr"]
UNIVERSAL_FIELDS = [
    "id", "title", "brand", "price", "sales", "revenue", "rating", "reviews", "bsr",
    "image", "seller", "category", "url", "description", "review_text", "timestamp", "launch_date",
]
RECORD_COLUMNS = ["record_id", *UNIVERSAL_FIELDS, "source", "attributes"]


@dataclass
class MarketRecord:
    record_id: str
    id: str | None = None
    title: str | None = None
    brand: str | None = None
    price: float | None = None
    sales: float | None = None
    revenue: float | None = None
    rating: float | None = None
    reviews: float | None = None
    bsr: float | None = None                  # best-sellers rank (lower = sells more); category-wide, one reading
    image: str | None = None
    seller: str | None = None
    category: str | None = None
    url: str | None = None
    description: str | None = None
    review_text: str | None = None
    timestamp: Any = None
    launch_date: Any = None
    source: str | None = None
    attributes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def make_record_id(source: str, native_id: Any, row_index: int, timestamp: Any = None) -> str:
    """Deterministic id: the same source row always maps to the same id."""
    key = f"{source}|{native_id if native_id not in (None, '') else f'row{row_index}'}|{timestamp or ''}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in RECORD_COLUMNS})


def records_to_frame(records: list[MarketRecord]) -> pd.DataFrame:
    if not records:
        return empty_frame()
    return pd.DataFrame([r.to_dict() for r in records], columns=RECORD_COLUMNS)
