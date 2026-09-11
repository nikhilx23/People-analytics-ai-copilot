"""
Tests for the real Entra ID token-validation code path
(app/governance/entra_auth.py).

There's no real Azure tenant available in this environment, so these tests
exercise the actual validation logic (signature check, audience/issuer/expiry
checks, role-claim mapping) against a self-signed token and an injected fake
JWKS client — the same code path a real Entra ID token goes through, minus
the network call to Microsoft's actual JWKS endpoint.
"""

import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.governance.entra_auth import EntraAuthError, authenticate_entra_token
from app.governance.permissions import authenticate, entra_id_configured

TENANT_ID = "test-tenant-id"
CLIENT_ID = "test-client-id"
ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"


@pytest.fixture(autouse=True)
def entra_env(monkeypatch):
    monkeypatch.setenv("ENTRA_TENANT_ID", TENANT_ID)
    monkeypatch.setenv("ENTRA_CLIENT_ID", CLIENT_ID)
    yield


@pytest.fixture
def rsa_keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


class FakeJWKClient:
    """Stands in for jwt.PyJWKClient — returns a fixed public key instead of
    fetching one from a real JWKS endpoint."""
    def __init__(self, public_key):
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey(self._public_key)


def make_token(private_key, roles, extra_claims=None, aud=CLIENT_ID, iss=ISSUER, exp_delta=3600):
    now = int(time.time())
    claims = {
        "aud": aud, "iss": iss, "iat": now, "exp": now + exp_delta,
        "oid": "11111111-2222-3333-4444-555555555555",
        "name": "Test User", "roles": roles,
    }
    if extra_claims:
        claims.update(extra_claims)
    return pyjwt.encode(claims, private_key, algorithm="RS256")


def test_valid_analyst_token_is_accepted(rsa_keypair):
    token = make_token(rsa_keypair, roles=["HR.Analyst"])
    user = authenticate_entra_token(token, jwks_client=FakeJWKClient(rsa_keypair.public_key()))
    assert user.role == "hr_analyst"
    assert user.auth_method == "entra_id"


def test_manager_token_carries_department_claim(rsa_keypair):
    token = make_token(rsa_keypair, roles=["HR.Manager"], extra_claims={"department": "Sales"})
    user = authenticate_entra_token(token, jwks_client=FakeJWKClient(rsa_keypair.public_key()))
    assert user.role == "hr_manager"
    assert user.department == "Sales"


def test_admin_role_maps_correctly(rsa_keypair):
    token = make_token(rsa_keypair, roles=["HR.Admin"])
    user = authenticate_entra_token(token, jwks_client=FakeJWKClient(rsa_keypair.public_key()))
    assert user.role == "hr_admin"


def test_token_without_recognized_role_is_rejected(rsa_keypair):
    token = make_token(rsa_keypair, roles=["SomeUnrelatedAppRole"])
    with pytest.raises(EntraAuthError):
        authenticate_entra_token(token, jwks_client=FakeJWKClient(rsa_keypair.public_key()))


def test_expired_token_is_rejected(rsa_keypair):
    token = make_token(rsa_keypair, roles=["HR.Analyst"], exp_delta=-10)
    with pytest.raises(EntraAuthError):
        authenticate_entra_token(token, jwks_client=FakeJWKClient(rsa_keypair.public_key()))


def test_wrong_audience_is_rejected(rsa_keypair):
    token = make_token(rsa_keypair, roles=["HR.Analyst"], aud="some-other-client-id")
    with pytest.raises(EntraAuthError):
        authenticate_entra_token(token, jwks_client=FakeJWKClient(rsa_keypair.public_key()))


def test_wrong_issuer_is_rejected(rsa_keypair):
    token = make_token(rsa_keypair, roles=["HR.Analyst"], iss="https://login.microsoftonline.com/some-other-tenant/v2.0")
    with pytest.raises(EntraAuthError):
        authenticate_entra_token(token, jwks_client=FakeJWKClient(rsa_keypair.public_key()))


def test_token_signed_by_untrusted_key_is_rejected(rsa_keypair):
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged_token = make_token(attacker_key, roles=["HR.Admin"])  # signed by attacker's key
    # Verifier only trusts rsa_keypair's public key (as if it came from the
    # tenant's real JWKS) - the forged signature must fail verification.
    with pytest.raises(EntraAuthError):
        authenticate_entra_token(forged_token, jwks_client=FakeJWKClient(rsa_keypair.public_key()))


def test_missing_token_is_rejected():
    with pytest.raises(EntraAuthError):
        authenticate_entra_token("")


def test_permissions_layer_routes_to_entra_when_configured():
    assert entra_id_configured() is True  # env vars set by the autouse fixture


def test_permissions_layer_falls_back_to_demo_when_entra_unconfigured(monkeypatch):
    monkeypatch.delenv("ENTRA_TENANT_ID", raising=False)
    monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)
    assert entra_id_configured() is False
    # Falls back to the demo stub map rather than trying (and failing) real validation.
    user = authenticate("demo-analyst-token")
    assert user.auth_method == "demo_stub"
