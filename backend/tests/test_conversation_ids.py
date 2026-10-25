"""Conversation-id validation at the storage and API boundaries."""

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from backend import storage
from backend.main import app


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    return tmp_path


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
def test_storage_rejects_unsafe_conversation_ids(data_dir, bad_id):
    with pytest.raises(ValueError, match="Invalid conversation id"):
        storage.get_conversation_path(bad_id)


def test_storage_accepts_valid_ids_and_keeps_paths_inside_data_dir(data_dir):
    for conversation_id in ("safe-id_1", "A1", str(uuid.uuid4())):
        path = storage.get_conversation_path(conversation_id)
        assert os.path.dirname(path) == str(data_dir.resolve())


def test_storage_rejects_symlink_escape(data_dir):
    outside = data_dir.parent / "outside.json"
    outside.write_text("{}")
    try:
        os.symlink(outside, data_dir / "linked.json")
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="Invalid conversation id"):
        storage.get_conversation_path("linked")


def test_api_rejects_malformed_ids_with_400(data_dir):
    client = TestClient(app)
    for bad_id in (
        "not-a-uuid",
        "..%5Csecret",
        "%2e%2e%2fsecret",
        "%2e%2e%5csecret",
        "%252e%252e%252fsecret",
    ):
        response = client.get(f"/api/conversations/{bad_id}")
        assert response.status_code == 400, (bad_id, response.status_code)


def test_api_never_serves_traversal_paths(data_dir):
    client = TestClient(app)
    for bad_id in ("..", "%2e%2e", "%2e%2e%2fsecret"):
        response = client.get(f"/api/conversations/{bad_id}")
        assert response.status_code in (400, 404), (bad_id, response.status_code)


def test_api_accepts_existing_uuid(data_dir):
    conversation_id = str(uuid.uuid4())
    storage.create_conversation(conversation_id)
    client = TestClient(app)

    response = client.get(f"/api/conversations/{conversation_id}")
    assert response.status_code == 200
    assert response.json()["id"] == conversation_id
