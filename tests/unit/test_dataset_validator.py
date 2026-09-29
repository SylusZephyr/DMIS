"""Milestone 16 -- Dataset Validation Engine. Every check mirrors a rule
normalize.py/validation.py already enforce downstream (see
dataset_validator.py's own docstring) -- these tests pin the PASS/
WARNING/FAIL severity each real-world problem should produce.
"""

import pandas as pd

from dmie.validation.dataset_validator import (
    SEVERITY_FAIL,
    SEVERITY_PASS,
    SEVERITY_WARNING,
    ValidationReport,
    validate_raw_dataframe,
)

RAW_COLUMNS = ["ASIN", "商品标题", "品牌", "商品详情页链接", "商品主图",
               "价格($)", "评分", "子体销量", "子体销售额($)"]


def _clean_row(**overrides) -> dict:
    row = {
        "ASIN": "B000000001", "商品标题": "Test Product", "品牌": "TestBrand",
        "商品详情页链接": "https://amazon.com/dp/B000000001", "商品主图": "https://img/1.jpg",
        "价格($)": 19.99, "评分": 4.5, "子体销量": 100, "子体销售额($)": 1999.0,
    }
    row.update(overrides)
    return row


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=RAW_COLUMNS)


def test_clean_dataframe_passes_every_check():
    df = _df([_clean_row(), _clean_row(ASIN="B000000002")])
    report = validate_raw_dataframe(df)
    assert report.status == SEVERITY_PASS
    assert report.row_count == 2
    assert all(c.severity == SEVERITY_PASS for c in report.checks)


def test_missing_column_is_a_fail_and_short_circuits_row_checks():
    df = _df([_clean_row()]).drop(columns=["价格($)"])
    report = validate_raw_dataframe(df)
    assert report.status == SEVERITY_FAIL
    schema_check = next(c for c in report.checks if c.name == "schema_mismatch")
    assert schema_check.severity == SEVERITY_FAIL
    assert "价格($)" in schema_check.details
    # Nothing else can be meaningfully checked once a core column is gone.
    assert len(report.checks) == 1


def test_missing_asin_is_a_fail():
    df = _df([_clean_row(ASIN=None)])
    report = validate_raw_dataframe(df)
    assert report.status == SEVERITY_FAIL
    check = next(c for c in report.checks if c.name == "missing_asin")
    assert check.severity == SEVERITY_FAIL


def test_duplicate_asin_is_a_warning_not_a_fail():
    df = _df([_clean_row(), _clean_row()])  # same ASIN twice
    report = validate_raw_dataframe(df)
    assert report.status == SEVERITY_WARNING
    check = next(c for c in report.checks if c.name == "duplicate_asins")
    assert check.severity == SEVERITY_WARNING
    assert "B000000001" in check.details


def test_missing_price_is_a_warning():
    df = _df([_clean_row(**{"价格($)": None})])
    report = validate_raw_dataframe(df)
    check = next(c for c in report.checks if c.name == "missing_values_price")
    assert check.severity == SEVERITY_WARNING


def test_invalid_price_zero_or_negative_is_a_warning():
    df = _df([_clean_row(**{"价格($)": -5.0}), _clean_row(ASIN="B2", **{"价格($)": 0})])
    report = validate_raw_dataframe(df)
    check = next(c for c in report.checks if c.name == "invalid_prices")
    assert check.severity == SEVERITY_WARNING
    assert "2 row" in check.message


def test_invalid_rating_out_of_range_is_a_warning():
    df = _df([_clean_row(评分=5.5), _clean_row(ASIN="B2", 评分=-1)])
    report = validate_raw_dataframe(df)
    check = next(c for c in report.checks if c.name == "invalid_ratings")
    assert check.severity == SEVERITY_WARNING
    assert "2 row" in check.message


def test_missing_image_is_a_warning():
    df = _df([_clean_row(商品主图=None)])
    report = validate_raw_dataframe(df)
    check = next(c for c in report.checks if c.name == "missing_images")
    assert check.severity == SEVERITY_WARNING


def test_overall_status_is_the_worst_severity_present():
    # FAIL (missing ASIN) beats WARNING (duplicate ASIN) when both are present.
    df = _df([_clean_row(ASIN=None), _clean_row(ASIN="B2"), _clean_row(ASIN="B2")])
    report = validate_raw_dataframe(df)
    assert report.status == SEVERITY_FAIL
    severities = {c.name: c.severity for c in report.checks}
    assert severities["missing_asin"] == SEVERITY_FAIL
    assert severities["duplicate_asins"] == SEVERITY_WARNING


def test_report_to_dict_round_trips():
    df = _df([_clean_row()])
    report = validate_raw_dataframe(df)
    data = report.to_dict()
    assert data["status"] == SEVERITY_PASS
    assert data["row_count"] == 1
    assert isinstance(data["checks"], list)


# --- Tier 1: aggregate quality score (Milestone 1's "Dataset profile") ---

def test_quality_score_is_100_for_a_fully_clean_dataset():
    df = _df([_clean_row(), _clean_row(ASIN="B2")])
    report = validate_raw_dataframe(df)
    assert report.quality_score == 100.0


def test_quality_score_is_0_for_a_fail():
    df = _df([_clean_row(ASIN=None)])
    report = validate_raw_dataframe(df)
    assert report.status == SEVERITY_FAIL
    assert report.quality_score == 0.0


def test_quality_score_averages_across_all_8_dimensions_not_just_triggered_ones():
    """One dimension (price) is 100% affected on a 1-row dataset -- the
    other 7 row-level dimensions are clean. The score should reflect
    1 bad dimension out of 8, not treat the triggered check as the only
    thing that matters (which would tank the score far more)."""
    df = _df([_clean_row(**{"价格($)": None})])
    report = validate_raw_dataframe(df)
    assert report.status == SEVERITY_WARNING
    # 1/8 dimensions fully affected -> 100 - (1/8 * 100) = 87.5
    assert report.quality_score == 87.5


def test_quality_score_scales_with_fraction_of_rows_affected():
    # 1 of 2 rows missing price -> that dimension is 50% affected.
    df = _df([_clean_row(**{"价格($)": None}), _clean_row(ASIN="B2")])
    report = validate_raw_dataframe(df)
    # 50% of 1/8 dimensions -> 100 - (0.5/8 * 100) = 93.75 -> rounds to 93.8
    assert report.quality_score == 93.8


def test_quality_score_is_present_in_to_dict_and_survives_from_dict_round_trip():
    df = _df([_clean_row(**{"价格($)": None})])
    report = validate_raw_dataframe(df)
    data = report.to_dict()
    assert data["quality_score"] == report.quality_score

    restored = ValidationReport.from_dict(data)
    assert restored.quality_score == report.quality_score
