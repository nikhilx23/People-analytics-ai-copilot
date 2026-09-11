"""
Data policy / governance layer.

This is the piece that stands between the LLM and the database. Nothing in
this project lets the model run arbitrary SQL against raw tables without
passing through here first. Two independent checks are applied, because
either one alone can be worked around by a differently-worded question:

1. Question-level PII screen  (check_question_for_pii)
   Catches requests that name a specific employee or ask for a raw,
   ungoverned dump before any SQL is even generated.

2. SQL-level guard             (validate_sql)
   Even if a request slips past (1) or an LLM writes SQL directly via the
   run_governed_sql tool, this parses the statement and rejects it if it
   would expose blocked fields, isn't read-only, or isn't properly
   aggregated.

Every decision is written to query_log via log_decision() so governance is
auditable, not just enforced silently.
"""

import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from sqlalchemy import text

from app.database.connection import db_session

# Fields that are NEVER returned by any governed tool or governed SQL query,
# under any circumstances, regardless of role. Direct-identifier PII.
BLOCKED_FIELDS = {"full_name", "manager"}

# Fields that carry sensitive individual-level meaning. They may only be
# returned wrapped in an aggregate function (AVG, SUM, MIN, MAX, COUNT) —
# never as a raw per-row value.
AGGREGATE_ONLY_FIELDS = {"salary", "performance_rating"}

AGGREGATE_FUNCS = {"avg", "sum", "min", "max", "count"}

# Only SELECT statements are ever allowed through run_governed_sql.
_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|attach|pragma|replace|grant|revoke)\b",
    re.IGNORECASE,
)

_SELECT_PREFIX = re.compile(r"^\s*select\b", re.IGNORECASE)

# SELECT * (or table.*) is rejected outright: BLOCKED_FIELDS / AGGREGATE_ONLY_FIELDS
# are matched by column name, so a wildcard silently bypasses both checks and
# would return every row with full_name, salary, manager, and
# performance_rating intact. COUNT(*) is unaffected - the '*' there sits
# inside COUNT(...), not immediately after SELECT/DISTINCT.
_WILDCARD_SELECT = re.compile(r"select\s+(distinct\s+)?(\*|[\w]+\.\*)", re.IGNORECASE)

# Very small k-anonymity guard: a governed aggregate query that groups down
# to a single-row result for one individual is functionally an individual
# lookup. We require GROUP BY (if present) not to be on employee_id/full_name.
_GROUP_BY_INDIVIDUAL = re.compile(r"group\s+by\s+[^;]*\b(employee_id|full_name)\b", re.IGNORECASE)


