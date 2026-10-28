"""Conversation-id validation at the storage and API boundaries."""

import uuid

import pytest
from fastapi.testclient import TestClient

from backend import storage
from backend.main import app


@pytest.mark.parametrize("bad_id", [
    "",
    ".",
    "..",
    "../secret",
    "..\\secret",
    "a/b",
    "a\\b",
    "%2e%2e%2fsecret",
    "conversation.json",
    "a b",
    None,
    123,
])
def test_storage_rejects_unsafe_conversation_ids(bad_id):
    with pytest.raises(ValueError, match="Invalid conversation id"):
        storage.validate_conversation_id(bad_id)


def test_storage_accepts_valid_ids():
    for conversation_id in ("safe-id_1", "A1", str(uuid.uuid4())):
        storage.create_conversation(conversation_id)


def test_api_rejects_malformed_ids_with_400(client):
    for bad_id in (
        "not-a-uuid",
        "..%5Csecret",
        "%2e%2e%2fsecret",
        "%2e%2e%5csecret",
        "%252e%252e%252fsecret",
    ):
        response = client.get(f"/api/conversations/{bad_id}")
        assert response.status_code == 400, (bad_id, response.status_code)


def test_api_never_serves_traversal_paths(client):
    for bad_id in ("..", "%2e%2e", "%2e%2e%2fsecret"):
        response = client.get(f"/api/conversations/{bad_id}")
        assert response.status_code in (400, 404), (bad_id, response.status_code)


def test_api_accepts_existing_uuid(client):
    conversation_id = str(uuid.uuid4())
    user = client._auth_user["user_id"]
    storage.create_conversation(conversation_id, user_id=user)

    response = client.get(f"/api/conversations/{conversation_id}")
    assert response.status_code == 200
    assert response.json()["id"] == conversation_id


def test_api_rejects_malformed_ids_when_unauthenticated():
    """Malformed ids still get a 400 even without a token."""
    bare = TestClient(app)
    response = bare.get("/api/conversations/..%5Csecret")
    assert response.status_code == 400
