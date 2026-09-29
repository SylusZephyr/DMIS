from dmie.reviews.aggregation import rejection_summary, theme_frequency, theme_severity

INSIGHTS = [
    {"status": "extracted", "pain_point_category": "QUALITY", "pain_point_subcategory": "breakage", "severity": 3},
    {"status": "extracted", "pain_point_category": "QUALITY", "pain_point_subcategory": "breakage", "severity": 5},
    {"status": "extracted", "pain_point_category": "USABILITY", "pain_point_subcategory": "instructions", "severity": 2},
    {"status": "rejected_no_evidence", "pain_point_category": "QUALITY", "pain_point_subcategory": "breakage", "severity": 4},
    {"status": "ai_unavailable", "pain_point_category": None, "pain_point_subcategory": None, "severity": None},
    {"status": "no_pain_point", "pain_point_category": None, "pain_point_subcategory": None, "severity": None},
]


def test_theme_frequency_counts_only_extracted():
    freq = theme_frequency(INSIGHTS)
    assert freq == {"QUALITY/breakage": 2, "USABILITY/instructions": 1}


def test_theme_frequency_excludes_rejected_and_unavailable():
    freq = theme_frequency(INSIGHTS)
    # the rejected_no_evidence QUALITY/breakage entry must NOT be counted
    assert freq["QUALITY/breakage"] == 2


def test_theme_severity_hand_computed():
    severity = theme_severity(INSIGHTS)
    assert severity["QUALITY/breakage"]["mean_severity"] == 4.0  # (3+5)/2
    assert severity["QUALITY/breakage"]["max_severity"] == 5
    assert severity["QUALITY/breakage"]["count"] == 2
    assert severity["USABILITY/instructions"]["mean_severity"] == 2.0


def test_theme_severity_excludes_non_extracted():
    severity = theme_severity(INSIGHTS)
    # only 2 breakage entries counted, not the rejected one with severity=4
    assert severity["QUALITY/breakage"]["count"] == 2


def test_rejection_summary_counts_non_extracted_statuses():
    summary = rejection_summary(INSIGHTS)
    assert summary == {"rejected_no_evidence": 1, "ai_unavailable": 1, "no_pain_point": 1}


def test_empty_insights_produce_empty_aggregates():
    assert theme_frequency([]) == {}
    assert theme_severity([]) == {}
    assert rejection_summary([]) == {}
