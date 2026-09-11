"""
Performance instrumentation storage — backs the "⚡ Performance" dashboard
tab and the /performance endpoint (both hr_admin-only).

Every question through app.api.copilot.answer_question records a row here
with a stage-by-stage latency breakdown: routing (LLM call or rule-based
parse), tool execution (the MCP tool call, including any DB/pandas work),
DB query time specifically (a subset of tool execution — see
app/api/perf_context.py), validation, and total.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from app.database.connection import db_session


def record_performance(
    question: Optional[str],
    tool_used: Optional[str],
    routing_method: Optional[str],
    routing_time_ms: float,
    tool_execution_time_ms: float,
    db_query_time_ms: float,
    validation_time_ms: float,
    total_time_ms: float,
):
    with db_session() as conn:
        conn.execute(
            text(
                """
                INSERT INTO performance_log
                    (timestamp, question, tool_used, routing_method, routing_time_ms,
                     tool_execution_time_ms, db_query_time_ms, validation_time_ms, total_time_ms)
                VALUES (:timestamp, :question, :tool_used, :routing_method, :routing_time_ms,
                        :tool_execution_time_ms, :db_query_time_ms, :validation_time_ms, :total_time_ms)
                """
            ),
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "question": question,
                "tool_used": tool_used,
                "routing_method": routing_method,
                "routing_time_ms": routing_time_ms,
                "tool_execution_time_ms": tool_execution_time_ms,
                "db_query_time_ms": db_query_time_ms,
                "validation_time_ms": validation_time_ms,
                "total_time_ms": total_time_ms,
            },
        )


def _percentile(sorted_vals: list, p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    f, c = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    if f == c:
        return round(sorted_vals[f], 1)
    return round(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f), 1)


def _stats_for(values: list) -> dict:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {"avg_ms": 0.0, "p95_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0}
    return {
        "avg_ms": round(sum(vals) / len(vals), 1),
        "p95_ms": _percentile(vals, 0.95),
        "min_ms": round(vals[0], 1),
        "max_ms": round(vals[-1], 1),
    }


def get_performance_stats() -> dict:
    with db_session() as conn:
        rows = conn.execute(
            text("SELECT * FROM performance_log ORDER BY id DESC LIMIT 5000")
        ).mappings().all()

    rows = [dict(r) for r in rows]
    if not rows:
        return {"count": 0, "total": _stats_for([]), "by_stage": {}, "by_tool": {}}

    by_stage = {
        "routing": _stats_for([r["routing_time_ms"] for r in rows]),
        "tool_execution": _stats_for([r["tool_execution_time_ms"] for r in rows]),
        "db_query": _stats_for([r["db_query_time_ms"] for r in rows]),
        "validation": _stats_for([r["validation_time_ms"] for r in rows]),
    }

    by_tool = {}
    for r in rows:
        tool = r["tool_used"] or "(none)"
        by_tool.setdefault(tool, []).append(r["total_time_ms"])
    by_tool_stats = {tool: {**_stats_for(vals), "count": len(vals)} for tool, vals in by_tool.items()}

    return {
        "count": len(rows),
        "total": _stats_for([r["total_time_ms"] for r in rows]),
        "by_stage": {k: {"avg_ms": v["avg_ms"]} for k, v in by_stage.items()},
        "by_tool": by_tool_stats,
    }
