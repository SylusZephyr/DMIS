from dmie.market.pricing import price_band, price_distribution, price_stats, representative_price


def test_price_stats_basic():
    stats = price_stats([10.0, 20.0, 30.0])
    assert stats["min_price"] == 10.0
    assert stats["max_price"] == 30.0
    assert stats["median_price"] == 20.0
    assert stats["count"] == 3


def test_price_stats_ignores_nulls():
    stats = price_stats([10.0, None, 30.0])
    assert stats["count"] == 2
    assert stats["median_price"] == 20.0


def test_price_stats_empty_when_no_data():
    stats = price_stats([None, None])
    assert stats["count"] == 0
    assert stats["min_price"] is None


def test_representative_price_uses_best_listing_price_when_available():
    best = {"listing_id": "A", "price": 15.0}
    assert representative_price(best, median_price=99.0) == 15.0


def test_representative_price_falls_back_to_median_when_no_best_listing():
    assert representative_price(None, median_price=20.0) == 20.0


def test_representative_price_falls_back_when_best_listing_has_no_price():
    best = {"listing_id": "A", "price": None}
    assert representative_price(best, median_price=20.0) == 20.0


def test_price_band_boundaries():
    assert price_band(19.99) == "<$20"
    assert price_band(20.0) == "$20-50"
    assert price_band(49.99) == "$20-50"
    assert price_band(250.0) == "$250+"
    assert price_band(1000.0) == "$250+"


def test_price_distribution_bands_sum_to_count():
    dist = price_distribution([8.99, 15.99, 59.0, 77.0, None])
    assert dist["count"] == 4
    assert sum(dist["bands"].values()) == 4
    assert dist["bands"]["<$20"] == 2
    assert dist["bands"]["$50-100"] == 2


def test_price_distribution_empty_when_no_data():
    dist = price_distribution([None, None])
    assert dist["count"] == 0
    assert dist["bands"] == {}
