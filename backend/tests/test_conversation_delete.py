"""Conversation deletion ownership tests (uses shared fixtures in conftest)."""

import uuid


def test_user_can_delete_own_conversation(client):
    """A user can delete their own conversation."""
    conv_resp = client.post("/api/conversations", json={"title": "test-conv"})
    assert conv_resp.status_code == 200
    conv_id = conv_resp.json()["id"]

    delete_resp = client.delete(f"/api/conversations/{conv_id}")
    assert delete_resp.status_code == 204

    get_resp = client.get(f"/api/conversations/{conv_id}")
    assert get_resp.status_code == 404


def test_deleting_nonexistent_conversation_404s(client):
    """Deleting a nonexistent conversation returns 404."""
    fake_id = str(uuid.uuid4())
    delete_resp = client.delete(f"/api/conversations/{fake_id}")
    assert delete_resp.status_code == 404


def test_unauthenticated_deletion_rejected():
    """Unauthenticated deletion request is rejected."""
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)  # No auth header
    fake_id = str(uuid.uuid4())
    delete_resp = client.delete(f"/api/conversations/{fake_id}")
    assert delete_resp.status_code == 401
