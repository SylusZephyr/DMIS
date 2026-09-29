"""Materialize the reviewed gold-labeling sample(s) into the canonical
benchmark schema at data/validated/<category>/gold_labels.csv.

Does not generate labels — reads relevant/reason/product_id values a
human already reviewed (via data/samples/*_pilot.xlsx) and reshapes them
into the fixed schema:
    listing_id, relevant, relevance_reason, product_type, product_id,
    reviewer, review_status

`listing_id` is derived from ASIN with the same stable hash used by the
ingestion pipeline (dmie.cleaning.normalize.make_listing_id), so it joins
directly against the `listings` table.
"""


import pandas as pd

from dmie.cleaning.normalize import make_listing_id
from dmie.database.connection import PROJECT_ROOT

PILOT_XLSX = PROJECT_ROOT / "data" / "samples" / "gold_labels_pilot.xlsx"
OUT_CSV = PROJECT_ROOT / "data" / "validated" / "denture_base" / "gold_labels.csv"

OUTPUT_COLUMNS = [
    "listing_id", "relevant", "relevance_reason", "product_type",
    "product_id", "reviewer", "review_status",
]


def build() -> pd.DataFrame:
    src = pd.read_excel(PILOT_XLSX)

    out = pd.DataFrame()
    out["listing_id"] = src["asin"].apply(make_listing_id)
    out["relevant"] = src["relevant"]
    out["relevance_reason"] = src["reason"]
    out["product_type"] = None  # not yet classified — no product_type taxonomy exists yet
    out["product_id"] = src["product_id"]
    out["reviewer"] = "model"
    needs_review = (src["relevant"] == "UNCERTAIN") | (src["status"] == "needs_review")
    out["review_status"] = needs_review.map({True: "needs_review", False: "model_labeled"})

    return out[OUTPUT_COLUMNS]


def main() -> None:
    df = build()
    assert df["listing_id"].is_unique, "duplicate listing_id in gold dataset"
    assert df["relevant"].isin(["YES", "NO", "UNCERTAIN"]).all()

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)

    print(f"rows: {len(df)}")
    print(df["review_status"].value_counts().to_string())
    print(f"written to: {OUT_CSV}")


if __name__ == "__main__":
    main()
