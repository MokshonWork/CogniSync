"""Cross-user isolation tests for CogniSync.

Verifies that users cannot access, modify, or delete another user's
conversations. The API registers/authenticates each user via /auth/register
(see conftest), and ownership filters are checked at both API and storage
level.
"""

import pytest
from fastapi.testclient import TestClient

from backend import storage
from backend.main import app


def _auth_user(client):
    return client._auth_user


def test_user_can_access_own_conversations(client):
    """The authenticated user can access their own conversations."""
    user = _auth_user(client)
    conv_resp = client.post("/api/conversations", json={"title": "user1-conv"})
    assert conv_resp.status_code == 200
    conv_id = conv_resp.json()["id"]

    listings = storage.list_conversations(user_id=user["user_id"], include_empty=True)
    assert conv_id in [c["id"] for c in listings]

    conv = storage.get_conversation_for_user(conv_id, user["user_id"])
    assert conv is not None
    assert conv["user_id"] == user["user_id"]


def test_other_user_cannot_list_or_read(client, second_client):
    """A second user's list/read must not include the first user's data."""
    conv_resp = client.post("/api/conversations", json={"title": "user1-conv"})
    conv_id = conv_resp.json()["id"]

    listings = storage.list_conversations(user_id=second_client._auth_user["user_id"])
    assert conv_id not in [c["id"] for c in listings]

    other = second_client.get(f"/api/conversations/{conv_id}")
    assert other.status_code == 404
    assert storage.get_conversation_for_user(conv_id, second_client._auth_user["user_id"]) is None


def test_other_user_cannot_delete(client, second_client):
    """Delete requests from another user are rejected and data survives."""
    conv_id = client.post("/api/conversations", json={"title": "to-keep"}).json()["id"]

    delete_resp = second_client.delete(f"/api/conversations/{conv_id}")
    assert delete_resp.status_code == 404

    get_resp = client.get(f"/api/conversations/{conv_id}")
    assert get_resp.status_code == 200


def test_unauthenticated_requests_are_rejected(client):
    """Every conversation route requires a bearer token."""
    bare = TestClient(app)
    conv_id = client.post("/api/conversations", json={}).json()["id"]

    for method, url, body in (
        ("get", "/api/conversations", None),
        ("get", f"/api/conversations/{conv_id}", None),
        ("delete", f"/api/conversations/{conv_id}", None),
        ("post", f"/api/conversations/{conv_id}/message", {"content": "x"}),
        ("post", f"/api/conversations/{conv_id}/message/events", {"content": "x"}),
        ("post", "/api/query", {"question": "x"}),
        ("get", "/auth/me", None),
    ):
        resp = getattr(bare, method)(url, **({"json": body} if body is not None else {}))
        assert resp.status_code == 401, (method, url, resp.status_code)


def test_invalid_tokens_are_rejected(client):
    """Malformed/expired tokens fail validation with 401."""
    from backend import auth

    bare = TestClient(app)
    for token in ("garbage-token", auth.create_access_token("x", "x").replace("a", "b")):
        resp = bare.get(
            "/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 401
