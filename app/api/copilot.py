"""
Copilot orchestration — the full pipeline for one analyst question:

    Question
       -> Governance: question-level PII screen
       -> Routing: LLM tool-selection (if ANTHROPIC_API_KEY is configured),
          else the deterministic rule-based parser — automatic fallback on
          any LLM/API error too, so a missing key or a flaky network never
          fails the request, it just answers a bit less flexibly
       -> Governance: RBAC department-scope check, low-confidence -> clarify
       -> MCP tool call (through app.mcp.tools, same functions the MCP
          server exposes) — the LLM selects a tool, it never executes SQL
          directly except through run_governed_sql's own guard
       -> Validation layer: sanity-check the numeric result before it's
          allowed to reach the analyst
       -> LLM explanation layer (optional): rephrases the governed result
          into a natural sentence — never introduces a number that wasn't
          already in the tool result
       -> AI answer (natural-language sentence + attached explanation)
       -> Performance log: every stage's latency is recorded

This mirrors the diagram in the project brief:
    HR Analyst -> AI Copilot/LLM -> MCP Server -> SQL/Analytics Tool ->
    Validation Layer -> Governance Check -> AI Response
with governance applied at three independent points (question screen, RBAC
scope, and the SQL guard inside run_governed_sql) rather than once, since a
single checkpoint is easy to route around with different phrasing.
"""

import copy
import time
from typing import Optional

from app.api import perf_context
from app.api.llm_router import generate_llm_explanation, llm_available, route_with_llm
from app.api.nl_parser import parse_intent
from app.api.performance import record_performance
from app.api.validation import validate_result
from app.governance.permissions import authenticate, authorize
from app.governance.policies import check_question_for_pii, log_decision
from app.mcp.tools import analytics_tools
from app.mcp.tools.sql_tool import run_governed_sql


def _apply_rbac_scope(intent, caps) -> Optional[str]:
    """
    Enforces department-level RBAC on a parsed intent (see
    app/governance/permissions.py: hr_manager is scoped to exactly one
    department). Returns a block reason if the question falls outside the
    caller's scope; otherwise returns None, having narrowed
    intent.departments to the caller's own department in place when the
    question didn't name one (company-wide phrasing from a scoped role is
    auto-narrowed rather than refused outright).
    """
    if caps.department_scope is None:
        return None  # company-wide role - no restriction

    scope = caps.department_scope
    if intent.departments:
        out_of_scope = [d for d in intent.departments if d != scope]
        if out_of_scope:
            return (
                f"Your role is scoped to the {scope} department. "
                f"You don't have access to analytics for {', '.join(out_of_scope)}."
            )
        return None

    intent.departments = [scope]
    intent.auto_scoped = True
    return None


CLARIFICATION_EXAMPLES = [
    "What was the voluntary attrition rate in Engineering over the last 12 months?",
    "How many employees joined Sales this year?",
    "Compare attrition between Engineering and Sales.",
    "What is the average tenure by department?",
    "What percentage of employees were promoted last year?",
    "What is the average salary by department?",
]


def _run_metric(intent) -> (str, dict):
    """Dispatch a parsed intent to the right MCP tool. Returns (tool_name, result_dict)."""
    dept = intent.departments[0] if intent.departments else None

    if intent.metric == "attrition":
        return "calculate_attrition", analytics_tools.calculate_attrition(
            dept, intent.start_date, intent.end_date, intent.termination_type
        )
    if intent.metric == "headcount":
        return "calculate_headcount", analytics_tools.calculate_headcount(dept, intent.end_date)
    if intent.metric == "new_hires":
        return "calculate_new_hires", analytics_tools.calculate_new_hires(
            dept, intent.start_date, intent.end_date
        )
    if intent.metric == "tenure":
        return "calculate_average_tenure", analytics_tools.calculate_average_tenure(dept, intent.end_date)
    if intent.metric == "salary":
        return "calculate_average_salary", analytics_tools.calculate_average_salary(dept, intent.group_by)
    if intent.metric == "promotion":
        return "calculate_promotion_rate", analytics_tools.calculate_promotion_rate(
            dept, intent.start_date, intent.end_date
        )
    if intent.metric == "overview":
        if dept:
            return "get_department_metrics", analytics_tools.get_department_metrics(dept, intent.end_date)
        return "get_employee_metrics", analytics_tools.get_employee_metrics(intent.end_date)

    raise ValueError(f"Unhandled metric: {intent.metric}")


def _compose_answer_text(tool_name: str, result: dict) -> str:
    if "metric_name" in result:
        return f"{result['metric_name']} was {_headline(result)}."
    if tool_name in ("get_department_metrics", "get_employee_metrics"):
        hc = result.get("headcount", {})
        attr = result.get("trailing_12mo_attrition", {})
        ten = result.get("average_tenure", {})
        parts = []
        if hc:
            parts.append(f"headcount is {hc.get('value')}")
        if attr:
            parts.append(f"trailing-12-month attrition is {attr.get('value')}%")
        if ten:
            parts.append(f"average tenure is {ten.get('value')} years")
        return "Here's the snapshot: " + ", ".join(parts) + "."
    return "Here's what I found."


