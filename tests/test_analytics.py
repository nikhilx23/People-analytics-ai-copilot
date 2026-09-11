"""Tests for the encoded analytical methods against independent reference calculations."""

from app.analytics.attrition import calculate_attrition
from app.analytics.compensation import calculate_average_salary, calculate_promotion_rate
from app.analytics.headcount import calculate_headcount, calculate_new_hires
from app.analytics.tenure import calculate_average_tenure
from app.evaluation import reference_calculations as ref


def test_attrition_matches_reference():
    result = calculate_attrition(department="Engineering")
    expected = ref.ref_attrition(department="Engineering")
    assert result.value == expected
    assert 0 <= result.value <= 100
    assert result.method_version == "v1.2"


def test_attrition_voluntary_filter_narrows_result():
    overall = calculate_attrition(department="Sales", termination_type="all")
    voluntary = calculate_attrition(department="Sales", termination_type="voluntary")
    assert voluntary.breakdown["employees_who_left"] <= overall.breakdown["employees_who_left"]


def test_headcount_matches_reference():
    result = calculate_headcount(department="Marketing")
    expected = ref.ref_headcount(department="Marketing")
    assert result.value == expected
    assert result.value >= 0


def test_new_hires_matches_reference():
    result = calculate_new_hires(department="Product", start_date="2026-01-01", end_date="2026-08-31")
    expected = ref.ref_new_hires(department="Product", start="2026-01-01", end="2026-08-31")
    assert result.value == expected


def test_average_tenure_matches_reference_and_is_plausible():
    result = calculate_average_tenure(department="Finance")
    expected = ref.ref_average_tenure(department="Finance")
    assert result.value == expected
    assert 0 <= result.value <= 20


def test_average_salary_matches_reference():
    result = calculate_average_salary(department="Engineering")
    expected = ref.ref_average_salary(department="Engineering")
    assert result.value == expected
    assert result.value > 0


def test_average_salary_group_by_returns_all_departments():
    result = calculate_average_salary(group_by="department")
    assert len(result.breakdown) == 8  # 8 departments in the synthetic dataset


def test_promotion_rate_matches_reference():
    result = calculate_promotion_rate(start_date="2025-01-01", end_date="2025-12-31")
    expected = ref.ref_promotion_rate(start="2025-01-01", end="2025-12-31")
    assert result.value == expected
    assert 0 <= result.value <= 100


def test_analytics_result_carries_explainability_fields():
    result = calculate_attrition(department="Engineering")
    d = result.to_dict()
    for field in ("metric_name", "value", "unit", "method_name", "method_version", "formula", "breakdown", "data_source"):
        assert field in d
    explanation = result.explain()
    assert "Formula:" in explanation
    assert "Method:" in explanation
