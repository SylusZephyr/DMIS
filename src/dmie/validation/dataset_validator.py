"""Dataset Validation Engine (Milestone 16).

Runs a structured, non-destructive PASS/WARNING/FAIL report over a raw
uploaded DataFrame *before* anything reaches normalize_dataframe() or the
database. Every check here mirrors a rule normalize.py/validation.py
already enforce downstream (EXPECTED_RAW_COLUMNS, price > 0, rating in
[0, 5], duplicate-ASIN handling) -- a FAIL/WARNING here means
normalization would reject or silently drop the same rows, not a second,
independently-invented notion of "valid".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from dmie.ingestion.schema import EXPECTED_RAW_COLUMNS

SEVERITY_PASS = "PASS"
SEVERITY_WARNING = "WARNING"
SEVERITY_FAIL = "FAIL"

_SEVERITY_RANK = {SEVERITY_PASS: 0, SEVERITY_WARNING: 1, SEVERITY_FAIL: 2}

# The fixed set of row-level data-quality dimensions quality_score
# averages over -- every one of these always appears in `checks` (as
# PASS or WARNING) once schema validation passes, so a clean check
# contributes a real 0%-affected data point, not just an absence.
# schema_mismatch/missing_asin are deliberately excluded: they're FAIL
# conditions that already zero the whole score, not a fraction-of-rows
# dimension.
_ROW_LEVEL_CHECK_NAMES = {
    "duplicate_asins", "missing_values_price", "missing_values_rating",
    "missing_values_monthly_sales", "missing_values_monthly_revenue",
    "invalid_prices", "invalid_ratings", "missing_images",
}


@dataclass
class ValidationCheck:
    name: str
    severity: str  # PASS | WARNING | FAIL
    message: str
    details: list[str] = field(default_factory=list)
    # Rows this specific check flagged -- 0 for schema-level/informational
    # checks (schema_mismatch, missing_asin) that don't have a clean
    # "fraction of rows affected" meaning. Used only by quality_score
    # below; every check's own PASS/WARNING/FAIL severity (shown in the
    # UI) is unaffected by this field.
    affected_count: int = 0


@dataclass
class ValidationReport:
    checks: list[ValidationCheck]
    row_count: int

    @property
    def status(self) -> str:
        if not self.checks:
            return SEVERITY_PASS
        return max((c.severity for c in self.checks), key=lambda s: _SEVERITY_RANK[s])

    @property
    def quality_score(self) -> float:
        """A single 0-100 number for the "Dataset profile" summary
        (Milestone 1). Deterministic, not AI-derived, and each
        contributing check is visible in `checks` -- this is a summary
        of real, already-shown numbers, never an opaque score.

        A FAIL means ingestion cannot proceed at all -- score is 0, full
        stop. Otherwise: 100 minus the average, across all 8 row-level
        data-quality dimensions (_ROW_LEVEL_CHECK_NAMES), of the fraction
        of rows that dimension flagged -- a clean dimension contributes a
        real 0%, not an absence, so one moderate issue among several
        otherwise-clean dimensions doesn't tank the score the way
        averaging only the triggered checks would.
        """
        if self.status == SEVERITY_FAIL:
            return 0.0
        row_level = [c for c in self.checks if c.name in _ROW_LEVEL_CHECK_NAMES]
        if not row_level or self.row_count == 0:
            return 100.0
        avg_affected_fraction = sum(c.affected_count for c in row_level) / (len(row_level) * self.row_count)
        return round(max(0.0, 100.0 - avg_affected_fraction * 100.0), 1)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "row_count": self.row_count,
            "quality_score": self.quality_score,
            "checks": [
                {"name": c.name, "severity": c.severity, "message": c.message,
                 "details": c.details, "affected_count": c.affected_count}
                for c in self.checks
            ],
        }

    @staticmethod
    def from_dict(data: dict) -> "ValidationReport":
        return ValidationReport(
            row_count=data["row_count"],
            checks=[ValidationCheck(
                name=c["name"], severity=c["severity"], message=c["message"],
                details=c.get("details", []), affected_count=c.get("affected_count", 0),
            ) for c in data["checks"]],
        )


def validate_raw_dataframe(df: pd.DataFrame) -> ValidationReport:
    """Validate a raw SellerSprite export before normalization."""
    checks: list[ValidationCheck] = []

    missing_columns = sorted(set(EXPECTED_RAW_COLUMNS) - set(df.columns))
    if missing_columns:
        checks.append(ValidationCheck(
            "schema_mismatch", SEVERITY_FAIL,
            f"{len(missing_columns)} expected column(s) missing from this file",
            details=missing_columns,
        ))
        # Nothing else can be checked meaningfully once core columns are
        # missing -- every row-level check below keys off ASIN/price/
        # rating/image columns that may not exist.
        return ValidationReport(checks=checks, row_count=len(df))
    checks.append(ValidationCheck("schema_mismatch", SEVERITY_PASS, "All expected columns present"))

    asin_col = df["ASIN"]
    blank_asin = asin_col.isna() | (asin_col.astype(str).str.strip() == "")
    if blank_asin.any():
        checks.append(ValidationCheck(
            "missing_asin", SEVERITY_FAIL,
            f"{int(blank_asin.sum())} row(s) have no ASIN -- these rows cannot be ingested at all",
        ))
    else:
        checks.append(ValidationCheck("missing_asin", SEVERITY_PASS, "Every row has an ASIN"))

    non_blank_asins = asin_col[~blank_asin].astype(str).str.strip()
    duplicate_asins = non_blank_asins[non_blank_asins.duplicated()].unique().tolist()
    if duplicate_asins:
        dup_row_count = int(non_blank_asins.duplicated().sum())
        checks.append(ValidationCheck(
            "duplicate_asins", SEVERITY_WARNING,
            f"{len(duplicate_asins)} duplicate ASIN(s) found -- ingestion keeps the first occurrence "
            "of each, the rest are dropped",
            details=[str(a) for a in duplicate_asins[:20]], affected_count=dup_row_count,
        ))
    else:
        checks.append(ValidationCheck("duplicate_asins", SEVERITY_PASS, "No duplicate ASINs"))

    for raw_col, field_name in [("价格($)", "price"), ("评分", "rating"),
                                 ("子体销量", "monthly_sales"), ("子体销售额($)", "monthly_revenue")]:
        missing = int(df[raw_col].isna().sum())
        if missing:
            checks.append(ValidationCheck(
                f"missing_values_{field_name}", SEVERITY_WARNING,
                f"{missing} of {len(df)} row(s) have no {field_name} -- kept as unmeasured, "
                "never treated as zero (PRINCIPLES.md 'None never 0')",
                affected_count=missing,
            ))
        else:
            checks.append(ValidationCheck(f"missing_values_{field_name}", SEVERITY_PASS, f"No missing {field_name}"))

    prices = pd.to_numeric(df["价格($)"], errors="coerce")
    invalid_prices = int(((prices <= 0) & prices.notna()).sum())
    if invalid_prices:
        checks.append(ValidationCheck(
            "invalid_prices", SEVERITY_WARNING,
            f"{invalid_prices} row(s) have a price <= 0 -- normalization drops these to unmeasured",
            affected_count=invalid_prices,
        ))
    else:
        checks.append(ValidationCheck("invalid_prices", SEVERITY_PASS, "All present prices are > 0"))

    ratings = pd.to_numeric(df["评分"], errors="coerce")
    invalid_ratings = int((((ratings < 0) | (ratings > 5)) & ratings.notna()).sum())
    if invalid_ratings:
        checks.append(ValidationCheck(
            "invalid_ratings", SEVERITY_WARNING,
            f"{invalid_ratings} row(s) have a rating outside [0, 5] -- normalization drops these to unmeasured",
            affected_count=invalid_ratings,
        ))
    else:
        checks.append(ValidationCheck("invalid_ratings", SEVERITY_PASS, "All present ratings are within [0, 5]"))

    missing_images = df["商品主图"].isna() | (df["商品主图"].astype(str).str.strip() == "")
    if missing_images.any():
        checks.append(ValidationCheck(
            "missing_images", SEVERITY_WARNING,
            f"{int(missing_images.sum())} of {len(df)} row(s) have no product image",
            affected_count=int(missing_images.sum()),
        ))
    else:
        checks.append(ValidationCheck("missing_images", SEVERITY_PASS, "Every row has a product image"))

    return ValidationReport(checks=checks, row_count=len(df))
