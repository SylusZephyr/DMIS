"""Read-only loader for raw SellerSprite Excel exports.

Never writes to the source file — data/raw/ is immutable source evidence
(see PRINCIPLES.md principle 3).
"""

from pathlib import Path

import pandas as pd

from dmie.ingestion.schema import EXPECTED_RAW_COLUMNS, SHEET_NAME


def load_raw_excel(path: str | Path, sheet_name: str = SHEET_NAME) -> pd.DataFrame:
    df = pd.read_excel(Path(path), sheet_name=sheet_name)
    missing = set(EXPECTED_RAW_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"raw export missing expected columns: {sorted(missing)}")
    return df
