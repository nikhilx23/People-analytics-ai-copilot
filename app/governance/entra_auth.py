"""
Real Microsoft Entra ID (Azure AD) token validation.

Activated automatically by app.governance.permissions.authenticate() when
ENTRA_TENANT_ID and ENTRA_CLIENT_ID are set in the environment. Validates a
bearer token the way any real Azure AD-protected API would:

    1. Fetch Entra ID's signing keys (JWKS) for the tenant.
    2. Verify the token's RS256 signature against the matching key.
    3. Verify audience (this app's Client ID), issuer (this tenant), and
       expiry.
    4. Read the caller's App Role(s) from the `roles` claim — these are
       assigned to users/groups in the Entra ID app registration, not
       decided by this code — and map them to this app's internal RBAC
       roles.

This module has no way to be exercised against a real Microsoft tenant in
this environment (that requires an actual Azure AD app registration), but
the validation logic itself is real and is exercised in
tests/test_entra_auth.py against a self-signed token and a mocked JWKS
endpoint, which covers the same code path a real token would take.

--- Azure Portal setup (for a real tenant) ---------------------------------
1. Azure Portal -> Microsoft Entra ID -> App registrations -> New registration.
   Note the Application (client) ID and Directory (tenant) ID.
2. App registration -> App roles -> create three roles with these exact
   Value strings (the code below maps on these):
       HR.Analyst   (Display name: "HR Analyst")
       HR.Manager   (Display name: "HR Manager")
       HR.Admin     (Display name: "HR Admin")
3. Enterprise applications -> (this app) -> Users and groups -> assign each
   analyst/manager/admin user to the matching role.
4. For manager department-scoping, set a `department` claim: Token
   configuration -> Add optional claim -> access token -> a claim backed by
   a user attribute (e.g. extension_department), or map it via a
   custom claims mapping policy. Configure the claim name via
   ENTRA_DEPARTMENT_CLAIM if it isn't "department".
5. Set ENTRA_TENANT_ID, ENTRA_CLIENT_ID (and ENTRA_CLIENT_SECRET if this
   API itself needs to call other Microsoft Graph APIs on-behalf-of the
   user) in .env.
-----------------------------------------------------------------------------
"""

import os

import jwt
from jwt import PyJWKClient

from app.governance.permissions import User

# App Role "Value" (as configured in the Entra ID app registration) -> this
# app's internal role name.
ROLE_CLAIM_MAP = {
    "HR.Admin": "hr_admin",
    "HR.Manager": "hr_manager",
    "HR.Analyst": "hr_analyst",
}
# Priority order when a token somehow carries more than one recognized role.
ROLE_PRIORITY = ["hr_admin", "hr_manager", "hr_analyst"]

_jwks_client_cache = {}


class EntraAuthError(PermissionError):
    pass


def _tenant_id() -> str:
    return os.environ["ENTRA_TENANT_ID"]


def _client_id() -> str:
    return os.environ["ENTRA_CLIENT_ID"]


def _issuer() -> str:
    return f"https://login.microsoftonline.com/{_tenant_id()}/v2.0"


def _jwks_uri() -> str:
    return f"https://login.microsoftonline.com/{_tenant_id()}/discovery/v2.0/keys"


def _get_jwks_client(jwks_uri: str) -> PyJWKClient:
    # PyJWKClient caches fetched keys internally; we additionally cache the
    # client itself per JWKS URI so repeated calls don't re-instantiate it.
    if jwks_uri not in _jwks_client_cache:
        _jwks_client_cache[jwks_uri] = PyJWKClient(jwks_uri)
    return _jwks_client_cache[jwks_uri]


def _map_role(claims: dict) -> str:
    token_roles = set(claims.get("roles", []) or [])
    mapped = {ROLE_CLAIM_MAP[r] for r in token_roles if r in ROLE_CLAIM_MAP}
    for candidate in ROLE_PRIORITY:
        if candidate in mapped:
            return candidate
    raise EntraAuthError(
        "This account has no recognized HR analytics role assigned "
        f"(expected one of {list(ROLE_CLAIM_MAP)} in the Entra ID app role assignment)."
    )


def authenticate_entra_token(token: str, jwks_client: PyJWKClient = None) -> User:
    """
    Validates a real Entra ID-issued JWT and returns the mapped User.
    `jwks_client` is an injection point for tests (a client pointed at a
    mocked JWKS endpoint); production code omits it and uses the tenant's
    real JWKS endpoint.
    """
    if not token:
        raise EntraAuthError("Missing bearer token.")

    jwks_client = jwks_client or _get_jwks_client(_jwks_uri())

    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=_client_id(),
            issuer=_issuer(),
        )
    except jwt.PyJWTError as e:
        raise EntraAuthError(f"Entra ID token validation failed: {e}") from e

    role = _map_role(claims)

    department_claim = os.environ.get("ENTRA_DEPARTMENT_CLAIM", "department")
    department = claims.get(department_claim) if role == "hr_manager" else None

    return User(
        user_id=claims.get("oid", claims.get("sub", "unknown")),
        display_name=claims.get("name", claims.get("preferred_username", "Entra ID User")),
        role=role,
        department=department,
        auth_method="entra_id",
    )
