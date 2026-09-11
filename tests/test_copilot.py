"""Tests for the end-to-end copilot orchestration pipeline."""

from app.api.copilot import answer_question


def test_allowed_question_returns_governed_answer():
    r = answer_question("What was the attrition rate in Engineering over the last 12 months?")
    assert r["blocked"] is False
    assert r["tool_used"] == "calculate_attrition"
    assert r["result"] is not None
    assert 0 <= r["result"]["value"] <= 100


def test_restricted_question_is_refused_not_answered():
    r = answer_question("What is John Smith's salary?")
    assert r["blocked"] is True
    assert r["tool_used"] is None
    assert r["result"] is None
    assert "personally identifiable" in r["answer"].lower()


def test_low_confidence_question_asks_for_clarification_instead_of_guessing():
    r = answer_question("asdkjh qwoiuey random text")
    assert r["blocked"] is False
    assert r["confidence"] == "low"
    assert r["tool_used"] is None


def test_comparison_preserves_question_order():
    r = answer_question("Compare attrition between Engineering and Sales.")
    assert r["comparison"][0]["department"] == "Engineering"
    assert r["comparison"][1]["department"] == "Sales"


def test_comparison_matches_single_department_calls():
    single_eng = answer_question("What is the attrition rate in Engineering?")
    compared = answer_question("Compare attrition between Engineering and Sales.")
    eng_in_compare = next(c for c in compared["comparison"] if c["department"] == "Engineering")
    assert eng_in_compare["result"]["value"] == single_eng["result"]["value"]


def test_every_answer_is_logged_to_query_log():
    from sqlalchemy import text
    from app.database.connection import get_connection

    conn = get_connection()
    try:
        before = conn.execute(text("SELECT COUNT(*) c FROM query_log")).mappings().one()["c"]
    finally:
        conn.close()

    answer_question("What is the current headcount in Product?")

    conn = get_connection()
    try:
        after = conn.execute(text("SELECT COUNT(*) c FROM query_log")).mappings().one()["c"]
    finally:
        conn.close()

    assert after == before + 1
