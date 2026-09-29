from dmie.classification.calibration import recommend_tiers, sweep_thresholds

# A synthetic but continuous confidence distribution, unlike the real
# classifier's current bimodal one (0.0 / 0.95) -- exercises the module's
# actual threshold-sweeping logic properly.
CONTINUOUS_CONFIDENCES = [0.10, 0.30, 0.50, 0.60, 0.75, 0.82, 0.88, 0.91, 0.96, 0.99]
CONTINUOUS_CORRECT =     [False, False, False, True, True, True, True, True, True, True]


def test_sweep_thresholds_hand_computed():
    points = sweep_thresholds(CONTINUOUS_CONFIDENCES, CONTINUOUS_CORRECT, thresholds=[0.0, 0.6, 0.9])
    by_t = {p.threshold: p for p in points}
    assert by_t[0.0].n_at_or_above == 10
    assert by_t[0.0].n_correct_at_or_above == 7
    assert by_t[0.6].n_at_or_above == 7  # 0.60,0.75,0.82,0.88,0.91,0.96,0.99
    assert by_t[0.6].n_correct_at_or_above == 7
    assert by_t[0.9].precision == 1.0


def test_sweep_thresholds_default_uses_observed_values():
    points = sweep_thresholds(CONTINUOUS_CONFIDENCES, CONTINUOUS_CORRECT)
    thresholds = {p.threshold for p in points}
    assert 0.75 in thresholds
    assert 0.0 in thresholds and 1.0 in thresholds


def test_sweep_thresholds_precision_is_none_for_empty_subset():
    points = sweep_thresholds([0.1, 0.2], [True, True], thresholds=[0.9])
    assert points[0].n_at_or_above == 0
    assert points[0].precision is None  # not 0.0 -- no data, not "zero precision"


def test_sweep_thresholds_rejects_mismatched_lengths():
    import pytest
    with pytest.raises(AssertionError):
        sweep_thresholds([0.1, 0.2], [True])


def test_recommend_tiers_finds_smallest_threshold_meeting_target():
    points = sweep_thresholds(CONTINUOUS_CONFIDENCES, CONTINUOUS_CORRECT)
    rec = recommend_tiers(points, automatic_precision_target=0.95, sampling_precision_target=0.80)
    # threshold 0.60 is the smallest with precision 1.0 (5/5) among values >= it that are all correct
    assert rec.automatic_threshold is not None
    assert rec.automatic_precision >= 0.95
    assert rec.sampling_threshold is not None
    assert rec.sampling_threshold <= rec.automatic_threshold
    assert rec.sampling_precision >= 0.80


def test_recommend_tiers_warns_when_target_unreachable():
    points = sweep_thresholds([0.9, 0.9, 0.9], [False, False, True])  # max possible precision 33%
    rec = recommend_tiers(points, automatic_precision_target=0.95)
    assert rec.automatic_threshold is None
    assert any("automatic tier not recommended" in w for w in rec.warnings)


def test_recommend_tiers_warns_on_bimodal_real_world_distribution():
    """Regression: the real relevance classifier's confidence is
    currently bimodal (0.95 for rules, 0.0 for ai_unavailable) -- must
    flag this explicitly rather than silently reporting a 'sampling
    tier' that's actually just a data gap."""
    confidences = [0.95] * 39 + [0.0] * 11
    correct = [True] * 38 + [False] + [True] * 11  # matches the real gold-set finding: 1 wrong at 0.95
    points = sweep_thresholds(confidences, correct)
    rec = recommend_tiers(points)
    assert any("too sparse/bimodal" in w for w in rec.warnings)


def test_recommend_tiers_no_warning_with_genuinely_continuous_data():
    points = sweep_thresholds(CONTINUOUS_CONFIDENCES, CONTINUOUS_CORRECT)
    rec = recommend_tiers(points)
    assert not any("too sparse/bimodal" in w for w in rec.warnings)


def test_recommend_tiers_empty_input():
    rec = recommend_tiers(sweep_thresholds([], []))
    assert rec.automatic_threshold is None
    assert rec.sampling_threshold is None
