"""
FastAPI backend for the People Analytics AI Copilot.

Endpoints:
    POST /ask         - ask a natural-language question, get a governed answer
    POST /feedback     - record 👍 / 👎 / report-issue on a prior answer
    GET  /stats        - feedback-loop dashboard data (most-used tools, accuracy signals, etc.)
    GET  /audit-log    - recent governance decisions (hr_admin role only)
    GET  /performance  - latency dashboard data (hr_admin role only)
    GET  /health       - liveness check

Auth: pass a bearer token either as `Authorization: Bearer <token>` (the
standard way — this is what a real Entra ID-issued token would use) or in
the /ask request body's `token` field (kept for quick manual testing). The
header takes precedence when both are present. With no Entra ID configured
(see .env.example), demo tokens work: demo-analyst-token, demo-manager-token,
demo-admin-token.

Run:
    uvicorn app.api.main:app --reload --port 8000
"""

from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text

from app.api.copilot import answer_question
from app.api.feedback_store import get_feedback_stats, save_feedback
from app.database.connection import get_connection
from app.governance.permissions import authenticate, authorize

app = FastAPI(
    title="People Analytics AI Copilot",
    description="Governed natural-language analytics over an employee dataset, via an MCP tool layer.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str
    token: Optional[str] = None


class FeedbackRequest(BaseModel):
    question: str
    tool_used: Optional[str] = None
    answer: Optional[str] = None
    feedback: str  # 'up' | 'down' | 'report'
    error_type: Optional[str] = None
    response_time_ms: Optional[int] = None


def _bearer_token(authorization: Optional[str] = Header(default=None)) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return None


def require_admin(token: Optional[str] = Depends(_bearer_token)):
    """FastAPI dependency: 401 for an unrecognized token, 403 if the caller
    isn't authorized for admin-only surfaces (audit log, performance stats)."""
    try:
        user = authenticate(token) if token else authenticate()
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    caps = authorize(user)
    if not caps.can_view_admin_dashboards:
        raise HTTPException(status_code=403, detail=f"Role '{user.role}' does not have admin dashboard access.")
    return user


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ask")
def ask(req: AskRequest, header_token: Optional[str] = Depends(_bearer_token)):
    token = header_token or req.token
    return answer_question(req.question, token)


@app.post("/feedback")
def feedback(req: FeedbackRequest):
    save_feedback(
        question=req.question,
        tool_used=req.tool_used,
        answer=req.answer,
        feedback=req.feedback,
        error_type=req.error_type,
        response_time_ms=req.response_time_ms,
    )
    return {"status": "recorded"}


@app.get("/stats")
def stats():
    return get_feedback_stats()


@app.get("/audit-log")
def audit_log(limit: int = 50, admin=Depends(require_admin)):
    conn = get_connection()
    try:
        rows = conn.execute(
            text("SELECT * FROM query_log ORDER BY id DESC LIMIT :limit"), {"limit": limit}
        ).mappings().all()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@app.get("/performance")
def performance(admin=Depends(require_admin)):
    from app.api.performance import get_performance_stats
    return get_performance_stats()
