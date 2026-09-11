"""
Tests for the LLM tool-selection layer and its integration into the copilot
pipeline. No real ANTHROPIC_API_KEY is available in this environment, so
these tests mock app.api.llm_router.route_with_llm / llm_available to
exercise every branch of the routing logic in app.api.copilot — the
fallback-to-rule-based behavior, the "LLM declined" text path, the
run_governed_sql escape hatch, and RBAC enforcement applying identically
regardless of which router produced the intent.
"""

from unittest.mock import patch

from app.api.copilot import answer_question
from app.api.llm_router import Intent, _tool_use_to_intent


def test_llm_unavailable_uses_rule_based_routing():
    with patch("app.api.copilot.llm_available", return_value=False):
        r = answer_question("What is the attrition rate in Engineering?")
    assert r["routing_method"] == "rule_based"
    assert r["tool_used"] == "calculate_attrition"


def test_llm_route_intent_is_used_when_available():
    fake_intent = Intent(raw_question="", metric="headcount", departments=["Marketing"], confidence="high")
    with patch("app.api.copilot.llm_available", return_value=True), \
         patch("app.api.copilot.route_with_llm", return_value={"kind": "intent", "intent": fake_intent}):
        r = answer_question("How many people work in Marketing?")
    assert r["routing_method"] == "llm"
    assert r["tool_used"] == "calculate_headcount"
    assert r["blocked"] is False


def test_llm_error_falls_back_to_rule_based():
    with patch("app.api.copilot.llm_available", return_value=True), \
         patch("app.api.copilot.route_with_llm", side_effect=RuntimeError("network error")):
        r = answer_question("What is the attrition rate in Sales?")
    assert r["routing_method"] == "rule_based_fallback_after_llm_error"
    assert r["blocked"] is False
    assert r["tool_used"] == "calculate_attrition"


def test_llm_decline_text_is_relayed_not_treated_as_a_tool_call():
    with patch("app.api.copilot.llm_available", return_value=True), \
         patch("app.api.copilot.route_with_llm",
               return_value={"kind": "text", "text": "I can't help with that specific request."}):
        r = answer_question("some ambiguous question")
    assert r["blocked"] is False
    assert r["tool_used"] is None
    assert "can't help" in r["answer"].lower()


def test_llm_sql_route_is_governed_like_everything_else():
    with patch("app.api.copilot.llm_available", return_value=True), \
         patch("app.api.copilot.route_with_llm",
               return_value={"kind": "sql", "sql": "SELECT full_name, salary FROM employees"}):
        r = answer_question("some question that made the LLM reach for SQL")
    # full_name is a blocked field - the governance guard inside
    # run_governed_sql must reject this even though it came from the LLM.
    assert r["blocked"] is True


def test_llm_sql_route_allows_governed_aggregate_query():
    with patch("app.api.copilot.llm_available", return_value=True), \
         patch("app.api.copilot.route_with_llm",
               return_value={"kind": "sql", "sql": "SELECT department, COUNT(*) FROM employees GROUP BY department"}):
        r = answer_question("some question")
    assert r["blocked"] is False
    assert r["tool_used"] == "run_governed_sql"


def test_manager_role_blocked_from_llm_sql_escape_hatch():
    with patch("app.api.copilot.llm_available", return_value=True), \
         patch("app.api.copilot.route_with_llm",
               return_value={"kind": "sql", "sql": "SELECT department, COUNT(*) FROM employees GROUP BY department"}):
        r = answer_question("some question", token="demo-manager-token")
    assert r["blocked"] is True
    assert "scoped" in r["answer"].lower()


def test_rbac_enforced_identically_on_llm_routed_intent():
    # A manager scoped to Engineering asking (via a mocked LLM route) about
    # Sales must still be blocked - RBAC doesn't care which router produced
    # the intent.
    fake_intent = Intent(raw_question="", metric="attrition", departments=["Sales"], confidence="high")
    with patch("app.api.copilot.llm_available", return_value=True), \
         patch("app.api.copilot.route_with_llm", return_value={"kind": "intent", "intent": fake_intent}):
        r = answer_question("What is Sales attrition?", token="demo-manager-token")
    assert r["blocked"] is True


def test_llm_explanation_failure_falls_back_to_template():
    with patch("app.api.copilot.llm_available", return_value=True), \
         patch("app.api.copilot.route_with_llm", return_value=None), \
         patch("app.api.copilot.generate_llm_explanation", return_value=None):
        r = answer_question("What is the current headcount in Product?")
    assert r["blocked"] is False
    assert "Headcount" in r["answer"] or "headcount" in r["answer"].lower()


def test_llm_explanation_is_used_when_available():
    with patch("app.api.copilot.llm_available", return_value=True), \
         patch("app.api.copilot.route_with_llm", return_value=None), \
         patch("app.api.copilot.generate_llm_explanation", return_value="Product currently has a custom LLM-phrased answer."):
        r = answer_question("What is the current headcount in Product?")
    assert r["answer"].startswith("Product currently has a custom LLM-phrased answer.")


def test_tool_use_to_intent_maps_run_governed_sql_to_none():
    # run_governed_sql has no Intent-compatible metric - the caller handles
    # it as a separate "kind": "sql" branch instead.
    assert _tool_use_to_intent("run_governed_sql", {}) is None


def test_performance_is_recorded_for_every_question():
    from sqlalchemy import text
    from app.database.connection import get_connection

    conn = get_connection()
    try:
        before = conn.execute(text("SELECT COUNT(*) c FROM performance_log")).mappings().one()["c"]
    finally:
        conn.close()

    answer_question("What is the average tenure in Finance?")

    conn = get_connection()
    try:
        after = conn.execute(text("SELECT COUNT(*) c FROM performance_log")).mappings().one()["c"]
    finally:
        conn.close()

    assert after == before + 1
