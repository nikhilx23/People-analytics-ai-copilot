"""
run_governed_sql — the escape hatch for questions the predefined analytics
methods don't cover (e.g. "how many employees are at each job level in
Remote - US locations?"). Every query still passes through the same
governance guard as everything else: read-only, no blocked fields, sensitive
fields must be aggregated, row-capped.

This is intentionally the ONLY tool that can execute free-form SQL, and it
is not just "SQL with a WHERE clause allowlist" — validate_sql() rejects
writes, blocked fields (full_name, manager), un-aggregated sensitive fields
(salary, performance_rating), and individual-level GROUP BY.
"""

import datetime
import decimal
import time

from app.api import perf_context
from app.database.connection import get_connection
from app.governance.policies import GovernanceViolation, log_decision, validate_sql


def _jsonable(value):
    """
    Coerce a single raw DB-driver value into something json.dumps() (and
    everything downstream that expects plain JSON types - the LLM
    explanation layer, the FastAPI response, the frontend) can handle
    without special-casing.

    This matters specifically on PostgreSQL: AVG()/SUM() over an INTEGER
    column returns SQL 'numeric', which psycopg2 surfaces as
    decimal.Decimal, and DATE columns come back as datetime.date/datetime -
    neither is JSON-serializable by default. SQLite's driver returns plain
    float/str for the same queries, which is exactly why this only shows up
    against Postgres and was caught by the adversarial security suite
    (app/evaluation/security_tests.py) rather than earlier SQLite-only
    testing.
    """
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value


def run_governed_sql(sql: str, user_role: str = "hr_analyst") -> dict:
    """
    Execute a read-only, governed SQL SELECT against the employee_analytics
    dataset. Raises no exception to the caller on a governance violation —
    returns a structured refusal instead, so the copilot can relay it to the
    analyst as a normal answer.
    """
    try:
        safe_sql = validate_sql(sql)
    except GovernanceViolation as e:
        log_decision(
            user_role=user_role,
            question=None,
            tool_used="run_governed_sql",
            sql_executed=sql,
            decision="BLOCKED",
            block_reason=e.reason,
        )
        return {
            "allowed": False,
            "error": "governance_violation",
            "message": e.reason,
        }

    conn = get_connection()
    try:
        started = time.perf_counter()
        # exec_driver_sql (not text()) because safe_sql is a fully-formed,
        # already-governed SQL string, not a parameterized template - using
        # text() here would make SQLAlchemy try to interpret any literal ':'
        # in the query as a bind parameter.
        cursor = conn.exec_driver_sql(safe_sql)
        columns = list(cursor.keys())
        rows = [
            {col: _jsonable(val) for col, val in zip(columns, row)}
            for row in cursor.fetchall()
        ]
        perf_context.add_db_time((time.perf_counter() - started) * 1000)
    finally:
        conn.close()

    log_decision(
        user_role=user_role,
        question=None,
        tool_used="run_governed_sql",
        sql_executed=safe_sql,
        decision="ALLOWED",
        result_summary=f"{len(rows)} row(s) returned",
    )

    return {
        "allowed": True,
        "sql_executed": safe_sql,
        "row_count": len(rows),
        "rows": rows,
    }