def _headline(result: dict) -> str:
    unit = result.get("unit")
    value = result.get("value")
    if unit == "%":
        return f"{value}%"
    if unit == "$":
        return f"${value:,.0f}"
    if unit == "years":
        return f"{value} years"
    return str(value)


def _route_question(question: str) -> (object, str, str):
    """
    Resolves a question to a parsed Intent, trying the LLM tool-selection
    layer first (if configured) and falling back to the deterministic
    rule-based parser on any failure or decline. Returns
    (intent_or_none, routing_method, llm_decline_text_or_none).

    A non-None third element means the LLM declined to answer (e.g. it
    recognized a PII/individual-lookup attempt in a shape the upstream
    question-level screen didn't catch, or genuinely didn't know what to
    do) — the caller should relay that text rather than dispatching a tool.
    """
    if not llm_available():
        return parse_intent(question), "rule_based", None

    try:
        route = route_with_llm(question)
    except Exception:
        # Any API/network/parsing failure - fall back to the deterministic
        # parser rather than failing the request.
        return parse_intent(question), "rule_based_fallback_after_llm_error", None

    if route is None:
        return parse_intent(question), "rule_based", None
    if route["kind"] == "text":
        return None, "llm", route["text"]
    if route["kind"] == "sql":
        return route, "llm_sql", None  # handled specially by the caller
    if route["kind"] == "intent":
        return route["intent"], "llm", None

    return parse_intent(question), "rule_based", None  # unreachable, defensive fallback


