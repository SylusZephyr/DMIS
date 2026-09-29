"""What every Amazon provider returns, before it becomes a snapshot: listings, reviews and history points.

``to_snapshot_frame`` writes listings in the universal record layout the ingestion stage already reads
(id, title, brand, price, sales, rating, reviews, image, url, category, seller, launch_date) plus the fields
landed cost reads from ``attributes`` (fba_fee, package_weight) -- so a live snapshot is processed exactly
like an upload. ``sales`` holds Amazon's "bought in past month" badge value when the listing shows one
(50, 100, 200 ...); a listing without a badge has no value, which the demand model reads as "below the first
rung", never as zero.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime

import pandas as pd

SNAPSHOT_COLUMNS = ["id", "title", "brand", "price", "sales", "rating", "reviews", "image", "url", "category", "seller",
                    "launch_date", "bsr", "fba_fee", "package_weight", "acquired_via"]
REVIEW_COLUMNS = ["asin", "review_id", "rating", "title", "text", "date", "verified", "helpful", "source"]


@dataclass
class Listing:
    asin: str
    title: str | None = None
    brand: str | None = None
    price: float | None = None
    sales: float | None = None           # "bought in past month" badge floor, None when not shown
    rating: float | None = None
    reviews: int | None = None           # rating / review count
    image: str | None = None
    url: str | None = None
    category: str | None = None
    seller: str | None = None
    launch_date: date | None = None
    bsr: int | None = None
    fba_fee: float | None = None         # pick & pack fee, USD
    package_weight_g: float | None = None
    source: str = ""
    extra: dict = field(default_factory=dict)

    def merge(self, other: "Listing") -> "Listing":
        """Fill empty fields from another observation of the same ASIN (first source wins where both know)."""
        for k, v in asdict(other).items():
            if k in ("asin", "extra"):
                continue
            if getattr(self, k) in (None, "") and v not in (None, ""):
                setattr(self, k, v)
        self.extra = {**other.extra, **self.extra}
        if other.source and other.source not in self.source:
            self.source = f"{self.source}+{other.source}" if self.source else other.source
        return self


@dataclass
class Review:
    asin: str
    review_id: str
    rating: float | None
    title: str | None
    text: str
    date: date | None = None
    verified: bool | None = None
    helpful: int | None = None
    source: str = ""


@dataclass
class HistoryPoint:
    asin: str
    month: date                          # month end the values are "as of"
    price: float | None = None
    sales: float | None = None
    rating: float | None = None
    reviews: int | None = None
    bsr: int | None = None


def to_snapshot_frame(listings: list[Listing]) -> pd.DataFrame:
    rows = [{"id": x.asin, "title": x.title, "brand": x.brand, "price": x.price, "sales": x.sales, "rating": x.rating,
             "reviews": x.reviews, "image": x.image, "url": x.url or f"https://www.amazon.com/dp/{x.asin}",
             "category": x.category, "seller": x.seller,
             "launch_date": x.launch_date.isoformat() if isinstance(x.launch_date, (date, datetime)) else None,
             "bsr": x.bsr, "fba_fee": x.fba_fee, "package_weight": x.package_weight_g, "acquired_via": x.source}
            for x in listings if x.asin and x.title]
    return pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)


def to_review_frame(reviews: list[Review]) -> pd.DataFrame:
    return pd.DataFrame([{**asdict(r), "date": r.date.isoformat() if r.date else None} for r in reviews], columns=REVIEW_COLUMNS)


def history_snapshots(points: list[HistoryPoint], listings: dict[str, Listing]) -> dict[date, pd.DataFrame]:
    """Monthly snapshot frames rebuilt from history: one frame per month end, listing fields that do not change
    (title, brand, category, fees) from the current observation, the time-varying ones from history."""
    by_month: dict[date, list[dict]] = {}
    for p in points:
        cur = listings.get(p.asin)
        if cur is None or p.price is None:           # no price that month -> the listing was not buyable
            continue
        by_month.setdefault(p.month, []).append({
            "id": p.asin, "title": cur.title, "brand": cur.brand, "price": p.price, "sales": p.sales, "rating": p.rating,
            "reviews": p.reviews, "image": cur.image, "url": cur.url or f"https://www.amazon.com/dp/{p.asin}",
            "category": cur.category, "seller": cur.seller,
            "launch_date": cur.launch_date.isoformat() if isinstance(cur.launch_date, (date, datetime)) else None,
            "bsr": p.bsr, "fba_fee": cur.fba_fee, "package_weight": cur.package_weight_g,
            "acquired_via": f"{cur.source}:history"})
    return {m: pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS) for m, rows in sorted(by_month.items())}
