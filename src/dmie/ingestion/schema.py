"""Canonical schema for SellerSprite raw exports.

See docs/data_dictionary.md for the full column inspection this is based on.
"""

SHEET_NAME = "Sheet1"
MARKETPLACE = "US"

# Raw (Chinese) column name -> canonical field name.
# Only columns with a destination in the `listings` DB table are mapped here.
# Columns with no current destination (二级类目, 小类目, FBA($), 上架时间,
# 包装重量/包装尺寸/包装尺寸分段, 产品经理) are intentionally left unmapped —
# see docs/data_dictionary.md "Fields with no destination in the current
# listings schema".
COLUMN_MAP = {
    "ASIN": "asin",
    "商品标题": "title",
    "品牌": "brand",
    "商品详情页链接": "url",
    "商品主图": "image_url",
    "价格($)": "price",
    "评分": "rating",
    "子体销量": "monthly_sales",
    "子体销售额($)": "monthly_revenue",
}

EXPECTED_RAW_COLUMNS = list(COLUMN_MAP.keys())
