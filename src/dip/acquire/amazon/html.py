"""Amazon pages as HTML: parsers for search results and product pages (with the reviews they show), and the
direct-fetch provider that uses them.

The parsers are used by the direct provider and by anything that already has a page (a vendor returning raw
HTML, a saved page). Direct fetching is **disabled by default** (``direct_html.enabled``): Amazon's Conditions
of Use do not permit automated access, so it is an operator's explicit choice. When enabled it is slow on
purpose (``min_seconds_between_requests``), identifies itself, never signs in, and stops at the first
robot check instead of trying to get around it.
"""

from __future__ import annotations

import hashlib
import re

from bs4 import BeautifulSoup

from dip.acquire.amazon import parse
from dip.acquire.base import AcquireError, Provider, config
from dip.acquire.models import Listing, Review

ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})")


class Blocked(AcquireError):
    """Amazon answered with a robot check."""


def _text(el) -> str | None:
    return el.get_text(" ", strip=True) if el is not None else None


def _attr(el, name: str) -> str | None:
    """An attribute as text (bs4 returns str, a list for multi-valued attributes, or None)."""
    if el is None:
        return None
    v = el.get(name)
    if isinstance(v, list):
        v = " ".join(str(x) for x in v)
    return str(v) if v is not None else None


def check_blocked(html: str) -> None:
    low = html[:20000].lower()
    if "validatecaptcha" in low or "enter the characters you see below" in low or "api-services-support@amazon.com" in low:
        raise Blocked("amazon: robot check page -- stopping (not bypassed)")


def parse_search(html: str) -> list[Listing]:
    check_blocked(html)
    soup = BeautifulSoup(html, "html.parser")
    out: list[Listing] = []
    for card in soup.select('div[data-component-type="s-search-result"][data-asin]'):
        asin = (_attr(card, "data-asin") or "").strip()
        if not asin:
            continue
        title = _text(card.select_one("h2 span")) or _text(card.select_one("h2"))
        brand = _text(card.select_one("div[data-cy='title-recipe'] span.a-size-base-plus.a-color-base")) \
            or _text(card.select_one("h5 span"))
        price = parse.money(_text(card.select_one("span.a-price span.a-offscreen")))
        rating = parse.rating(_text(card.select_one("span.a-icon-alt")))
        count_el = card.select_one("span[aria-label$='ratings']") or card.select_one("a[href*='customerReviews'] span")
        reviews = parse.integer(_attr(count_el, "aria-label") or _text(count_el))
        bought = next((parse.bought_past_month(s.get_text(" ", strip=True)) for s in card.select("span")
                       if "bought in past month" in s.get_text(" ", strip=True).lower()), None)
        img = card.select_one("img.s-image")
        out.append(Listing(asin=asin, title=title, brand=brand, price=price, sales=bought, rating=rating, reviews=reviews,
                           image=_attr(img, "src"), url=f"https://www.amazon.com/dp/{asin}",
                           source="amazon_html"))
    return out


def _detail_rows(soup: BeautifulSoup) -> dict[str, str]:
    rows: dict[str, str] = {}
    for li in soup.select("#detailBullets_feature_div li, #detailBulletsWrapper_feature_div li"):
        t = _text(li) or ""
        if ":" in t:
            k, v = t.split(":", 1)
            rows[re.sub(r"[‎‏]", "", k).strip().lower()] = v.strip()
    for tr in soup.select("table.prodDetTable tr, #productDetails_detailBullets_sections1 tr, #productDetails_techSpec_section_1 tr"):
        th, td = tr.select_one("th"), tr.select_one("td")
        if th is not None and td is not None:
            rows[(_text(th) or "").strip().lower()] = _text(td) or ""
    return rows


