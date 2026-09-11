"""Tests for the governance layer: question-level PII screening and the SQL guard."""

import pytest
from sqlalchemy import text

from app.database.connection import get_connection
from app.governance.policies import GovernanceViolation, check_question_for_pii, validate_sql


# ---------------------------------------------------------------- #
# Question-level PII screen
# ---------------------------------------------------------------- #

def test_roster_employee_name_is_blocked():
    # Pull a real name from the dataset so this exercises the roster-match
    # branch of check_question_for_pii specifically.
    conn = get_connection()
    try:
        name = conn.execute(text("SELECT full_name FROM employees LIMIT 1")).mappings().one()["full_name"]
    finally:
        conn.close()

    reason = check_question_for_pii(f"What is {name}'s salary?")
    assert reason is not None
    assert "personally identifiable" in reason.lower()


def test_unlisted_name_shaped_salary_question_is_still_blocked():
    # "John Smith" likely isn't in the (Faker-generated) roster, so this
    # exercises the heuristic branch rather than the roster-match branch —
    # it should still be refused, just via a different code path.
    reason = check_question_for_pii("What is John Smith's salary?")
    assert reason is not None
    assert "individual" in reason.lower()


def test_raw_dump_request_is_blocked():
    assert check_question_for_pii("List all employees with their salaries") is not None
    assert check_question_for_pii("Export all employee records") is not None


def test_department_level_aggregate_question_is_allowed():
    assert check_question_for_pii("What is the attrition rate in Engineering?") is None
    assert check_question_for_pii("What is the average salary by department?") is None


def test_department_name_that_looks_like_a_person_is_not_false_flagged():
    # Regression test: "Human Resources" is a Title Case two-word phrase like
    # a person's name, and the question mentions "salary" - this must NOT be
    # blocked, since it's a department-level aggregate question.
    assert check_question_for_pii("What is the average salary in Human Resources?") is None
    assert check_question_for_pii("What is the attrition rate in Customer Support?") is None


# ---------------------------------------------------------------- #
# SQL guard
# ---------------------------------------------------------------- #

def test_valid_aggregate_query_is_allowed():
    sql = "SELECT department, AVG(salary) FROM employees GROUP BY department"
    safe = validate_sql(sql)
    assert safe.lower().startswith("select")
    assert "limit" in safe.lower()


def test_write_statements_are_rejected():
    for bad_sql in [
        "DELETE FROM employees",
        "UPDATE employees SET salary = 0",
        "DROP TABLE employees",
        "INSERT INTO employees VALUES (1)",
    ]:
        with pytest.raises(GovernanceViolation):
            validate_sql(bad_sql)


def test_blocked_field_full_name_is_rejected():
    with pytest.raises(GovernanceViolation):
        validate_sql("SELECT full_name, salary FROM employees")


def test_raw_unaggregated_salary_is_rejected():
    with pytest.raises(GovernanceViolation):
        validate_sql("SELECT employee_id, salary FROM employees WHERE department='Engineering'")


def test_aggregated_salary_is_allowed():
    safe = validate_sql("SELECT department, AVG(salary) as avg_sal FROM employees GROUP BY department")
    assert "avg(salary)" in safe.lower()


def test_group_by_individual_identifier_is_rejected():
    with pytest.raises(GovernanceViolation):
        validate_sql("SELECT employee_id, AVG(salary) FROM employees GROUP BY employee_id")


def test_multiple_statements_are_rejected():
    with pytest.raises(GovernanceViolation):
        validate_sql("SELECT COUNT(*) FROM employees; DROP TABLE employees")
