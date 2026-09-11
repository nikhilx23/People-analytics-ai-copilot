"""Tests for the MCP server registration and the run_governed_sql tool."""

import asyncio

from app.mcp.server import mcp
from app.mcp.tools.sql_tool import run_governed_sql


def test_all_expected_tools_are_registered():
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    expected = {
        "get_employee_metrics", "get_department_metrics", "calculate_attrition",
        "calculate_headcount", "calculate_average_tenure", "run_governed_sql",
    }
    assert expected.issubset(names)


def test_run_governed_sql_allows_aggregate_query():
    result = run_governed_sql("SELECT department, COUNT(*) as n FROM employees GROUP BY department")
    assert result["allowed"] is True
    assert result["row_count"] == 8


def test_run_governed_sql_blocks_pii_field():
    result = run_governed_sql("SELECT full_name, salary FROM employees")
    assert result["allowed"] is False
    assert result["error"] == "governance_violation"


def test_run_governed_sql_blocks_write_statement():
    result = run_governed_sql("DELETE FROM employees")
    assert result["allowed"] is False


def test_run_governed_sql_blocks_wildcard_select():
    # SELECT * bypasses the field-name checks (blocked/aggregate-only
    # fields are matched by name) - it must be rejected outright rather
    # than silently returning every column, PII included.
    result = run_governed_sql("SELECT * FROM employees")
    assert result["allowed"] is False
    assert result["error"] == "governance_violation"


def test_run_governed_sql_result_is_always_json_serializable():
    # On PostgreSQL, AVG()/SUM() over an INTEGER column returns SQL
    # 'numeric', which psycopg2 surfaces as decimal.Decimal, and DATE
    # columns come back as datetime.date - neither is JSON-serializable by
    # default. SQLite returns plain float/str for the same queries, so this
    # only reproduces against Postgres; run on either dialect, the
    # assertion is the same: every row must already be plain JSON types.
    import json

    result = run_governed_sql(
        "SELECT department, AVG(salary) AS avg_salary, MIN(hire_date) AS earliest_hire "
        "FROM employees GROUP BY department"
    )
    assert result["allowed"] is True
    json.dumps(result["rows"])  # must not raise
    for row in result["rows"]:
        assert isinstance(row["avg_salary"], float)
        assert isinstance(row["earliest_hire"], str)
