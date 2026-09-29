"""Empirical confidence-tier calibration.

The human-review workflow routes every AI/rule decision into one of three
tiers by confidence: automatic, sampling/QA, human review. The boundaries
between tiers must come from measuring precision against a gold-labeled
set, not from assumed round numbers (95%/70%) — those are a starting
hypothesis, not a target to claim without measurement. See
docs/human_review_workflow.md.

PRINCIPLES.md principle 4: this is arithmetic, done in Python — never an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ThresholdPoint:
    threshold: float
    n_at_or_above: int
    n_correct_at_or_above: int
    coverage: float  # n_at_or_above / total population

    @property
    def precision(self) -> float | None:
        """None (not 0 or 1) when nothing in the sweep reaches this
        threshold — there's no precision to report for an empty subset,
        and reporting one would fabricate certainty about zero data."""
        return (self.n_correct_at_or_above / self.n_at_or_above) if self.n_at_or_above else None


@dataclass
class TierRecommendation:
    automatic_threshold: float | None
    automatic_precision: float | None
    sampling_threshold: float | None
    sampling_precision: float | None
    warnings: list[str]


def sweep_thresholds(
    confidences: list[float], correct: list[bool], thresholds: list[float] | None = None
) -> list[ThresholdPoint]:
    """For each candidate threshold t, precision among predictions with
    confidence >= t. Defaults to sweeping the actual distinct confidence
    values observed (plus 0.0 and 1.0) rather than a fixed grid — more
    meaningful for a sparse or bimodal real distribution than an
    arbitrary 0.05 step size would be."""
    assert len(confidences) == len(correct), "confidences and correct must be the same length"
    total = len(confidences)
    if thresholds is None:
        thresholds = sorted({0.0, 1.0, *confidences})

    points = []
    for t in thresholds:
        subset_correct = [c for conf, c in zip(confidences, correct) if conf >= t]
        n = len(subset_correct)
        points.append(ThresholdPoint(threshold=t, n_at_or_above=n,
                                      n_correct_at_or_above=sum(subset_correct),
                                      coverage=(n / total) if total else 0.0))
    return points


def recommend_tiers(
    points: list[ThresholdPoint],
    automatic_precision_target: float = 0.95,
    sampling_precision_target: float = 0.80,
) -> TierRecommendation:
    """The smallest threshold whose precision-at-or-above still meets
    each target — maximizes automation/sampling coverage while holding
    the precision bar, rather than picking the highest (most
    conservative, least useful) threshold that would also qualify.
    Honestly reports (via `warnings`) when no threshold in the sweep has
    both real coverage and a met target, rather than picking one anyway.
    """
    warnings: list[str] = []
    ascending = sorted(points, key=lambda p: p.threshold)

    def _find(target: float, min_n: int = 1) -> ThresholdPoint | None:
        for point in ascending:
            if point.n_at_or_above >= min_n and point.precision is not None and point.precision >= target:
                return point
        return None

    automatic = _find(automatic_precision_target)
    if automatic is None:
        warnings.append(
            f"No confidence threshold in this sweep reaches {automatic_precision_target:.0%} "
            "precision with real coverage — automatic tier not recommended from this data."
        )

    sampling = _find(sampling_precision_target)
    if sampling is None:
        warnings.append(
            f"No confidence threshold in this sweep reaches {sampling_precision_target:.0%} "
            "precision with real coverage — sampling/QA tier not recommended from this data."
        )
    elif automatic is not None and sampling.threshold >= automatic.threshold:
        warnings.append(
            "The sampling-tier threshold that meets its precision target is not below the "
            "automatic-tier threshold — likely means there's no real data in between the two "
            "(a gap in the confidence distribution, not a calibration result). "
            "See docs/human_review_workflow.md."
        )

    distinct_thresholds = len(ascending)
    if distinct_thresholds <= 3:  # sweep_thresholds always adds 0.0 and 1.0, so <=3 means <=1 real observed value
        warnings.append(
            f"Only {distinct_thresholds - 2} distinct confidence value(s) observed in this data "
            "(besides the 0.0/1.0 sweep endpoints) — the confidence distribution is too sparse/bimodal "
            "to place a genuine middle tier yet. Any 'sampling' boundary found here reflects a gap in "
            "the data, not a calibrated decision boundary — re-run once a wider range of confidence "
            "values exists (e.g. once the AI classification stage is configured)."
        )

    return TierRecommendation(
        automatic_threshold=automatic.threshold if automatic else None,
        automatic_precision=automatic.precision if automatic else None,
        sampling_threshold=sampling.threshold if sampling else None,
        sampling_precision=sampling.precision if sampling else None,
        warnings=warnings,
    )
