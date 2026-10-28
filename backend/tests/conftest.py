"""Shared backend test fixtures.

Uses the SQLite fallback engine from backend.database. Per-test isolation:
every table is emptied after each test (the dataset is tiny, so cheap).
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import app


def _registered_client(username: str, password: str) -> TestClient:
    """TestClient registered AND authenticated as a fresh user.

    Registration returns {user_id, username, access_token}; the bearer
    header is attached for the test. Credentials are exposed via
    ``_auth_user`` for ownership-aware storage-level tests.
    """
    _client = TestClient(app)
    resp = _client.post("/auth/register", json={"username": username, "password": password})
    if resp.status_code != 200:
        pytest.fail(f"Registration failed: {resp.status_code} - {resp.text}")
    _client._auth_user = {
        "user_id": resp.json()["user_id"],
        "username": resp.json()["username"],
        "token": resp.json()["access_token"],
    }
    _client.headers = {"Authorization": f"Bearer {_client._auth_user['token']}"}
    return _client


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Create the schema once per session (SQLite fallback)."""
    from backend.database import Base, engine

    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def clean_database():
    """Delete all rows between tests."""
    from backend.database import engine
    from backend import models

    yield

    with engine.begin() as conn:
        for table in (
            models.RevokedToken.__table__,
            models.Message.__table__,
            models.Conversation.__table__,
            models.User.__table__,
        ):
            conn.execute(table.delete())


@pytest.fixture()
def client(clean_database):
    """TestClient authenticated as a fresh registered user."""
    return _registered_client("testuser", "testpass123")


@pytest.fixture()
def second_client(clean_database):
    """A second, independently registered user for cross-user tests."""
    return _registered_client("testuser2", "testpass123")
