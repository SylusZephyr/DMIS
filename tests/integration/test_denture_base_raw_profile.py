"""Validates facts documented in docs/data_dictionary.md against the raw
SellerSprite export for the denture_base pilot category.

These tests pin known characteristics of the source file (including its
hash, to catch accidental modification of data/raw/) rather than testing
pipeline code, since no ingestion/cleaning code exists yet.
"""

import hashlib

import pandas as pd
import pytest

from dmie.database.connection import PROJECT_ROOT

RAW_PATH = PROJECT_ROOT / "data" / "raw" / "denture_base" / "denture_base_sellersprite.xlsx"
EXPECTED_SHA256 = "5771a9b3fe47006db153b5a84ae250ec73dc3833d05018ba4e792d100e8694b9"

EXPECTED_COLUMNS = [
    "二级类目", "ASIN", "品牌", "商品标题", "商品详情页链接", "商品主图", "小类目",
    "子体销量", "子体销售额($)", "价格($)", "评分", "FBA($)", "上架时间",
    "包装重量（单位换算）", "包装尺寸（单位换算）", "包装尺寸分段", "产品经理",
]


@pytest.fixture(scope="module")
def raw_df() -> pd.DataFrame:
    return pd.read_excel(RAW_PATH, sheet_name="Sheet1")


def test_raw_file_exists() -> None:
    assert RAW_PATH.exists()


def test_raw_file_is_unmodified() -> None:
    digest = hashlib.sha256(RAW_PATH.read_bytes()).hexdigest()
    assert digest == EXPECTED_SHA256, "data/raw/ file changed — raw exports must be immutable"


def test_shape(raw_df: pd.DataFrame) -> None:
    assert raw_df.shape == (106, 17)


def test_expected_columns_present(raw_df: pd.DataFrame) -> None:
    assert list(raw_df.columns) == EXPECTED_COLUMNS


def test_asin_mostly_unique_with_one_known_duplicate(raw_df: pd.DataFrame) -> None:
    dup_count = int(raw_df["ASIN"].duplicated().sum())
    assert dup_count == 1, "expected exactly the one known duplicate ASIN (B0FMK8XB26)"


def test_price_has_no_negative_or_zero_values(raw_df: pd.DataFrame) -> None:
    prices = raw_df["价格($)"].dropna()
    assert (prices > 0).all()


def test_rating_within_valid_range(raw_df: pd.DataFrame) -> None:
    ratings = raw_df["评分"].dropna()
    assert ratings.between(0, 5).all()


def test_image_and_url_fields_fully_populated(raw_df: pd.DataFrame) -> None:
    assert raw_df["商品主图"].isna().sum() == 0
    assert raw_df["商品详情页链接"].isna().sum() == 0


def test_category_contamination_documented(raw_df: pd.DataFrame) -> None:
    """Confirms the finding in docs/data_dictionary.md: most rows in this
    'denture base' export are not actually denture base."""
    counts = raw_df["二级类目"].value_counts()
    assert set(counts.index) == {"义齿基托", "义齿修复材料"}
    assert counts["义齿基托"] < counts["义齿修复材料"]