class GovernanceViolation(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@lru_cache(maxsize=1)
def _employee_names() -> tuple:
    # Cached: this used to run a full-table SELECT on every single question
    # (see performance profiling in README/perf dashboard). The roster is
    # static for the life of the process, so we load it once. Call
    # _employee_names.cache_clear() after reloading the dataset in tests.
    with db_session() as conn:
        rows = conn.execute(text("SELECT full_name FROM employees")).mappings().all()
    return tuple(r["full_name"] for r in rows)


@lru_cache(maxsize=1)
def _non_person_phrases() -> frozenset:
    """
    Department, location, and job-title strings — used to keep the "looks
    like a person's name" heuristic in check_question_for_pii from
    false-positiving on phrases like "Human Resources" or "New York, NY",
    which are also two-or-more Title Case words. Cached for the same reason
    as _employee_names above.
    """
    with db_session() as conn:
        depts = conn.execute(text("SELECT DISTINCT department FROM employees")).mappings().all()
        locations = conn.execute(text("SELECT DISTINCT location FROM employees")).mappings().all()
        titles = conn.execute(text("SELECT DISTINCT job_title FROM employees")).mappings().all()
    phrases = {r["department"].lower() for r in depts}
    phrases |= {r["location"].lower() for r in locations}
    phrases |= {r["job_title"].lower() for r in titles}
    # Also add each individual word from multi-word phrases (e.g. "Human",
    # "Resources") so a partial match against the two-word regex still clears.
    for p in list(phrases):
        phrases.update(re.findall(r"[a-z]+", p))
    return frozenset(phrases)


def check_question_for_pii(question: str) -> Optional[str]:
    """
    Screens a natural-language question BEFORE any tool/SQL is generated.
    Returns a block reason string if the question should be refused, else None.
    """
    q_lower = question.lower()

    sensitive_terms = ["salary", "pay", "compensation", "comp ", "wage",
                        "performance rating", "performance review", "rating of"]
    is_sensitive_ask = any(term in q_lower for term in sensitive_terms)

    # Does the question name a specific employee? Cheap substring match
    # against the known roster - good enough for a governed internal tool
    # where the roster is a closed set (unlike open-web PII detection).
    for name in _employee_names():
        if name.lower() in q_lower:
            return (
                f"Question references a specific employee ('{name}'). "
                "I can't provide personally identifiable employee information "
                "such as an individual's name, salary, or performance rating."
            )

    # Even without a matched name, "what is <capitalized two-word name>'s
    # salary" pattern is a strong signal of an individual lookup attempt —
    # unless that phrase is actually a known department/location/job title
    # (e.g. "Human Resources", "New York, NY"), which would otherwise
    # false-positive since those are also Title Case multi-word phrases.
    name_match = re.search(r"\b([A-Z][a-z]+)\s([A-Z][a-z]+)('s)?\b", question)
    is_known_entity = False
    if is_sensitive_ask and name_match:
        excluded = _non_person_phrases()
        candidate = name_match.group(0).rstrip("'s").lower()
        word1, word2 = name_match.group(1).lower(), name_match.group(2).lower()
        is_known_entity = candidate in excluded or (word1 in excluded and word2 in excluded)

    if is_sensitive_ask and name_match and not is_known_entity:
        return (
            "This looks like a request for one individual's compensation or "
            "performance data. I can only provide aggregate, department- or "
            "company-level metrics — not individual employee records."
        )

    # Row-level export attempts: either a known "dump the whole table" phrase,
    # or "every/each employee's <sensitive field>" (an individual-level ask
    # phrased as if it were plural, which the name-match heuristic above
    # can't catch since there's no literal name in the question).
    raw_export_phrases = [
        "list all employees", "dump", "export all", "full export",
        "employee table", "employee database", "entire roster", "whole roster",
    ]
    looks_like_raw_export = any(phrase in q_lower for phrase in raw_export_phrases) or (
        "export" in q_lower and "employee" in q_lower
    )
    if is_sensitive_ask and ("every employee" in q_lower or "each employee" in q_lower
                              or "all employees'" in q_lower):
        looks_like_raw_export = True

    if looks_like_raw_export:
        return (
            "I can't return a raw, row-level export of employee records. "
            "I can provide aggregate metrics (headcount, attrition, average "
            "tenure, average salary, etc.) grouped by department, level, or "
            "location instead."
        )

    return None


def validate_sql(sql: str) -> str:
    """
    Validates a SQL statement generated for run_governed_sql.
    Returns the (possibly row-capped) safe SQL to execute.
    Raises GovernanceViolation if the statement is not allowed.
    """
    stripped = sql.strip().rstrip(";")

    if not _SELECT_PREFIX.match(stripped):
        raise GovernanceViolation("Only read-only SELECT statements are permitted.")

    if _WRITE_KEYWORDS.search(stripped):
        raise GovernanceViolation("Statement contains a disallowed write/DDL keyword.")

    if ";" in stripped:
        raise GovernanceViolation("Multiple statements are not permitted.")

    if _WILDCARD_SELECT.search(stripped):
        raise GovernanceViolation(
            "SELECT * is not permitted. Governed queries must name their "
            "columns explicitly so blocked and aggregate-only fields can be "
            "checked."
        )

    lowered = stripped.lower()

    for field in BLOCKED_FIELDS:
        if re.search(rf"\b{field}\b", lowered):
            raise GovernanceViolation(
                f"Query references blocked field '{field}'. Direct-identifier "
                "fields can never be returned by governed queries."
            )

    for field in AGGREGATE_ONLY_FIELDS:
        if field not in lowered:
            continue
        # Every mention of a sensitive field must appear inside one of the
        # allowed aggregate functions, e.g. avg(salary), count(salary).
        wrapped_pattern = rf"\b({'|'.join(AGGREGATE_FUNCS)})\s*\(\s*[\w.]*{field}\b"
        if not re.search(wrapped_pattern, lowered):
            raise GovernanceViolation(
                f"Field '{field}' must be wrapped in an aggregate function "
                f"(AVG/SUM/MIN/MAX/COUNT) — raw individual values are not permitted."
            )

    if _GROUP_BY_INDIVIDUAL.search(lowered):
        raise GovernanceViolation(
            "Query groups by an individual identifier, which would expose "
            "single-employee aggregate results. Group by department, level, "
            "location, or status instead."
        )

    if " limit " not in f" {lowered} ":
        stripped += " LIMIT 500"

    return stripped


def log_decision(
    user_role: str,
    question: Optional[str],
    tool_used: Optional[str],
    sql_executed: Optional[str],
    decision: str,
    block_reason: Optional[str] = None,
    result_summary: Optional[str] = None,
    routing_method: Optional[str] = None,
):
    with db_session() as conn:
        conn.execute(
            text(
                """
                INSERT INTO query_log
                    (timestamp, user_role, question, tool_used, sql_executed,
                     governance_decision, block_reason, result_summary, routing_method)
                VALUES (:timestamp, :user_role, :question, :tool_used, :sql_executed,
                        :decision, :block_reason, :result_summary, :routing_method)
                """
            ),
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user_role": user_role,
                "question": question,
                "tool_used": tool_used,
                "sql_executed": sql_executed,
                "decision": decision,
                "block_reason": block_reason,
                "result_summary": result_summary,
                "routing_method": routing_method,
            },
        )