def answer_question(question: str, token: Optional[str] = None) -> dict:
    """
    Full pipeline entry point. Returns a dict shaped for both the API layer
    and the Streamlit frontend:
        {
          "question", "blocked", "answer", "tool_used", "result",
          "comparison" (optional), "explanation", "confidence",
          "routing_method", "response_time_ms"
        }
    """
    started = time.perf_counter()
    perf_context.reset()
    routing_time_ms = tool_execution_time_ms = validation_time_ms = 0.0
    routing_method = "rule_based"

    def _finish(response: dict) -> dict:
        total_ms = (time.perf_counter() - started) * 1000
        response["response_time_ms"] = int(total_ms)
        response.setdefault("routing_method", routing_method)
        try:
            record_performance(
                question=question,
                tool_used=response.get("tool_used"),
                routing_method=response.get("routing_method"),
                routing_time_ms=routing_time_ms,
                tool_execution_time_ms=tool_execution_time_ms,
                db_query_time_ms=perf_context.get_db_time_ms(),
                validation_time_ms=validation_time_ms,
                total_time_ms=total_ms,
            )
        except Exception:
            pass  # performance logging is best-effort, never blocks an answer
        return response

    user = authenticate(token) if token else authenticate()
    caps = authorize(user)

    # 1) Question-level governance screen — before any tool is even chosen,
    # and before the LLM even sees the question.
    block_reason = check_question_for_pii(question)
    if block_reason:
        log_decision(user.role, question, None, None, "BLOCKED", block_reason)
        return _finish({
            "question": question,
            "blocked": True,
            "answer": "I can't provide personally identifiable employee information. " + block_reason,
            "tool_used": None,
            "result": None,
            "explanation": None,
            "confidence": "n/a",
        })

    # 2) Routing: LLM tool-selection (if configured) with automatic fallback
    # to the deterministic rule-based parser.
    routing_started = time.perf_counter()
    intent, routing_method, llm_decline_text = _route_question(question)
    routing_time_ms = (time.perf_counter() - routing_started) * 1000

    if routing_method == "llm_sql":
        # The LLM chose the run_governed_sql escape hatch. Still fully
        # governed: validate_sql() inside run_governed_sql applies the same
        # field-blocking/aggregate-only rules as everywhere else. Department-
        # scoped roles (hr_manager) can't safely be row-level-restricted on
        # free-form SQL, so they're not permitted to use this path at all.
        if caps.department_scope is not None:
            reason = (
                f"Your role is scoped to the {caps.department_scope} department, which can't be "
                "safely verified against a free-form SQL query. Try asking with a specific metric instead."
            )
            log_decision(user.role, question, "run_governed_sql", intent["sql"], "BLOCKED", reason)
            return _finish({
                "question": question, "blocked": True, "answer": reason,
                "tool_used": None, "result": None, "explanation": None, "confidence": "n/a",
            })

        tool_started = time.perf_counter()
        sql_result = run_governed_sql(intent["sql"], user_role=user.role)
        tool_execution_time_ms = (time.perf_counter() - tool_started) * 1000

        if not sql_result["allowed"]:
            return _finish({
                "question": question, "blocked": True,
                "answer": f"I can't run that query: {sql_result['message']}",
                "tool_used": None, "result": None, "explanation": None, "confidence": "n/a",
            })

        return _finish({
            "question": question,
            "blocked": False,
            "answer": f"Found {sql_result['row_count']} row(s).",
            "tool_used": "run_governed_sql",
            "result": {"rows": sql_result["rows"], "sql_executed": sql_result["sql_executed"]},
            "explanation": {"sql_executed": sql_result["sql_executed"], "row_count": sql_result["row_count"]},
            "confidence": "high",
            "user_role": user.role,
        })

    if llm_decline_text is not None:
        log_decision(user.role, question, None, None, "ALLOWED", result_summary="llm_declined")
        return _finish({
            "question": question,
            "blocked": False,
            "answer": llm_decline_text,
            "tool_used": None,
            "result": None,
            "explanation": None,
            "confidence": "low",
        })

    if intent.confidence == "low":
        log_decision(user.role, question, None, None, "ALLOWED", result_summary="clarification_requested")
        examples = "\n".join(f"  - {e}" for e in CLARIFICATION_EXAMPLES)
        return _finish({
            "question": question,
            "blocked": False,
            "answer": (
                "I'm not confident which metric you're asking about. Could you rephrase, "
                "or try one of these?\n" + examples
            ),
            "tool_used": None,
            "result": None,
            "explanation": None,
            "confidence": intent.confidence,
        })

    # 2.5) RBAC — department-scoped roles (hr_manager) can't see other
    # departments' data. This runs after intent parsing (we need to know
    # which department(s) were asked about) and before any tool executes.
    rbac_block_reason = _apply_rbac_scope(intent, caps)
    if rbac_block_reason:
        log_decision(user.role, question, None, None, "BLOCKED", rbac_block_reason)
        return _finish({
            "question": question,
            "blocked": True,
            "answer": rbac_block_reason,
            "tool_used": None,
            "result": None,
            "explanation": None,
            "confidence": intent.confidence,
        })

    # 3) Comparison mode: run the metric for each department, side by side.
    if intent.compare and len(intent.departments) >= 2:
        results = []
        tool_name = None
        tool_started = time.perf_counter()
        for dept in intent.departments[:2]:
            single_intent = copy.copy(intent)
            single_intent.departments = [dept]
            tool_name, r = _run_metric(single_intent)
            results.append((dept, r))
        tool_execution_time_ms = (time.perf_counter() - tool_started) * 1000

        validation_started = time.perf_counter()
        validations = [validate_result(tool_name, r) for _, r in results]
        validation_time_ms = (time.perf_counter() - validation_started) * 1000
        overall_valid = all(v.is_valid for v in validations)

        a_dept, a_res = results[0]
        b_dept, b_res = results[1]
        answer = (
            f"{a_dept}: {_headline(a_res)} vs {b_dept}: {_headline(b_res)} "
            f"({a_res.get('metric_name', tool_name)})."
        )

        # Note: each _run_metric() call above already logged its own tool
        # invocation via analytics_tools._log — no separate log_decision here,
        # so a comparison question logs exactly two audit rows (one per side),
        # not three.

        return _finish({
            "question": question,
            "blocked": False,
            "answer": answer,
            "tool_used": tool_name,
            "result": None,
            "comparison": [
                {"department": a_dept, "result": a_res},
                {"department": b_dept, "result": b_res},
            ],
            "explanation": {a_dept: a_res, b_dept: b_res},
            "confidence": intent.confidence,
            "validation": {"is_valid": overall_valid, "notes": [v.notes for v in validations]},
            "user_role": user.role,
        })

    # 4) Single-metric path.
    try:
        tool_started = time.perf_counter()
        tool_name, result = _run_metric(intent)
        tool_execution_time_ms = (time.perf_counter() - tool_started) * 1000
    except ValueError as e:
        log_decision(user.role, question, None, None, "BLOCKED", str(e))
        return _finish({
            "question": question,
            "blocked": True,
            "answer": "I couldn't map that question to a governed analytics tool.",
            "tool_used": None,
            "result": None,
            "explanation": None,
            "confidence": intent.confidence,
        })

    # 5) Validation layer — sanity-check before the number reaches the analyst.
    validation_started = time.perf_counter()
    validation = validate_result(tool_name, result)
    validation_time_ms = (time.perf_counter() - validation_started) * 1000

    # 6) LLM explanation layer (optional): rephrase the deterministic result
    # into a nicer sentence. It never sees anything but the already-computed
    # JSON, so it can't introduce a number that isn't already there — on any
    # failure this just falls back to the plain template below.
    answer_text = None
    if llm_available():
        answer_text = generate_llm_explanation(question, result)
    if answer_text is None:
        answer_text = _compose_answer_text(tool_name, result)

    if intent.auto_scoped:
        answer_text += f" (Scoped to your department, {intent.departments[0]}.)"
    if not validation.is_valid:
        answer_text += (
            " ⚠️ Note: this result failed an automated sanity check "
            f"({'; '.join(validation.notes)}) — treat it as provisional."
        )

    # Note: the tool call above (analytics_tools.calculate_* / get_*_metrics)
    # already logged its own audit row via _log() — logging again here would
    # double-count every question in query_log.

    return _finish({
        "question": question,
        "blocked": False,
        "answer": answer_text,
        "tool_used": tool_name,
        "result": result,
        "explanation": result,
        "confidence": intent.confidence,
        "validation": validation.__dict__,
        "user_role": user.role,
    })
