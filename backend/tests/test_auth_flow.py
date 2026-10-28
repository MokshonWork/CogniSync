"""Authentication flow tests: registration, login, token validation,
revocation, protected endpoints.

Uses shared fixtures from backend/tests/conftest.py.
"""

import time

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from backend import auth
from backend.config import JWT_ALGORITHM, JWT_AUDIENCE, JWT_ISSUER, JWT_SECRET
from backend.main import app


def test_register_hashes_password_argon2id(client):
    user = auth.get_user_by_username("testuser")
    assert user is not None
    assert user["hashed_password"].startswith("$argon2id$")
    # Plaintext never stored anywhere accessible via auth/storage.


def test_register_rejects_weak_credentials(client):
    for body in (
        {"username": "", "password": "x"},
        {"username": "ab", "password": "long-enough"},
        {"username": "validuser", "password": "x"},
    ):
        resp = TestClient(app).post("/auth/register", json=body)
        assert resp.status_code == 400, body


def test_register_duplicate_username_conflict():
    c1 = TestClient(app)
    r1 = c1.post("/auth/register", json={"username": "dupuser", "password": "pw123456"})
    assert r1.status_code == 200
    r2 = c1.post("/auth/register", json={"username": "dupuser", "password": "pw123456"})
    assert r2.status_code == 409


def test_login_success_and_failure():
    c = TestClient(app)
    c.post("/auth/register", json={"username": "loginuser", "password": "pw123456"})

    ok = c.post("/auth/login", json={"username": "loginuser", "password": "pw123456"})
    assert ok.status_code == 200
    token = ok.json()["access_token"]
    assert auth.decode_access_token(token)["sub"] == ok.json()["user_id"]

    bad = c.post("/auth/login", json={"username": "loginuser", "password": "wrong-pass"})
    assert bad.status_code == 401
    nogood = c.post("/auth/login", json={"username": "ghost", "password": "pw123456"})
    assert nogood.status_code == 401


def test_token_has_required_claims(client):
    token = client._auth_user["token"]
    payload = auth.decode_access_token(token)
    for claim in ("sub", "username", "jti", "iat", "exp", "iss", "aud", "type"):
        assert claim in payload
    assert payload["type"] == "access"


def test_expired_token_rejected(client):
    # Forge a token with a past expiry (correct secret/issuer/audience).
    payload = {
        "sub": client._auth_user["user_id"],
        "username": client._auth_user["username"],
        "jti": "expired-jti",
        "iat": int(time.time()) - 7200,
        "exp": int(time.time()) - 3600,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "type": "access",
    }
    token = pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    resp = TestClient(app).get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_garbage_and_unsupported_algorithm_tokens_rejected():
    bare = TestClient(app)
    assert bare.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    # `none` algorithm tokens must not validate.
    payload = {"sub": "x", "username": "x", "jti": "j", "exp": int(time.time()) + 600,
               "iss": JWT_ISSUER, "aud": JWT_AUDIENCE, "type": "access"}
    none_token = pyjwt.encode(payload, key=None, algorithm="none")
    resp = bare.get("/auth/me", headers={"Authorization": f"Bearer {none_token}"})
    assert resp.status_code == 401


def test_token_revocation_blocks_access(client):
    token = client._auth_user["token"]
    header = {"Authorization": f"Bearer {token}"}
    assert TestClient(app).get("/auth/me", headers=header).status_code == 200

    payload = auth.decode_access_token(token)
    client.post("/auth/logout")
    assert TestClient(app).get("/auth/me", headers=header).status_code == 401


def test_logout_revokes_and_relogin_works(client):
    user = client._auth_user
    # logout with the original token revokes it
    assert client.post("/auth/logout").status_code == 204
    assert (TestClient(app).get("/auth/me",
            headers={"Authorization": f"Bearer {user['token']}"}).status_code == 401)

    # a fresh token issued after login is usable
    fresh = TestClient(app).post(
        "/auth/login", json={"username": user["username"], "password": "testpass123"}
    )
    assert fresh.status_code == 200
    ok = TestClient(app).get(
        "/auth/me", headers={"Authorization": f"Bearer {fresh.json()['access_token']}"}
    )
    assert ok.status_code == 200


def test_revocation_is_jti_scoped(client):
    """Only the revoked token dies, not the user's other tokens."""
    user = client._auth_user
    other = client.post("/auth/login", json={"username": user["username"], "password": "testpass123"}).json()

    client.post("/auth/logout")
    kept = TestClient(app).get(
        "/auth/me", headers={"Authorization": f"Bearer {other['access_token']}"}
    )
    assert kept.status_code == 200


def test_malformed_authorization_header(client):
    bearer = client._auth_user["token"]
    best_client = TestClient(app)
    assert best_client.get("/auth/me").status_code == 401
    assert best_client.get("/auth/me", headers={"Authorization": bearer}).status_code == 401
    assert best_client.get("/auth/me", headers={"Authorization": f"Token {bearer}"}).status_code == 401


def test_deleted_user_token_is_rejected(client):
    """Deleting the user account invalidates outstanding tokens immediately."""
    user = client._auth_user
    from backend.models import User
    from backend.database import get_db, SessionLocal

    db = SessionLocal()
    try:
        db.query(User).filter(User.id == user["user_id"]).delete()
        db.commit()
    finally:
        db.close()

    header = {"Authorization": f"Bearer {user['token']}"}
    probe = TestClient(app)
    assert probe.get("/auth/me", headers=header).status_code == 401
    assert probe.get("/api/conversations", headers=header).status_code == 401


def test_revocation_store_outage_is_fail_closed(client, monkeypatch):
    """A revocation-store read failure must not validate tokens (503, not 200)."""
    def boom(jti):
        raise auth.TokenStoreUnavailable("database down")

    monkeypatch.setattr(auth, "is_token_revoked", boom)
    probe = TestClient(app)
    resp = probe.get(
        "/auth/me", headers={"Authorization": f"Bearer {client._auth_user['token']}"}
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Token validation is temporarily unavailable"
