"""
Analyst feedback loop storage — the 👍/👎/report-issue loop from the project
brief. Every piece of feedback is stored alongside the question, which tool
answered it, the answer text, and response time, so an internal dashboard
can be built directly off this table (see app/evaluation and the Streamlit
dashboard tab).
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from app.database.connection import db_session


def save_feedback(
    question: str,
    tool_used: Optional[str],
    answer: Optional[str],
    feedback: str,  # 'up' | 'down' | 'report'
    error_type: Optional[str] = None,
    response_time_ms: Optional[int] = None,
):
    with db_session() as conn:
        conn.execute(
            text(
                """
                INSERT INTO feedback (timestamp, question, tool_used, answer, feedback, error_type, response_time_ms)
                VALUES (:timestamp, :question, :tool_used, :answer, :feedback, :error_type, :response_time_ms)
                """
            ),
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "question": question,
                "tool_used": tool_used,
                "answer": answer,
                "feedback": feedback,
                "error_type": error_type,
                "response_time_ms": response_time_ms,
            },
        )


def get_feedback_stats() -> dict:
    with db_session() as conn:
        total = conn.execute(text("SELECT COUNT(*) c FROM feedback")).mappings().one()["c"]
        up = conn.execute(text("SELECT COUNT(*) c FROM feedback WHERE feedback='up'")).mappings().one()["c"]
        down = conn.execute(text("SELECT COUNT(*) c FROM feedback WHERE feedback='down'")).mappings().one()["c"]
        reports = conn.execute(text("SELECT COUNT(*) c FROM feedback WHERE feedback='report'")).mappings().one()["c"]

        top_tools = conn.execute(
            text(
                """
                SELECT tool_used, COUNT(*) c FROM feedback
                WHERE tool_used IS NOT NULL
                GROUP BY tool_used ORDER BY c DESC LIMIT 10
                """
            )
        ).mappings().all()

        top_questions = conn.execute(
            text(
                """
                SELECT question, COUNT(*) c FROM feedback
                GROUP BY question ORDER BY c DESC LIMIT 10
                """
            )
        ).mappings().all()

        failed_questions = conn.execute(
            text(
                """
                SELECT question, error_type, timestamp FROM feedback
                WHERE feedback IN ('down', 'report')
                ORDER BY timestamp DESC LIMIT 20
                """
            )
        ).mappings().all()

        avg_response_time = conn.execute(
            text("SELECT AVG(response_time_ms) a FROM feedback WHERE response_time_ms IS NOT NULL")
        ).mappings().one()["a"]

    satisfaction = round((up / total) * 100, 1) if total else None

    return {
        "total_feedback": total,
        "thumbs_up": up,
        "thumbs_down": down,
        "reports": reports,
        "satisfaction_pct": satisfaction,
        "most_used_tools": [dict(r) for r in top_tools],
        "most_common_questions": [dict(r) for r in top_questions],
        "failed_questions": [dict(r) for r in failed_questions],
        "avg_response_time_ms": round(avg_response_time, 1) if avg_response_time else None,
    }
