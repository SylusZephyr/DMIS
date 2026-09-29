"""Stage 2 -- Data purification (quality scoring).

Wraps ``dmie.engine.quality.assess_quality`` (missing values, duplicates,
IQR / robust z-score / Isolation Forest outliers, rule checks, per-record
data confidence). Hard-rejected records from ingestion are marked unusable
but kept, with reasons, for the report.
"""

from __future__ import annotations

import pandas as pd

from dmie.engine.quality import QualityReport, assess_quality


def clean(frame: pd.DataFrame) -> QualityReport:
    rep = assess_quality(frame)
    if "accepted" in rep.frame:
        rep.frame["usable_for_market"] = rep.frame["usable_for_market"] & rep.frame["accepted"]
    return rep
