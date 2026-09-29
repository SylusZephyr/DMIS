"""Keepa (api.keepa.com): Amazon search, product details and full history -- the licensed backbone for live data.

Contract used (Keepa product object):
* ``csv[i]`` are flat histories ``[t0, v0, t1, v1, ...]`` in Keepa minutes; unix seconds = (t + 21564000) * 60.
  i = 0 Amazon price, 1 new (3rd-party) price, 3 sales rank, 16 rating x10, 17 review count; prices in cents,
  -1 = no offer at that time.
* ``monthlySold`` / ``monthlySoldHistory``: Amazon's "bought in past month" badge value (a floor on a fixed
  ladder), current and as a ``[t, v, ...]`` history.
* ``packageWeight`` grams; ``fbaFees.pickAndPackFee`` cents; ``categoryTree`` root -> leaf; ``listedSince`` /
  ``trackingSince`` Keepa minutes.
The response's ``tokensLeft`` is checked: at 0 the provider stops instead of queueing paid requests.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timezone

from dip.acquire.base import AcquireError, Provider, config, env
from dip.acquire.models import HistoryPoint, Listing

KEEPA_EPOCH_OFFSET_MIN = 21564000
AMAZON, NEW, SALES_RANK, RATING, COUNT_REVIEWS = 0, 1, 3, 16, 17
IMAGE_BASE = "https://m.media-amazon.com/images/I/"


def keepa_time(t: int) -> datetime:
    return datetime.fromtimestamp((int(t) + KEEPA_EPOCH_OFFSET_MIN) * 60, tz=timezone.utc)


def series(flat: list | None) -> list[tuple[datetime, float]]:
    """Pairs from a flat Keepa history; -1 (no value) is kept as None-equivalent by the callers."""
    if not flat:
        return []
    return [(keepa_time(flat[i]), flat[i + 1]) for i in range(0, len(flat) - 1, 2)]


def value_at(s: list[tuple[datetime, float]], t: datetime) -> float | None:
    """The last value recorded at or before ``t`` (histories are step functions)."""
    v = None
    for ts, x in s:
        if ts > t:
            break
        v = x
    return None if v is None or v < 0 else v


def _last(s: list[tuple[datetime, float]]) -> float | None:
    for _, x in reversed(s):
        if x is not None and x >= 0:
            return x
    return None


def _cents(v: float | None) -> float | None:
    return None if v is None or v < 0 else round(v / 100.0, 2)


def month_ends(n: int, today: date | None = None) -> list[date]:
    """The last n complete month ends before ``today``, oldest first."""
    d = today or datetime.now(timezone.utc).date()
    y, m = d.year, d.month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(date(y, m, calendar.monthrange(y, m)[1]))
    return list(reversed(out))


class Keepa(Provider):
    name = "keepa"
    capabilities = ("amazon_search", "amazon_detail", "amazon_history")

    @property
    def _base(self) -> str:
        return config()["providers"]["keepa"]["base_url"].rstrip("/")

    def _get(self, path: str, params: dict) -> dict:
        key = env("DIP_KEEPA_API_KEY")
        data = self.http.get_json(f"{self._base}/{path}", {"key": key, "domain": config()["keepa_domain"], **params})
        if isinstance(data, dict) and data.get("error"):
            raise AcquireError(f"keepa: {data['error'].get('message') if isinstance(data['error'], dict) else data['error']}")
        if isinstance(data, dict) and data.get("tokensLeft") is not None and int(data["tokensLeft"]) <= 0:
            self.ledger.errors.append("keepa: token balance exhausted; remaining requests skipped")
        return data

    # ---------------------------------------------------------------- capabilities
    def search(self, term: str, page: int = 0) -> list[str]:
        data = self._get("search", {"type": "product", "term": term, "page": page})
        return [str(a) for a in (data.get("asinList") or [])]

    def products(self, asins: list[str]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(asins), 100):                 # Keepa accepts up to 100 ASINs per request
            chunk = asins[i:i + 100]
            data = self._get("product", {"asin": ",".join(chunk), "stats": 90, "rating": 1, "history": 1})
            out += data.get("products") or []
        return out

    def details(self, asins: list[str]) -> list[Listing]:
        return [self.listing(p) for p in self.products(asins) if p.get("asin")]

    # ---------------------------------------------------------------- normalization
    @staticmethod
    def listing(p: dict) -> Listing:
        csv = p.get("csv") or []
        get = lambda i: series(csv[i]) if len(csv) > i and csv[i] else []   # noqa: E731
        price = _cents(_last(get(NEW))) or _cents(_last(get(AMAZON)))
        rating = _last(get(RATING))
        imgs = (p.get("imagesCSV") or "").split(",")
        tree = p.get("categoryTree") or []
        since = p.get("listedSince") or p.get("trackingSince")
        ms = p.get("monthlySold")
        fees = p.get("fbaFees") or {}
        w = p.get("packageWeight")
        return Listing(
            asin=str(p["asin"]), title=p.get("title"), brand=p.get("brand"), price=price,
            sales=float(ms) if ms and ms > 0 else None,
            rating=round(rating / 10.0, 1) if rating is not None else None,
            reviews=int(v) if (v := _last(get(COUNT_REVIEWS))) is not None else None,
            image=f"{IMAGE_BASE}{imgs[0]}" if imgs and imgs[0] else None,
            url=f"https://www.amazon.com/dp/{p['asin']}",
            category=tree[-1].get("name") if tree else None,
            seller=p.get("manufacturer"),
            launch_date=keepa_time(since).date() if since and since > 0 else None,
            bsr=int(v) if (v := _last(get(SALES_RANK))) is not None else None,
            fba_fee=_cents(fees.get("pickAndPackFee")) if isinstance(fees, dict) else None,
            package_weight_g=float(w) if w and w > 0 else None,
            source="keepa")

    @staticmethod
    def history(p: dict, months: list[date]) -> list[HistoryPoint]:
        """Values as of each month end, from the product's histories."""
        csv = p.get("csv") or []
        get = lambda i: series(csv[i]) if len(csv) > i and csv[i] else []   # noqa: E731
        new, amz, rank, rat, cnt = get(NEW), get(AMAZON), get(SALES_RANK), get(RATING), get(COUNT_REVIEWS)
        sold = series(p.get("monthlySoldHistory"))
        out = []
        for m in months:
            t = datetime(m.year, m.month, m.day, 23, 59, tzinfo=timezone.utc)
            price = _cents(value_at(new, t)) or _cents(value_at(amz, t))
            r = value_at(rat, t)
            s = value_at(sold, t)
            out.append(HistoryPoint(asin=str(p["asin"]), month=m, price=price, sales=float(s) if s else None,
                                    rating=round(r / 10.0, 1) if r is not None else None,
                                    reviews=int(v) if (v := value_at(cnt, t)) is not None else None,
                                    bsr=int(v) if (v := value_at(rank, t)) is not None else None))
        return out
