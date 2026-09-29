"""Evidence-based opportunity signals — NOT an AI verdict.

The system never asks an LLM "is this a good opportunity" and reports the
answer. Every signal here is a deterministic combination of
already-computed, already-explainable metrics (market engine, Milestone
7; review intelligence, Milestone 8): specific, inspectable conditions,
each with its own actual value and threshold attached — not a single
opaque score. See docs/opportunity_signals.md.

Signal types (a named, closed taxonomy — never an open-ended free-text
label, for the same reason review pain points use a controlled taxonomy):

  PRODUCT_IMPROVEMENT           strong demand + many listings + high complaints
  BUNDLE                        strong core demand + accessory activity + limited bundle offerings
  PRICE_SEGMENT                 a price band with ~no products, flanked by bands that do have them
  UNDERREPRESENTED_PRODUCT_TYPE a product_type with disproportionately few products for its demand
  CUSTOMER_PAIN_POINT           a single taxonomy theme with high frequency/severity, standalone
  COMPETITIVE_CONCENTRATION     category listing concentration (HHI) is above a standard threshold

PRICE_SEGMENT, UNDERREPRESENTED_PRODUCT_TYPE, and COMPETITIVE_CONCENTRATION
describe a gap or structural property of the *category*, not an existing
product — their `product_id` is None by design (see docs/opportunity_signals.md).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

_BUNDLE_KEYWORDS_RE = re.compile(
    r"\bbundle\b|\bcombo\b|\bvalue pack\b|\b\d-in-1\b|\+.{0,20}\baccessor",
    re.IGNORECASE,
)

SIGNAL_TYPES = (
    "PRODUCT_IMPROVEMENT",
    "BUNDLE",
    "PRICE_SEGMENT",
    "UNDERREPRESENTED_PRODUCT_TYPE",
    "CUSTOMER_PAIN_POINT",
    "COMPETITIVE_CONCENTRATION",
)

# HHI >= this is "highly concentrated" per the US DOJ/FTC Horizontal Merger
# Guidelines' standard threshold -- an external, defensible reference point
# rather than an invented number.
HHI_HIGH_CONCENTRATION_THRESHOLD = 2500


def looks_like_bundle(title: str | None) -> bool:
    """Heuristic keyword match for 'this listing is a core-product +
    accessories bundle', deliberately narrower than generic words like
    'kit' or 'set' (which in this dataset usually mean a single
    multi-component product, not a core-plus-accessory bundle — see
    docs/opportunity_signals.md limitations)."""
    return bool(_BUNDLE_KEYWORDS_RE.search(title or ""))


@dataclass
class SignalCondition:
    name: str
    met: bool | None  # None = not evaluated (insufficient data) -- never silently False
    value: float | int | None
    threshold: float | int | None
    description: str
    direction: str = "high"  # "high": met when value >= threshold; "low": met when value <= threshold

    @property
    def margin_ratio(self) -> float | None:
        """How far past the threshold this condition is, as a ratio >= 1.0
        when met. Feeds signal_strength. None when not met or not
        computable (e.g. a zero threshold)."""
        if self.met is not True or self.value is None or not self.threshold:
            return None
        if self.direction == "high":
            return self.value / self.threshold
        return self.threshold / self.value if self.value else None


@dataclass
class OpportunitySignal:
    signal_type: str
    category_id: str
    product_id: str | None
    conditions: list[SignalCondition] = field(default_factory=list)
    sample_size: int = 0
    confidence_reference_n: int = 10

    def __post_init__(self):
        assert self.signal_type in SIGNAL_TYPES, f"unknown signal_type: {self.signal_type}"

    @property
    def status(self) -> str:
        """Tri-state, not a boolean: a signal the system can't evaluate
        must never look identical to one it evaluated and rejected."""
        values = [c.met for c in self.conditions]
        if any(v is None for v in values):
            return "insufficient_data"
        return "signal_present" if all(values) else "signal_absent"

    @property
    def signal_strength(self) -> str | None:
        """LOW/MEDIUM/HIGH based on how far past its threshold each met
        condition is, averaged. Only meaningful once status is
        signal_present -- None otherwise (a signal that isn't present
        doesn't have a "strength", and one we can't evaluate certainly
        doesn't)."""
        if self.status != "signal_present":
            return None
        ratios = [c.margin_ratio for c in self.conditions if c.margin_ratio is not None]
        if not ratios:
            return "LOW"
        avg_ratio = sum(ratios) / len(ratios)
        if avg_ratio >= 2.0:
            return "HIGH"
        if avg_ratio >= 1.3:
            return "MEDIUM"
        return "LOW"

    @property
    def confidence(self) -> float:
        """How much data backs this finding (sample size), distinct from
        signal_strength (how far past threshold). 0.0 when the status is
        insufficient_data -- confidence in an unmeasured thing is 0, not
        undefined or defaulted upward."""
        if self.status == "insufficient_data" or not self.confidence_reference_n:
            return 0.0
        return min(1.0, self.sample_size / self.confidence_reference_n)


def price_coefficient_of_variation(prices: list[float | None]) -> float | None:
    """Std-dev / mean of price across a category's products. Low CoV =
    prices tightly clustered = a commodity market with little visible
    price segmentation."""
    values = [p for p in prices if p is not None]
    if len(values) < 2:
        return None
    mean = statistics.mean(values)
    if mean == 0:
        return None
    return statistics.stdev(values) / mean


def _demand_value(product_metrics: dict) -> float | None:
    """observed_monthly_sales preferred; observed_monthly_revenue as a
    documented fallback when sales data is unavailable but revenue is."""
    if product_metrics.get("best_listing_observed_monthly_sales") is not None:
        return product_metrics["best_listing_observed_monthly_sales"]
    return product_metrics.get("observed_monthly_revenue")


def is_high_demand(
    product_metrics: dict, category_demand_values: list[float | None], percentile: float, min_n: int
) -> SignalCondition:
    value = _demand_value(product_metrics)
    if value is None:
        return SignalCondition("high_demand", None, None, None,
                                "no observed sales or revenue data for this product")
    comparable = sorted(v for v in category_demand_values if v is not None)
    if len(comparable) < min_n:
        return SignalCondition(
            "high_demand", None, value, None,
            f"only {len(comparable)} products in category have demand data (need >= {min_n} for a meaningful comparison)",
        )
    threshold = comparable[min(int(len(comparable) * percentile), len(comparable) - 1)]
    return SignalCondition("high_demand", value >= threshold, value, threshold,
                            f"observed demand in the top {(1 - percentile) * 100:.0f}% of category products")


def has_many_listings(product_metrics: dict, threshold: int) -> SignalCondition:
    value = product_metrics["total_listing_count"]
    return SignalCondition("many_listings", value >= threshold, value, threshold,
                            f"total_listing_count >= {threshold}")


def has_high_complaint_frequency(
    insight_count: int, mean_severity: float | None, count_threshold: int, severity_threshold: float
) -> SignalCondition:
    if insight_count == 0:
        return SignalCondition("high_complaint_frequency", None, 0, count_threshold,
                                "no review insights extracted for this product yet")
    met = insight_count >= count_threshold or (mean_severity is not None and mean_severity >= severity_threshold)
    return SignalCondition("high_complaint_frequency", met, insight_count, count_threshold,
                            f">= {count_threshold} complaints OR mean severity >= {severity_threshold}")


def has_low_differentiation(category_price_cov: float | None, threshold: float) -> SignalCondition:
    if category_price_cov is None:
        return SignalCondition("low_differentiation", None, None, threshold,
                                "fewer than 2 products with price data in category", direction="low")
    return SignalCondition("low_differentiation", category_price_cov <= threshold, category_price_cov, threshold,
                            "price coefficient of variation across category products <= threshold "
                            "(prices tightly clustered = commodity market, little visible segmentation)",
                            direction="low")


def has_accessory_activity(accessory_listing_count: int, threshold: int) -> SignalCondition:
    return SignalCondition(
        "accessory_activity_proxy", accessory_listing_count >= threshold, accessory_listing_count, threshold,
        "count of ACCESSORY_ONLY-classified listings in the category -- a weak proxy for accessory "
        "demand, NOT real co-purchase/attach-rate data (see docs/opportunity_signals.md limitations)",
    )


def has_limited_bundle_offerings(bundle_listing_fraction: float, threshold: float) -> SignalCondition:
    return SignalCondition(
        "limited_bundled_offerings", bundle_listing_fraction <= threshold, bundle_listing_fraction, threshold,
        "fraction of category listings whose title reads as a bundle/combo <= threshold",
        direction="low",
    )


def detect_product_improvement_signal(
    product_metrics: dict, category_demand_values: list[float | None],
    insight_count: int, mean_severity: float | None, thresholds: dict,
) -> OpportunitySignal:
    conditions = [
        is_high_demand(product_metrics, category_demand_values, thresholds["demand_percentile"], thresholds["min_products_for_percentile"]),
        has_many_listings(product_metrics, thresholds["many_listings_threshold"]),
        has_high_complaint_frequency(insight_count, mean_severity, thresholds["high_complaint_count_threshold"], thresholds["high_complaint_severity_threshold"]),
    ]
    return OpportunitySignal("PRODUCT_IMPROVEMENT", product_metrics["category_id"], product_metrics["product_id"],
                              conditions, sample_size=insight_count, confidence_reference_n=10)


def detect_bundle_signal(
    product_metrics: dict, category_demand_values: list[float | None],
    accessory_listing_count: int, bundle_listing_fraction: float, thresholds: dict,
) -> OpportunitySignal:
    conditions = [
        is_high_demand(product_metrics, category_demand_values, thresholds["demand_percentile"], thresholds["min_products_for_percentile"]),
        has_accessory_activity(accessory_listing_count, thresholds["accessory_activity_threshold"]),
        has_limited_bundle_offerings(bundle_listing_fraction, thresholds["limited_bundle_offering_threshold"]),
    ]
    return OpportunitySignal("BUNDLE", product_metrics["category_id"], product_metrics["product_id"],
                              conditions, sample_size=accessory_listing_count, confidence_reference_n=5)


def detect_customer_pain_point_signal(
    category_id: str, product_id: str, theme: str, frequency: int, mean_severity: float | None, thresholds: dict,
) -> OpportunitySignal:
    """Standalone per-theme signal -- unlike PRODUCT_IMPROVEMENT, this
    doesn't require demand or listing-count conditions, just a
    significant complaint theme on its own."""
    conditions = [
        has_high_complaint_frequency(frequency, mean_severity, thresholds["high_complaint_count_threshold"], thresholds["high_complaint_severity_threshold"]),
    ]
    signal = OpportunitySignal("CUSTOMER_PAIN_POINT", category_id, product_id, conditions,
                                sample_size=frequency, confidence_reference_n=5)
    signal.conditions[0].name = f"high_complaint_frequency[{theme}]"
    return signal


def detect_price_segment_signal(
    category_id: str, band_label: str, band_count: int, total_priced_products: int, min_n: int,
) -> OpportunitySignal:
    """Category-level (product_id=None): flags a price band with ~no
    products while the category overall has enough priced products for
    the gap to be meaningful, rather than just sparse data everywhere."""
    if total_priced_products < min_n:
        condition = SignalCondition(
            f"empty_price_band[{band_label}]", None, band_count, 0,
            f"only {total_priced_products} priced products in category (need >= {min_n})",
            direction="low",
        )
    else:
        condition = SignalCondition(
            f"empty_price_band[{band_label}]", band_count <= 0, band_count, 0,
            f"band '{band_label}' has ~no products while the category has {total_priced_products} priced products overall "
            "-- a possible whitespace, not a confirmed opportunity (see docs/opportunity_signals.md)",
            direction="low",
        )
    return OpportunitySignal("PRICE_SEGMENT", category_id, None, [condition],
                              sample_size=total_priced_products, confidence_reference_n=10)


def detect_underrepresented_product_type_signal(
    category_id: str, product_type_distribution: dict[str, int], unclassified_fraction_threshold: float = 0.5,
) -> OpportunitySignal:
    """Category-level (product_id=None). Requires real product_type data
    to mean anything -- if most products are 'unclassified' (true for
    this project until product-type classification is built, see
    Milestone 5's PROGRESS.md), this honestly reports insufficient_data
    rather than fabricating a finding from absent data."""
    total = sum(product_type_distribution.values())
    unclassified = product_type_distribution.get("unclassified", 0)
    unclassified_fraction = (unclassified / total) if total else 1.0

    if total == 0 or unclassified_fraction >= unclassified_fraction_threshold:
        condition = SignalCondition(
            "product_type_classified_fraction", None, 1 - unclassified_fraction, 1 - unclassified_fraction_threshold,
            f"{unclassified}/{total} products are unclassified -- product-type classification "
            "hasn't been built yet (Milestone 5), not enough real type data to find an underrepresented type",
        )
    else:
        condition = SignalCondition(
            "product_type_classified_fraction", True, 1 - unclassified_fraction, 1 - unclassified_fraction_threshold,
            "enough classified products to compare type representation",
        )
    return OpportunitySignal("UNDERREPRESENTED_PRODUCT_TYPE", category_id, None, [condition],
                              sample_size=total, confidence_reference_n=10)


def detect_competitive_concentration_signal(category_id: str, listing_concentration_hhi: float | None, total_listings: int) -> OpportunitySignal:
    """Category-level (product_id=None). Uses the standard US DOJ/FTC
    Horizontal Merger Guidelines HHI threshold (2500 = "highly
    concentrated") as an external, defensible reference point rather than
    an invented number."""
    if listing_concentration_hhi is None:
        condition = SignalCondition("listing_concentration_hhi", None, None, HHI_HIGH_CONCENTRATION_THRESHOLD,
                                     "no listings to compute concentration from")
    else:
        condition = SignalCondition(
            "listing_concentration_hhi", listing_concentration_hhi >= HHI_HIGH_CONCENTRATION_THRESHOLD,
            listing_concentration_hhi, HHI_HIGH_CONCENTRATION_THRESHOLD,
            "HHI >= 2500 (US DOJ/FTC 'highly concentrated' threshold) over listing counts per product",
        )
    return OpportunitySignal("COMPETITIVE_CONCENTRATION", category_id, None, [condition],
                              sample_size=total_listings, confidence_reference_n=20)
