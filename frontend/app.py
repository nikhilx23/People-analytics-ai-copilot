"""
Streamlit frontend for the People Analytics AI Copilot.

Run:
    streamlit run frontend/app.py

Talks to the copilot pipeline in-process (same code path FastAPI's /ask
endpoint uses) so the demo doesn't require a separate backend process to be
running — but everything shown here (governance, validation, MCP tools) is
exactly what app/api/main.py exposes over HTTP too.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from app.database.connection import DB_PATH, is_postgres

# Auto-initialize the demo SQLite database on first run. data/employee_analytics.db
# is gitignored on purpose (it's generated data, not source), so a fresh checkout —
# e.g. a new Streamlit Community Cloud deploy — won't have it yet. The CSV it's
# built from (data/synthetic_employee_data.csv) IS committed, so we can build the
# database from it automatically instead of requiring a manual setup step.
if not is_postgres() and not DB_PATH.exists():
    from app.database.load_data import main as _load_data
    with st.spinner("First run on this deployment: setting up the demo database…"):
        _load_data()
        
from app.api.copilot import CLARIFICATION_EXAMPLES, answer_question
from app.api.feedback_store import get_feedback_stats, save_feedback
from app.evaluation.eval_runner import run_eval
from app.governance.permissions import authenticate, authorize

st.set_page_config(page_title="People Analytics AI Copilot", page_icon="📊", layout="wide")

if "history" not in st.session_state:
    st.session_state.history = []  # list of dicts: question, response

st.title("📊 People Analytics AI Copilot")
st.caption(
    "Governed natural-language analytics over a synthetic employee dataset. "
    "Every answer is produced by a versioned analytical method through an MCP tool — "
    "never by the AI freehand-calculating a number."
)

# ------------------------------------------------------------------ #
# Role switcher (sidebar) — demonstrates RBAC without a real Entra ID
# tenant. See app/governance/permissions.py; swap for real Entra ID
# sign-in by setting ENTRA_TENANT_ID/ENTRA_CLIENT_ID and this dropdown's
# token can be replaced with the signed-in user's token.
# ------------------------------------------------------------------ #
ROLE_TOKENS = {
    "HR Analyst (company-wide)": "demo-analyst-token",
    "HR Manager (Engineering only)": "demo-manager-token",
    "HR Admin (+ audit/performance access)": "demo-admin-token",
}
with st.sidebar:
    st.subheader("Signed in as")
    role_label = st.selectbox("Role", list(ROLE_TOKENS.keys()))
    token = ROLE_TOKENS[role_label]
    user = authenticate(token)
    caps = authorize(user)
    st.caption(f"Auth method: `{user.auth_method}`")
    if caps.department_scope:
        st.info(f"Scoped to **{caps.department_scope}** department only.")
    if caps.can_view_admin_dashboards:
        st.success("Has audit log + performance dashboard access.")

st.session_state["_token"] = token

tab_chat, tab_dashboard, tab_eval, tab_audit, tab_perf = st.tabs(
    ["💬 Ask a question", "📈 Feedback dashboard", "✅ Evaluation",
     "🔍 Governance audit log", "⚡ Performance"]
)

# ------------------------------------------------------------------ #
# Tab 1: Chat
# ------------------------------------------------------------------ #
with tab_chat:
    col_main, col_side = st.columns([2.3, 1])

    with col_side:
        st.subheader("Try an example")
        for ex in CLARIFICATION_EXAMPLES:
            if st.button(ex, key=f"ex_{ex}", use_container_width=True):
                st.session_state["pending_question"] = ex
        st.subheader("Try a restricted question")
        for ex in ["What is John Smith's salary?", "List all employees with their salaries"]:
            if st.button(ex, key=f"restricted_{ex}", use_container_width=True):
                st.session_state["pending_question"] = ex

    with col_main:
        default_q = st.session_state.pop("pending_question", "")
        question = st.text_input(
            "Ask a question about the workforce",
            value=default_q,
            placeholder="e.g. What was the voluntary attrition rate in Engineering over the last 12 months?",
        )
        ask_clicked = st.button("Ask", type="primary")

        if ask_clicked and question.strip():
            with st.spinner("Routing through governed MCP tools..."):
                response = answer_question(question, st.session_state["_token"])
            st.session_state.history.insert(0, {"question": question, "response": response})

        for i, turn in enumerate(st.session_state.history):
            q, r = turn["question"], turn["response"]
            st.markdown(f"**You:** {q}")

            if r["blocked"]:
                st.error(r["answer"])
            elif r.get("confidence") == "low":
                st.warning(r["answer"])
            else:
                st.success(r["answer"])

                if r.get("comparison"):
                    df = pd.DataFrame([
                        {"Department": c["department"], "Value": c["result"]["value"], "Unit": c["result"]["unit"]}
                        for c in r["comparison"]
                    ])
                    st.bar_chart(df.set_index("Department")["Value"])

                with st.expander("🔎 Explain this answer"):
                    explanation = r.get("explanation")
                    if isinstance(explanation, dict) and "metric_name" in explanation:
                        st.markdown(f"**Method:** {explanation['method_name']} {explanation['method_version']}")
                        st.markdown(f"**Formula:** {explanation['formula']}")
                        if explanation.get("filters_applied"):
                            filt = {k: v for k, v in explanation["filters_applied"].items() if v}
                            if filt:
                                st.markdown(f"**Filters:** {filt}")
                        st.markdown("**Breakdown:**")
                        st.json(explanation.get("breakdown", {}))
                        st.markdown(f"**Data source:** {explanation.get('data_source')}")
                        st.markdown(f"**Computed at:** {explanation.get('computed_at')}")
                    elif isinstance(explanation, dict):
                        for dept, res in explanation.items():
                            st.markdown(f"**{dept}** — {res.get('method_name')} {res.get('method_version')}")
                            st.markdown(f"Formula: {res.get('formula')}")
                            st.json(res.get("breakdown", {}))
                    else:
                        st.write("No structured explanation available for this response.")

                    st.caption(f"Tool used: `{r.get('tool_used')}` · Response time: {r.get('response_time_ms')} ms")

                validation = r.get("validation")
                if validation and not validation.get("is_valid", True):
                    st.warning(f"Validation flagged this result: {validation.get('notes')}")

                fb_col1, fb_col2, fb_col3 = st.columns([1, 1, 3])
                with fb_col1:
                    if st.button("👍 Correct", key=f"up_{i}"):
                        save_feedback(q, r.get("tool_used"), r.get("answer"), "up",
                                      response_time_ms=r.get("response_time_ms"))
                        st.toast("Thanks for the feedback!")
                with fb_col2:
                    if st.button("👎 Incorrect", key=f"down_{i}"):
                        save_feedback(q, r.get("tool_used"), r.get("answer"), "down",
                                      error_type="marked_incorrect", response_time_ms=r.get("response_time_ms"))
                        st.toast("Thanks — logged for review.")
                with fb_col3:
                    issue = st.text_input("Report an issue (optional)", key=f"issue_{i}", label_visibility="collapsed",
                                           placeholder="Describe what's wrong, then press Enter")
                    if issue:
                        save_feedback(q, r.get("tool_used"), r.get("answer"), "report",
                                      error_type=issue, response_time_ms=r.get("response_time_ms"))
                        st.toast("Issue reported.")

            st.divider()

# ------------------------------------------------------------------ #
# Tab 2: Feedback / product dashboard
# ------------------------------------------------------------------ #
with tab_dashboard:
    st.subheader("Analyst feedback loop")
    stats = get_feedback_stats()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total feedback", stats["total_feedback"])
    c2.metric("Satisfaction", f"{stats['satisfaction_pct']}%" if stats["satisfaction_pct"] is not None else "—")
    c3.metric("Avg response time", f"{stats['avg_response_time_ms']} ms" if stats["avg_response_time_ms"] else "—")
    c4.metric("Reported issues", stats["reports"])

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Most-used tools**")
        if stats["most_used_tools"]:
            st.bar_chart(pd.DataFrame(stats["most_used_tools"]).set_index("tool_used")["c"])
        else:
            st.caption("No feedback recorded yet — ask a question and rate it in the Chat tab.")
    with col_b:
        st.markdown("**Most common questions**")
        if stats["most_common_questions"]:
            st.dataframe(pd.DataFrame(stats["most_common_questions"]), use_container_width=True, hide_index=True)
        else:
            st.caption("No feedback recorded yet.")

    st.markdown("**Recently flagged (👎 / reported) questions**")
    if stats["failed_questions"]:
        st.dataframe(pd.DataFrame(stats["failed_questions"]), use_container_width=True, hide_index=True)
    else:
        st.caption("Nothing flagged yet.")

# ------------------------------------------------------------------ #
# Tab 3: Evaluation
# ------------------------------------------------------------------ #
with tab_eval:
    st.subheader("AI answer evaluation")
    st.caption(
        "Runs every question in the evaluation dataset through the full pipeline and checks the "
        "result against an independently computed reference value — not the same code path being tested."
    )
    if st.button("Run evaluation now"):
        with st.spinner("Running evaluation..."):
            summary = run_eval()
        st.session_state["eval_summary"] = summary

    summary = st.session_state.get("eval_summary")
    if summary:
        st.metric("Answer accuracy", f"{summary['accuracy_pct']}%", f"{summary['correct']}/{summary['total']} correct")
        df = pd.DataFrame(summary["results"])
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("Click 'Run evaluation now' to see current accuracy.")

# ------------------------------------------------------------------ #
# Tab 4: Governance audit log (hr_admin only — this is a real RBAC gate,
# not cosmetic: caps comes from the same authorize() the API enforces)
# ------------------------------------------------------------------ #
with tab_audit:
    if not caps.can_view_admin_dashboards:
        st.error(f"🔒 Role '{user.role}' does not have audit log access. Switch to HR Admin in the sidebar.")
    else:
        st.subheader("Governance decisions")
        st.caption("Every question is logged with whether it was allowed or blocked, and why.")
        from sqlalchemy import text
        from app.database.connection import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(text("SELECT * FROM query_log ORDER BY id DESC LIMIT 100")).mappings().all()
        finally:
            conn.close()

        if rows:
            st.dataframe(pd.DataFrame([dict(r) for r in rows]), use_container_width=True, hide_index=True)
        else:
            st.caption("No governance decisions logged yet — ask a question in the Chat tab.")

# ------------------------------------------------------------------ #
# Tab 5: Performance dashboard (hr_admin only)
# ------------------------------------------------------------------ #
with tab_perf:
    if not caps.can_view_admin_dashboards:
        st.error(f"🔒 Role '{user.role}' does not have performance dashboard access. Switch to HR Admin in the sidebar.")
    else:
        try:
            from app.api.performance import get_performance_stats
            perf = get_performance_stats()
        except ImportError:
            perf = None

        if not perf:
            st.caption("Performance instrumentation not available yet.")
        elif perf["count"] == 0:
            st.caption("No timed requests yet — ask a question in the Chat tab.")
        else:
            st.subheader("Latency")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Average", f"{perf['total']['avg_ms']} ms")
            c2.metric("P95", f"{perf['total']['p95_ms']} ms")
            c3.metric("Fastest", f"{perf['total']['min_ms']} ms")
            c4.metric("Slowest", f"{perf['total']['max_ms']} ms")

            st.markdown("**By pipeline stage (average ms)**")
            stage_df = pd.DataFrame([
                {"Stage": k, "Avg ms": v["avg_ms"]} for k, v in perf["by_stage"].items()
            ])
            st.bar_chart(stage_df.set_index("Stage")["Avg ms"])

            st.markdown("**Slowest tools (average ms)**")
            if perf["by_tool"]:
                tool_df = pd.DataFrame([
                    {"Tool": k, "Avg ms": v["avg_ms"], "Calls": v["count"]} for k, v in perf["by_tool"].items()
                ]).sort_values("Avg ms", ascending=False)
                st.dataframe(tool_df, use_container_width=True, hide_index=True)
