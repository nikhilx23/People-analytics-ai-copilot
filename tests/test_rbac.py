"""Tests for role-based access control: analyst (company-wide), manager
(department-scoped), admin (governance/audit visibility, still no PII)."""

from app.api.copilot import answer_question
from app.governance.permissions import authenticate, authorize


def test_analyst_role_has_company_wide_scope():
    user = authenticate("demo-analyst-token")
    caps = authorize(user)
    assert caps.department_scope is None
    assert caps.can_query_individual_pii is False


def test_manager_role_is_scoped_to_own_department():
    user = authenticate("demo-manager-token")
    assert user.department == "Engineering"
    caps = authorize(user)
    assert caps.department_scope == "Engineering"
    assert caps.can_query_individual_pii is False


def test_admin_role_gets_audit_visibility_but_not_pii():
    user = authenticate("demo-admin-token")
    caps = authorize(user)
    assert caps.can_view_audit_log is True
    assert caps.can_view_admin_dashboards is True
    assert caps.can_query_individual_pii is False
    assert caps.can_run_raw_sql is False


def test_manager_can_query_their_own_department():
    r = answer_question("What is the attrition rate in Engineering?", token="demo-manager-token")
    assert r["blocked"] is False
    assert r["tool_used"] == "calculate_attrition"


def test_manager_is_blocked_from_other_departments():
    r = answer_question("What is the attrition rate in Sales?", token="demo-manager-token")
    assert r["blocked"] is True
    assert "scoped" in r["answer"].lower()


def test_manager_company_wide_question_is_auto_scoped_not_blocked():
    r = answer_question("What is the current headcount?", token="demo-manager-token")
    assert r["blocked"] is False
    assert r["result"]["filters_applied"]["department"] == "Engineering"
    assert "engineering" in r["answer"].lower()


def test_manager_cannot_compare_across_departments():
    r = answer_question("Compare attrition between Engineering and Sales.", token="demo-manager-token")
    assert r["blocked"] is True


def test_analyst_is_not_scoped():
    r = answer_question("What is the attrition rate in Sales?", token="demo-analyst-token")
    assert r["blocked"] is False


def test_unknown_token_raises():
    import pytest
    with pytest.raises(PermissionError):
        authenticate("not-a-real-token")
