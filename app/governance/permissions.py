"""
Authentication & Authorization.

Full pipeline this module represents:

    User -> Authentication -> Authorization -> Data policy -> MCP Tool -> Database

Two authentication backends are supported, chosen automatically:

    - Real Microsoft Entra ID (app/governance/entra_auth.py), activated when
      ENTRA_TENANT_ID / ENTRA_CLIENT_ID are configured — verifies a real
      Azure AD JWT and reads the caller's App Role from its claims.
    - A demo token->user map (this module), used whenever Entra ID isn't
      configured. This keeps the app fully runnable without an Azure tenant.

Either way, authorization (role -> capabilities) is real and enforced the
same way: nobody, at any role, gets individual-employee PII through the
copilot — that boundary is not something a higher role lifts. What
differs by role is aggregate scope (company-wide vs. one department) and
access to the audit/admin surfaces.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class User:
    user_id: str
    display_name: str
    role: str                       # "hr_analyst" | "hr_manager" | "hr_admin"
    department: Optional[str] = None  # set for hr_manager: the one department they're scoped to
    auth_method: str = "demo_stub"    # "demo_stub" | "entra_id"


@dataclass(frozen=True)
class RoleCapabilities:
    can_query_aggregates: bool = True
    can_query_individual_pii: bool = False   # never true for any role in this app
    can_run_raw_sql: bool = False            # governed run_governed_sql tool only, never direct DB access
    max_rows_returned: int = 500
    department_scope: Optional[str] = None   # None = company-wide; a string = restricted to that department
    can_view_audit_log: bool = False
    can_view_admin_dashboards: bool = False


# ---------------------------------------------------------------- #
# Role -> capabilities. Note department_scope for hr_manager is resolved
# per-user in authorize() (from user.department), not fixed here.
# ---------------------------------------------------------------- #

ROLE_TEMPLATES = {
    "hr_analyst": RoleCapabilities(
        can_query_aggregates=True,
        can_query_individual_pii=False,
        can_run_raw_sql=False,
        max_rows_returned=500,
        department_scope=None,          # company-wide aggregate access
        can_view_audit_log=False,
        can_view_admin_dashboards=False,
    ),
    "hr_manager": RoleCapabilities(
        can_query_aggregates=True,
        can_query_individual_pii=False,
        can_run_raw_sql=False,
        max_rows_returned=500,
        department_scope="__USER_DEPARTMENT__",  # resolved to user.department in authorize()
        can_view_audit_log=False,
        can_view_admin_dashboards=False,
    ),
    # hr_admin does NOT get raw SQL or individual PII either — the elevated
    # capability is visibility into governance/audit/performance surfaces,
    # not looser data access. That's a deliberate product choice: individual
    # lookups belong in the system of record, not a chat interface, for any
    # role.
    "hr_admin": RoleCapabilities(
        can_query_aggregates=True,
        can_query_individual_pii=False,
        can_run_raw_sql=False,
        max_rows_returned=2000,
        department_scope=None,
        can_view_audit_log=True,
        can_view_admin_dashboards=True,
    ),
}

NO_ACCESS = RoleCapabilities(
    can_query_aggregates=False, can_query_individual_pii=False,
    can_run_raw_sql=False, max_rows_returned=0,
)

# ---------------------------------------------------------------- #
# Demo auth backend (fallback when Entra ID isn't configured)
# ---------------------------------------------------------------- #

_DEMO_USERS = {
    "demo-analyst-token": User(user_id="u-1001", display_name="Demo Analyst", role="hr_analyst"),
    "demo-manager-token": User(user_id="u-1002", display_name="Demo Engineering Manager",
                                role="hr_manager", department="Engineering"),
    "demo-admin-token": User(user_id="u-1003", display_name="Demo Admin", role="hr_admin"),
}

DEFAULT_TOKEN = "demo-analyst-token"


def _authenticate_demo(token: str) -> User:
    user = _DEMO_USERS.get(token)
    if user is None:
        raise PermissionError("Authentication failed: unrecognized token.")
    return user


def entra_id_configured() -> bool:
    return bool(os.environ.get("ENTRA_TENANT_ID") and os.environ.get("ENTRA_CLIENT_ID"))


def authenticate(token: str = DEFAULT_TOKEN) -> User:
    """
    Resolve a bearer token to a User. Uses real Entra ID token validation
    when configured (ENTRA_TENANT_ID / ENTRA_CLIENT_ID set), otherwise falls
    back to the demo token map so the app runs without an Azure tenant.
    """
    if entra_id_configured():
        from app.governance.entra_auth import authenticate_entra_token
        return authenticate_entra_token(token)
    return _authenticate_demo(token)


def authorize(user: User) -> RoleCapabilities:
    """Resolve a User's role (+ department, for managers) to its capability set."""
    template = ROLE_TEMPLATES.get(user.role)
    if template is None:
        return NO_ACCESS

    if template.department_scope == "__USER_DEPARTMENT__":
        return RoleCapabilities(
            can_query_aggregates=template.can_query_aggregates,
            can_query_individual_pii=template.can_query_individual_pii,
            can_run_raw_sql=template.can_run_raw_sql,
            max_rows_returned=template.max_rows_returned,
            department_scope=user.department,
            can_view_audit_log=template.can_view_audit_log,
            can_view_admin_dashboards=template.can_view_admin_dashboards,
        )
    return template
