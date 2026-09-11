"""
LLM tool-selection layer.

    User question -> PII screen -> LLM -> tool selection -> MCP -> governance
    -> database -> validation -> LLM explanation

The LLM's only job is picking ONE OF THE GOVERNED MCP TOOLS (and its
parameters) — it never writes free-form SQL that gets executed directly, and
it never computes a metric itself. The tool schemas handed to the model are
pulled live from app.mcp.server (the same FastMCP tool registry an MCP
client would see), so there is exactly one place these tool contracts are
defined.

Activation is automatic: set ANTHROPIC_API_KEY (see .env.example) and this
module is used; leave it unset and app.api.copilot falls back to the
deterministic rule-based parser (app.api.nl_parser) automatically, on every
call, with no other configuration needed. Any error talking to the API
(missing key, network failure, malformed response, an unrecognized tool
name) also falls back to the rule-based parser rather than failing the
request — a governed-but-dumber answer beats no answer.
"""

import asyncio
import os
from datetime import date
from typing import Optional

from app.api.nl_parser import Intent

TOOL_TO_METRIC = {
    "calculate_attrition": "attrition",
    "calculate_headcount": "headcount",
    "calculate_new_hires": "new_hires",
    "calculate_average_tenure": "tenure",
    "calculate_average_salary": "salary",
    "calculate_promotion_rate": "promotion",
    "get_department_metrics": "overview",
    "get_employee_metrics": "overview",
}

SYSTEM_PROMPT = """You are the tool-selection layer for a governed HR People Analytics copilot.

Your ONLY job is to pick the single best tool from the ones provided and \
fill in its parameters based on the analyst's question. You must NEVER \
invent a number yourself — every figure in the final answer comes from the \
tool result, not from you.

Rules:
- If the question compares two departments, call the relevant \
  calculate_/get_ tool TWICE in the same turn, once per department (two \
  tool_use blocks), rather than trying to answer the comparison yourself.
- Use exact department names as they appear in the dataset (e.g. \
  "Engineering", "Human Resources", "Customer Support", "Sales", \
  "Marketing", "Finance", "Product", "Operations").
- Convert relative date phrases ("last 12 months", "this year", "last \
  year") into explicit start_date/end_date ISO dates yourself; today's date \
  is {today}.
- Prefer the specific calculate_*/get_*_metrics tools. Only use \
  run_governed_sql for a question those tools genuinely can't answer, and \
  even then write ONLY a read-only, aggregate SELECT — never select \
  full_name, and never select salary or performance_rating outside an \
  aggregate function (AVG/SUM/MIN/MAX/COUNT).
- If the question asks about a specific named individual (a person's name, \
  not a department), or asks for a raw row-level export, do NOT call any \
  tool — respond with plain text explaining that you can't provide \
  individual employee records, only aggregate analytics.
- If the question doesn't map to any available tool at all, do not call a \
  tool — respond with a short plain-text explanation that you're not sure \
  what metric they're asking about.
"""


def llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


_tool_schema_cache = None


def _get_anthropic_tool_schemas() -> list:
    global _tool_schema_cache
    if _tool_schema_cache is None:
        from app.mcp.server import mcp
        mcp_tools = asyncio.run(mcp.list_tools())
        _tool_schema_cache = [
            {"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
            for t in mcp_tools
        ]
    return _tool_schema_cache


def _tool_use_to_intent(tool_name: str, tool_input: dict) -> Optional[Intent]:
    metric = TOOL_TO_METRIC.get(tool_name)
    if metric is None:
        return None  # run_governed_sql, or an unrecognized tool name

    intent = Intent(raw_question="", metric=metric, confidence="high")
    dept = tool_input.get("department")
    intent.departments = [dept] if dept else []
    intent.start_date = tool_input.get("start_date")
    intent.end_date = tool_input.get("end_date") or tool_input.get("as_of_date")
    intent.termination_type = tool_input.get("termination_type", "all")
    intent.group_by = tool_input.get("group_by")
    return intent


def route_with_llm(question: str) -> Optional[dict]:
    """
    Returns one of:
        {"kind": "intent", "intent": Intent, "compare": bool}
        {"kind": "sql", "sql": "<query>"}
        {"kind": "text", "text": "<the model's plain-text reply>"}
        None  — no ANTHROPIC_API_KEY configured (caller should use the
                rule-based parser directly, this is not an error)
    Raises on API/network/parsing failure — callers should catch broadly
    and fall back to the rule-based parser rather than failing the request.
    """
    if not llm_available():
        return None

    import anthropic

    client = anthropic.Anthropic()
    model = os.environ.get("LLM_MODEL", "claude-sonnet-5")

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT.format(today=date.today().isoformat()),
        tools=_get_anthropic_tool_schemas(),
        messages=[{"role": "user", "content": question}],
    )

    tool_uses = [b for b in response.content if b.type == "tool_use"]
    text_blocks = [b.text for b in response.content if b.type == "text"]

    if not tool_uses:
        return {"kind": "text", "text": " ".join(text_blocks).strip() or
                "I'm not sure how to answer that with the tools available."}

    if any(t.name == "run_governed_sql" for t in tool_uses):
        sql_call = next(t for t in tool_uses if t.name == "run_governed_sql")
        return {"kind": "sql", "sql": sql_call.input.get("sql", "")}

    if len(tool_uses) >= 2:
        a, b = tool_uses[0], tool_uses[1]
        intent_a = _tool_use_to_intent(a.name, a.input)
        intent_b = _tool_use_to_intent(b.name, b.input)
        if intent_a and intent_b and intent_a.metric == intent_b.metric:
            merged = intent_a
            merged.departments = (intent_a.departments or []) + (intent_b.departments or [])
            merged.compare = True
            return {"kind": "intent", "intent": merged}
        # Mismatched/unrecognized pair — fall through to using just the first call.

    intent = _tool_use_to_intent(tool_uses[0].name, tool_uses[0].input)
    if intent is None:
        return {"kind": "text", "text": "I couldn't map that question to a governed analytics tool."}
    return {"kind": "intent", "intent": intent}


def generate_llm_explanation(question: str, result: dict) -> Optional[str]:
    """
    Rephrases an already-computed, governed result into a natural-language
    sentence. Never asked to compute anything — the JSON handed to it is the
    full and final numeric answer; its only job is phrasing. Returns None
    (caller falls back to the deterministic template) on any failure.
    """
    if not llm_available():
        return None
    try:
        import anthropic
        import json

        client = anthropic.Anthropic()
        model = os.environ.get("LLM_MODEL", "claude-sonnet-5")
        response = client.messages.create(
            model=model,
            max_tokens=256,
            system=(
                "Rephrase the given governed analytics result as one concise, natural "
                "sentence for an HR analyst. Use ONLY the numbers present in the JSON — "
                "never introduce a number that isn't there. No preamble, just the sentence."
            ),
            messages=[{
                "role": "user",
                "content": f"Question: {question}\n\nResult JSON:\n{json.dumps(result)}",
            }],
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text or None
    except Exception:
        return None
