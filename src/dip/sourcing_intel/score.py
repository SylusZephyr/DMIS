"""Judge supplier offers against a concept, merge the same supplier across platforms, pick the best.

Per offer (every component 0-100, a component without data is left out -- never guessed -- and the coverage
is reported, as in the opportunity engine):

* **fit**          share of the concept's key words the offer title states (English words, or their Chinese
                   glossary terms in a Chinese title), times the must-have attribute share where the title
                   states the attribute; a negative term ("toy", "keychain") marks it as not a match.
* **margin**       at the concept's Amazon target price and the offer's tier price for the first-order quantity:
                   ``(price - referral - FBA fee - (unit + freight + duty)) / price``; 0 at break-even,
                   100 at 1.5 x the target margin. Freight from the offer's weight (else the scope's median
                   package weight, else the configured default) and landed-cost settings; duty from landed cost
                   (before duty when unset -- said so).
* **reliability**  years on the platform, verified factory, trade assurance, rating, repeat-buyer rate, sales,
                   response rate (mean of what the platform shows).
* **compliance**   share of the category's required certifications the offer states (config); a missing
                   statement means "verify with the supplier", never a pass.
* **moq_fit**      100 when the MOQ fits the first order, else first order / MOQ.

The **Pareto front** is taken over (landed unit cost, reliability, fit) among matching offers: an offer is
dominated when another is at least as good on all three and better on one. The recommended offer is the
highest-scoring non-dominated offer that leaves a positive margin (when margin is known).
Offers of one supplier on several platforms are merged into one supplier by normalized company name.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from dip.sourcing_intel.concept import Concept, _words, zh_for
from dip.sourcing_intel.platforms import Offer, config

_SUFFIX = re.compile(r"(co\.?,?\s*ltd\.?|company|limited|ltd\.?|inc\.?|corp\.?|corporation|technology|tech|trading|"
                     r"industrial|industry|manufactur\w*|medical|dental|products?|international|"
                     r"有限责任公司|股份有限公司|有限公司|科技|贸易|工贸|实业|制品|医疗器械|牙科|厂|公司|集团)", re.I)


def supplier_key(name: str | None, location: str | None = None) -> str | None:
    if not name:
        return None
    k = _SUFFIX.sub(" ", str(name).lower())
    k = re.sub(r"[\s\W_]+", "", k)
    return k or None


@dataclass
class Scored:
    offer: Offer
    fit: float
    fit_detail: dict
    landed_usd: float | None
    unit_usd: float | None
    freight_usd: float | None
    duty_usd: float | None
    margin: float | None
    components: dict
    score: float | None
    coverage: float
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    pareto: bool = False
    match: bool = True

    def to_dict(self) -> dict:
        o = self.offer
        return {"platform": o.platform, "offer_id": o.offer_id, "title": o.title, "title_en": o.title_en, "url": o.url,
                "image": o.image, "currency": o.currency, "price": o.price, "price_usd": o.usd(o.price),
                "tiers": [{"min_qty": t.min_qty, "price": t.price} for t in o.tiers], "moq": o.moq, "sold": o.sold,
                "supplier": o.supplier, "supplier_id": o.supplier_id, "location": o.location, "years": o.years,
                "verified": o.verified, "trade_assurance": o.trade_assurance, "rating": o.rating,
                "repurchase_rate": o.repurchase_rate, "certifications": o.certifications, "query": o.query,
                "fit": round(self.fit, 3), "fit_detail": self.fit_detail, "unit_usd": self.unit_usd,
                "freight_usd": self.freight_usd, "duty_usd": self.duty_usd, "landed_usd": self.landed_usd,
                "margin": None if self.margin is None else round(self.margin, 4),
                "components": {k: (None if v is None else round(v, 1)) for k, v in self.components.items()},
                "score": None if self.score is None else round(self.score, 1), "coverage": round(self.coverage, 2),
                "pareto": self.pareto, "match": self.match, "reasons": self.reasons, "flags": self.flags}


def fit(c: Concept, o: Offer) -> tuple[float, dict]:
    title = f"{o.title} {o.title_en or ''}".lower()
    terms = c.terms_en or _words(c.label)
    hit = []
    for w in terms:
        zh = zh_for([w])
        if re.search(rf"\b{re.escape(w)}", title) or any(z in title for z in zh):
            hit.append(w)
    share = len(hit) / len(terms) if terms else 0.0
    neg = [n for n in config()["negative_terms"] if str(n).lower() in title]
    attr = None
    if c.must_have and c.market:
        from dip.knowledge import attributes, needs

        got = attributes.extract_row({"title": o.title_en or o.title}, c.market)["attributes"]
        reqs = [{"attribute": m["attribute"], "op": "eq", "value": m["value"]} for m in c.must_have]
        st = [needs.check(r, got) for r in reqs]
        known = [x for x in st if x != "unknown"]
        attr = sum(x == "met" for x in known) / len(known) if known else None
    f = share if attr is None else 0.7 * share + 0.3 * attr
    if neg:
        f *= 0.2
    return f, {"terms_matched": hit, "terms": terms, "attribute_share": attr, "negative": neg}


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def economics(c: Concept, o: Offer) -> dict:
    from dip.knowledge import landed_cost as lc

    lcfg = lc.config()["landed_cost"]
    unit = o.usd(o.unit_price_at(c.first_order_qty))
    w = o.weight_g or c.unit_weight_g or float(config()["default_unit_weight_g"])
    freight = round(max(w / 1000.0, float(lcfg["min_billable_kg"])) * float(lcfg["freight_usd_per_kg"]), 4)
    duty_rate = lc.duty_rate(c.market)
    duty = round(unit * duty_rate, 4) if unit is not None and duty_rate is not None else None
    landed = round(unit + freight + (duty or 0.0), 4) if unit is not None else None
    margin = None
    if landed is not None and c.target_price and c.fba_fee is not None:
        referral = c.target_price * float(lcfg["referral_fee"])
        margin = (c.target_price - referral - c.fba_fee - landed) / c.target_price
    return {"unit": unit, "freight": freight, "duty": duty, "landed": landed, "margin": margin, "weight_g": w,
            "duty_rate": duty_rate, "weight_source": "offer" if o.weight_g else ("scope median" if c.unit_weight_g else "default")}


def reliability(o: Offer) -> tuple[float | None, list[str]]:
    parts, why = [], []
    if o.years is not None:
        parts.append(_clamp(o.years / 8.0))
        why.append(f"{o.years:.0f} years on platform")
    if o.verified is not None:
        parts.append(1.0 if o.verified else 0.0)
        why.append("verified factory" if o.verified else "not a verified factory")
    if o.trade_assurance is not None:
        parts.append(1.0 if o.trade_assurance else 0.0)
        if o.trade_assurance:
            why.append("trade assurance")
    if o.rating is not None:
        parts.append(_clamp((o.rating - 3.5) / 1.5))
        why.append(f"rating {o.rating:.1f}")
    if o.repurchase_rate is not None:
        parts.append(_clamp(o.repurchase_rate / 0.4))
        why.append(f"{o.repurchase_rate:.0%} repeat buyers")
    if o.sold is not None:
        parts.append(_clamp(math.log10(1 + o.sold) / 4.0))
        why.append(f"{o.sold:,} sold")
    if o.response_rate is not None:
        parts.append(_clamp(o.response_rate))
    return (100 * sum(parts) / len(parts) if parts else None), why


def compliance(c: Concept, o: Offer) -> tuple[float | None, list[str]]:
    if not c.required_certs:
        return None, []
    have = " ".join(o.certifications + [o.title, o.title_en or ""]).lower()
    missing = [r for r in c.required_certs if r.lower() not in have]
    return 100 * (1 - len(missing) / len(c.required_certs)), missing


def score_offer(c: Concept, o: Offer) -> Scored:
    w = config()["weights"]
    f, fd = fit(c, o)
    e = economics(c, o)
    rel, rel_why = reliability(o)
    comp, missing = compliance(c, o)
    moq = None if o.moq is None else 100 * min(1.0, c.first_order_qty / max(o.moq, 1))
    from dip.knowledge import landed_cost

    target = float(landed_cost.config()["landed_cost"]["target_margin"])
    mscore = None if e["margin"] is None else 100 * _clamp(e["margin"] / (1.5 * target))
    comps = {"fit": 100 * f, "margin": mscore, "reliability": rel, "compliance": comp, "moq_fit": moq}
    known = {k: v for k, v in comps.items() if v is not None}
    cov = sum(float(w[k]) for k in known)
    total = sum(float(w[k]) * v for k, v in known.items()) / cov if cov else None
    reasons, flags = [], []
    if fd["terms_matched"]:
        reasons.append(f"matches {', '.join(fd['terms_matched'][:5])}")
    if e["landed"] is not None:
        duty = "" if e["duty"] is None else f" + duty ${e['duty']:.2f}"
        reasons.append(f"landed ${e['landed']:.2f}/unit at {c.first_order_qty} units "
                       f"(unit ${e['unit']:.2f} + freight ${e['freight']:.2f}{duty})")
    if e["margin"] is not None:
        reasons.append(f"{e['margin']:.0%} margin at ${c.target_price:.2f} after fees")
    if c.max_fob is not None and e["unit"] is not None:
        (reasons if e["unit"] <= c.max_fob else flags).append(
            f"unit price {'within' if e['unit'] <= c.max_fob else 'above'} the ${c.max_fob:.2f} sourcing ceiling")
    reasons += rel_why[:3]
    if missing:
        flags.append(f"verify certifications: {', '.join(missing)}")
    if o.moq is not None and o.moq > c.first_order_qty:
        flags.append(f"MOQ {o.moq} above the first order of {c.first_order_qty}")
    if e["duty"] is None:
        flags.append("landed cost before duty (no duty rate set for this market)")
    if fd["negative"]:
        flags.append(f"looks like something else: {', '.join(fd['negative'])}")
    return Scored(offer=o, fit=f, fit_detail=fd, landed_usd=e["landed"], unit_usd=e["unit"], freight_usd=e["freight"],
                  duty_usd=e["duty"], margin=e["margin"], components=comps, score=total, coverage=cov, reasons=reasons,
                  flags=flags, match=f >= float(config()["run"]["min_fit"]) and not fd["negative"])


def pareto(rows: list[Scored]) -> None:
    """Mark non-dominated matching offers over (landed cost lower, reliability higher, fit higher)."""
    cand = [(r, float(r.landed_usd), float(r.components.get("reliability") or 0.0))
            for r in rows if r.match and r.landed_usd is not None]
    for r, cost, rel in cand:
        r.pareto = not any(
            (oc <= cost and orl >= rel and o.fit >= r.fit) and (oc < cost or orl > rel or o.fit > r.fit)
            for o, oc, orl in cand if o is not r)


def best(rows: list[Scored]) -> Scored | None:
    ok = [r for r in rows if r.pareto and r.score is not None and (r.margin is None or r.margin > 0)]
    ok = ok or [r for r in rows if r.match and r.score is not None]
    return max(ok, key=lambda r: r.score or 0.0) if ok else None


def suppliers(rows: list[Scored]) -> list[dict]:
    """One entry per supplier (merged across platforms), ranked by its best matching offer's score."""
    by: dict[str, list[Scored]] = {}
    for r in rows:
        k = supplier_key(r.offer.supplier) or f"{r.offer.platform}:{r.offer.supplier_id or r.offer.offer_id}"
        by.setdefault(k, []).append(r)
    out = []
    for k, rs in by.items():
        matching = [r for r in rs if r.match and r.score is not None]
        top = max(matching or rs, key=lambda r: r.score or -1)
        o = top.offer
        out.append({"key": k, "name": o.supplier or "(unnamed)", "platforms": sorted({r.offer.platform for r in rs}),
                    "offers": len(rs), "matching_offers": len(matching), "location": o.location,
                    "years": max((r.offer.years or 0) for r in rs) or None,
                    "verified": any(r.offer.verified for r in rs) if any(r.offer.verified is not None for r in rs) else None,
                    "certifications": sorted({c for r in rs for c in r.offer.certifications}),
                    "best_offer": top.to_dict(), "score": top.score if matching else None})
    return sorted(out, key=lambda s: (s["score"] is None, -(s["score"] or 0)))
