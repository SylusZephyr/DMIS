import pytest

from dmie.market.competition import (
    herfindahl_hirschman_index,
    listing_concentration,
    product_type_distribution,
)


def test_hhi_perfectly_unconcentrated():
    # 10 products, 1 listing each -> each share = 0.1 -> HHI = 10 * 0.01 * 10000 = 1000
    assert herfindahl_hirschman_index([1] * 10) == pytest.approx(1000.0)


def test_hhi_fully_concentrated():
    # 1 product holds every listing -> share = 1.0 -> HHI = 10000
    assert herfindahl_hirschman_index([5]) == 10000.0


def test_hhi_none_when_no_listings():
    assert herfindahl_hirschman_index([]) is None
    assert herfindahl_hirschman_index([0, 0]) is None


def test_hhi_matches_hand_computed_example():
    # docs/market_metrics.md worked example: shares 2,1,1,1,1,1,1,1,1,1,1 (12 listings, 11 products)
    counts = [2] + [1] * 10
    hhi = herfindahl_hirschman_index(counts)
    expected = ((2 / 12) ** 2 + 10 * (1 / 12) ** 2) * 10000
    assert hhi == pytest.approx(expected)


def test_listing_concentration_top_share():
    result = listing_concentration([5, 3, 2])
    assert result["top_product_listing_share"] == 0.5
    assert result["hhi"] is not None


def test_listing_concentration_empty_input():
    result = listing_concentration([])
    assert result["top_product_listing_share"] is None
    assert result["hhi"] is None


def test_product_type_distribution_counts_unclassified_explicitly():
    dist = product_type_distribution([None, "wax", None, "wax", "resin"])
    assert dist == {"unclassified": 2, "wax": 2, "resin": 1}


def test_product_type_distribution_all_missing():
    dist = product_type_distribution([None, None])
    assert dist == {"unclassified": 2}