def parse_product(html: str, asin: str | None = None) -> tuple[Listing, list[Review]]:
    check_blocked(html)
    soup = BeautifulSoup(html, "html.parser")
    if asin is None:
        canon = soup.select_one("link[rel='canonical']")
        m = ASIN_RE.search(_attr(canon, "href") or "") or ASIN_RE.search(html)
        asin = m.group(1) if m else None
    if not asin:
        raise AcquireError("amazon_html: product page without an ASIN")
    rows = _detail_rows(soup)
    byline = _text(soup.select_one("#bylineInfo")) or ""
    brand = re.sub(r"^(Visit the\s+|Brand:\s*)", "", byline).replace(" Store", "").strip() or None
    rating_el = soup.select_one("#acrPopover")
    bsr_txt = rows.get("best sellers rank")
    img = soup.select_one("#landingImage")
    crumbs = [x for x in (_text(a) for a in soup.select("#wayfinding-breadcrumbs_feature_div a")) if x]
    listing = Listing(
        asin=asin, title=_text(soup.select_one("#productTitle")), brand=brand,
        price=parse.money(_text(soup.select_one("#corePrice_feature_div span.a-offscreen"))
                          or _text(soup.select_one("span.a-price span.a-offscreen"))),
        sales=parse.bought_past_month(_text(soup.select_one("#social-proofing-faceout-title-tk_bought"))),
        rating=parse.rating(_attr(rating_el, "title")),
        reviews=parse.integer(_text(soup.select_one("#acrCustomerReviewText"))),
        image=_attr(img, "data-old-hires") or _attr(img, "src"),
        url=f"https://www.amazon.com/dp/{asin}", category=crumbs[-1] if crumbs else None,
        launch_date=parse.any_date(rows.get("date first available")),
        bsr=parse.integer(bsr_txt) if bsr_txt else None,
        package_weight_g=parse.weight_grams(rows.get("item weight") or rows.get("package dimensions")
                                            or rows.get("product dimensions")),
        source="amazon_html")
    reviews = []
    for el in soup.select("[data-hook='review']"):
        body = _text(el.select_one("[data-hook='review-body']"))
        if not body:
            continue
        title_el = el.select_one("[data-hook='review-title']")
        title_spans = title_el.select("span") if title_el is not None else []
        title = _text(title_spans[-1]) if title_spans else _text(title_el)
        star = el.select_one("[data-hook='review-star-rating'], [data-hook='cmps-review-star-rating']")
        reviews.append(Review(
            asin=asin, review_id=_attr(el, "id") or hashlib.sha1(body.encode()).hexdigest()[:16],
            rating=parse.rating(_text(star)), title=title, text=body,
            date=parse.any_date(_text(el.select_one("[data-hook='review-date']"))),
            verified=el.select_one("[data-hook='avp-badge']") is not None,
            helpful=parse.integer(_text(el.select_one("[data-hook='helpful-vote-statement']"))) or 0,
            source="amazon_html"))
    return listing, reviews


class AmazonHTML(Provider):
    name = "amazon_html"
    capabilities = ("amazon_search", "amazon_detail", "amazon_reviews")

    @classmethod
    def missing(cls) -> list[str]:
        return [] if config()["direct_html"]["enabled"] else ["direct_html.enabled (config/platform/acquisition.yaml)"]

    def _page(self, url: str, params: dict | None = None) -> str:
        dh = config()["direct_html"]
        return self.http.get_text(url, params, headers={"User-Agent": dh["user_agent"], "Accept-Language": "en-US,en;q=0.8"},
                                  min_gap=float(dh["min_seconds_between_requests"]))

    def search_listings(self, term: str, page: int = 1) -> list[Listing]:
        return parse_search(self._page("https://www.amazon.com/s", {"k": term, "page": page}))

    def search(self, term: str, page: int = 0) -> list[str]:
        return [x.asin for x in self.search_listings(term, page + 1)]

    def product(self, asin: str) -> tuple[Listing, list[Review]]:
        return parse_product(self._page(f"https://www.amazon.com/dp/{asin}"), asin)

    def details(self, asins: list[str]) -> list[Listing]:
        return [self.product(a)[0] for a in asins]

    def reviews(self, asin: str, limit: int = 30) -> list[Review]:
        # only the reviews shown on the public product page (the full list requires sign-in)
        return self.product(asin)[1][:limit]
