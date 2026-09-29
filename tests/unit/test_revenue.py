from dmie.market.revenue import annualized_observed_revenue, total_observed_monthly_revenue


def test_total_observed_monthly_revenue_sums_across_listings():
    listings = [{"monthly_revenue": 449.0}, {"monthly_revenue": 100.0}]
    assert total_observed_monthly_revenue(listings) == 549.0


def test_total_observed_monthly_revenue_ignores_nulls():
    listings = [{"monthly_revenue": 449.0}, {"monthly_revenue": None}]
    assert total_observed_monthly_revenue(listings) == 449.0


def test_total_observed_monthly_revenue_none_when_no_data_at_all():
    """None, not 0 -- 'we don't know' must never silently become '$0'."""
    listings = [{"monthly_revenue": None}, {"monthly_revenue": None}]
    assert total_observed_monthly_revenue(listings) is None


def test_annualized_observed_revenue_multiplies_by_12():
    assert annualized_observed_revenue(449.0) == 5388.0


def test_annualized_observed_revenue_none_stays_none():
    assert annualized_observed_revenue(None) is None
