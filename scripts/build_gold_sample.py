"""Build a random 50-row gold-labeling sample for human review.

Samples from the deduplicated raw listing set (same ASIN-dedup rule as
scripts/ingest.py: first occurrence wins) and adds blank columns for a
human labeler to fill in. Never modifies data/raw/.
"""


import pandas as pd

from dmie.database.connection import PROJECT_ROOT
from dmie.ingestion.excel_loader import load_raw_excel

RAW_PATH = PROJECT_ROOT / "data" / "raw" / "denture_base" / "denture_base_sellersprite.xlsx"
OUT_PATH = PROJECT_ROOT / "data" / "samples" / "gold_labels_pilot.xlsx"
SAMPLE_SIZE = 50
RANDOM_SEED = 42

# Raw column -> output column. Includes category fields (not part of the
# normalized `listings` schema) so a human labeler has enough context to
# judge relevance and product_type.
CONTEXT_COLUMNS = {
    "ASIN": "asin",
    "商品标题": "title",
    "品牌": "brand",
    "商品主图": "image_url",
    "商品详情页链接": "url",
    "价格($)": "price",
    "评分": "rating",
    "子体销量": "monthly_sales",
    "子体销售额($)": "monthly_revenue",
    "二级类目": "raw_secondary_category",
    "小类目": "raw_subcategory",
}

# Blank columns for the human labeler. Left empty by design — not filled in here.
LABEL_COLUMNS = ["relevant", "reason", "product_type", "product_id", "status", "notes"]


def build_sample() -> tuple[pd.DataFrame, int]:
    df = load_raw_excel(RAW_PATH)
    df = df.drop_duplicates(subset="ASIN", keep="first")
    pool_size = len(df)

    sample = df.sample(n=SAMPLE_SIZE, random_state=RANDOM_SEED).sort_index()
    sample = sample[list(CONTEXT_COLUMNS.keys())].rename(columns=CONTEXT_COLUMNS)

    for col in LABEL_COLUMNS:
        sample[col] = None

    return sample.reset_index(drop=True), pool_size


def main() -> None:
    sample, pool_size = build_sample()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    sample.to_excel(OUT_PATH, index=False)
    print(f"pool size (deduplicated): {pool_size}")
    print(f"sampled rows: {len(sample)}")
    print(f"random seed: {RANDOM_SEED}")
    print(f"written to: {OUT_PATH}")


if __name__ == "__main__":
    main()
