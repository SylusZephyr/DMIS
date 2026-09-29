"""Profile a raw SellerSprite export without modifying it.

Read-only: never writes to data/raw/. Produces a per-column profile CSV
under data/samples/ for use while writing docs/data_dictionary.md.
"""

import sys
from pathlib import Path

import pandas as pd

from dmie.database.connection import PROJECT_ROOT


def profile_sheet(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    n = len(df)
    for col in df.columns:
        series = df[col]
        non_null = series.notna().sum()
        null_count = n - non_null
        sample_values = series.dropna().unique()[:5]
        rows.append(
            {
                "column_name": col,
                "pandas_dtype": str(series.dtype),
                "non_null_count": non_null,
                "null_count": null_count,
                "missing_rate": round(null_count / n, 4) if n else None,
                "unique_count": series.nunique(dropna=True),
                "min": series.min() if pd.api.types.is_numeric_dtype(series) else None,
                "max": series.max() if pd.api.types.is_numeric_dtype(series) else None,
                "mean": round(series.mean(), 4) if pd.api.types.is_numeric_dtype(series) else None,
                "sample_values": " | ".join(str(v) for v in sample_values),
            }
        )
    return pd.DataFrame(rows)


def main(xlsx_path: str, sheet_name: str, out_csv: str) -> None:
    src = Path(xlsx_path)
    df = pd.read_excel(src, sheet_name=sheet_name)

    profile = profile_sheet(df)

    out_path = Path(out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    profile.to_csv(out_path, index=False, encoding="utf-8-sig")

    total_rows = len(df)
    full_row_dupes = int(df.duplicated().sum())
    asin_dupes = int(df["ASIN"].duplicated().sum()) if "ASIN" in df.columns else None

    print(f"source: {src}")
    print(f"sheet: {sheet_name}")
    print(f"rows: {total_rows}, columns: {len(df.columns)}")
    print(f"full-row duplicates: {full_row_dupes} ({full_row_dupes / total_rows:.2%})")
    if asin_dupes is not None:
        print(f"duplicate ASIN rows: {asin_dupes} ({asin_dupes / total_rows:.2%})")
    print(f"profile written to: {out_path}")


if __name__ == "__main__":
    xlsx = sys.argv[1] if len(sys.argv) > 1 else str(
        PROJECT_ROOT / "data" / "raw" / "denture_base" / "denture_base_sellersprite.xlsx"
    )
    sheet = sys.argv[2] if len(sys.argv) > 2 else "Sheet1"
    out = sys.argv[3] if len(sys.argv) > 3 else str(
        PROJECT_ROOT / "data" / "samples" / "denture_base_profile.csv"
    )
    main(xlsx, sheet, out)
