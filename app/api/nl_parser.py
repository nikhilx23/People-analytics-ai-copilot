"""
Rule-based natural-language intent parser — the "AI Copilot" layer.

This deliberately does NOT call an LLM. It maps an analyst's question to one
of the governed MCP tools plus structured parameters, using keyword/pattern
matching. That keeps translation deterministic and inspectable end-to-end,
and the architecture is unchanged if you later swap this module for an LLM
that chooses among the same tool signatures (see README "Swapping in a real
LLM"): the tool contracts, governance layer, and validation layer don't move.

A low-confidence match returns confidence="low" rather than guessing, so the
copilot can ask a clarifying question instead of silently answering the
wrong metric.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from typing import List, Optional

from sqlalchemy import text

from app.database.connection import get_connection

DEPARTMENT_ALIASES = {
    "hr": "Human Resources",
    "human resources": "Human Resources",
    "eng": "Engineering",
    "engineering": "Engineering",
    "sales": "Sales",
    "marketing": "Marketing",
    "support": "Customer Support",
    "customer support": "Customer Support",
    "finance": "Finance",
    "product": "Product",
    "operations": "Operations",
    "ops": "Operations",
}


@lru_cache(maxsize=1)
def _known_departments() -> tuple:
    # Cached — see the note on app.governance.policies._employee_names for
    # why (this was hit on every single question, and the roster is static).
    conn = get_connection()
    try:
        rows = conn.execute(text("SELECT DISTINCT department FROM employees")).mappings().all()
    finally:
        conn.close()
    return tuple(r["department"] for r in rows)


@dataclass
class Intent:
    raw_question: str
    metric: Optional[str] = None            # attrition | headcount | new_hires | tenure | salary | promotion | overview
    departments: List[str] = field(default_factory=list)
    compare: bool = False
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    termination_type: str = "all"
    group_by: Optional[str] = None
    confidence: str = "low"                 # low | medium | high
    auto_scoped: bool = False               # set by RBAC enforcement when a department-scoped
                                             # role's company-wide question was narrowed automatically


_METRIC_KEYWORDS = [
    (re.compile(r"\battrition|turnover|left the company|leaving|departed\b", re.I), "attrition"),
    (re.compile(r"\bjoined|new hires?|hired\b", re.I), "new_hires"),
    (re.compile(r"\bheadcount|how many employees are|number of employees\b", re.I), "headcount"),
    (re.compile(r"\btenure|how long.*(stay|employed)\b", re.I), "tenure"),
    (re.compile(r"\bsalary|compensation|pay\b(?!.*\bmy\b)", re.I), "salary"),
    (re.compile(r"\bpromot(ed|ion)\b", re.I), "promotion"),
]

_VOLUNTARY_RE = re.compile(r"\bvoluntary\b", re.I)
_INVOLUNTARY_RE = re.compile(r"\binvoluntary\b", re.I)
_COMPARE_RE = re.compile(r"\bcompare|\bvs\.?\b|\bversus\b", re.I)
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _extract_dates(question: str) -> (Optional[str], Optional[str]):
    q = question.lower()
    today = date.today()

    iso_dates = _ISO_DATE_RE.findall(question)
    if len(iso_dates) >= 2:
        return iso_dates[0], iso_dates[1]

    if "this year" in q:
        return date(today.year, 1, 1).isoformat(), today.isoformat()

    if "last year" in q:
        return date(today.year - 1, 1, 1).isoformat(), date(today.year - 1, 12, 31).isoformat()

    m = re.search(r"last (\d+) months?", q)
    if m:
        months = int(m.group(1))
        from dateutil.relativedelta import relativedelta
        start = today - relativedelta(months=months)
        return start.isoformat(), today.isoformat()

    m = re.search(r"last (\d+) years?", q)
    if m:
        years = int(m.group(1))
        from dateutil.relativedelta import relativedelta
        start = today - relativedelta(years=years)
        return start.isoformat(), today.isoformat()

    if "12 months" in q or "past year" in q or "trailing year" in q:
        from dateutil.relativedelta import relativedelta
        start = today - relativedelta(months=12)
        return start.isoformat(), today.isoformat()

    # No explicit period mentioned - caller applies its own default (trailing 12mo).
    return None, None


def parse_intent(question: str) -> Intent:
    intent = Intent(raw_question=question)

    q_lower = question.lower()

    # --- metric ---
    for pattern, metric in _METRIC_KEYWORDS:
        if pattern.search(question):
            intent.metric = metric
            break

    # --- departments (kept in the order they appear in the question, so a
    # "compare X and Y" question reports X first, not DB iteration order) ---
    found = {}  # department -> earliest match position in q_lower
    known = _known_departments()
    for dept in known:
        pos = q_lower.find(dept.lower())
        if pos != -1:
            found[dept] = pos
    if not found:
        for alias, canonical in DEPARTMENT_ALIASES.items():
            m = re.search(rf"\b{re.escape(alias)}\b", q_lower)
            if m and canonical not in found:
                found[canonical] = m.start()
    intent.departments = [d for d, _ in sorted(found.items(), key=lambda kv: kv[1])]

    # --- compare mode ---
    if _COMPARE_RE.search(question) and len(intent.departments) >= 2:
        intent.compare = True

    # --- termination type ---
    if _VOLUNTARY_RE.search(question):
        intent.termination_type = "voluntary"
    elif _INVOLUNTARY_RE.search(question):
        intent.termination_type = "involuntary"

    # --- dates ---
    start, end = _extract_dates(question)
    intent.start_date, intent.end_date = start, end

    # --- group_by (for salary-by-department style questions) ---
    if intent.metric == "salary" and ("by department" in q_lower or "each department" in q_lower or "per department" in q_lower):
        intent.group_by = "department"
    if intent.metric == "salary" and ("by level" in q_lower or "job level" in q_lower):
        intent.group_by = "job_level"

    # --- confidence ---
    overview_keywords = ("how is", "how's", "overview", "doing", "summary", "snapshot")
    if intent.metric:
        # A recognized metric keyword is enough to route confidently, whether
        # or not a specific department was named (company-wide is a valid scope).
        intent.confidence = "high"
    elif intent.departments and any(kw in q_lower for kw in overview_keywords):
        # No specific metric, but a department + an "overview" phrasing -> route
        # to the composite department snapshot tool.
        intent.metric = "overview"
        intent.confidence = "medium"
    else:
        intent.confidence = "low"

    return intent
