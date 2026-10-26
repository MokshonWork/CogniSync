"""CORS middleware origin allow-list tests.

Verifies that the production Vercel frontend origin is granted CORS access
while an unrelated origin is not.
"""

from fastapi.testclient import TestClient

from backend.main import app

VERCEL_ORIGIN = "https://cognisync-v1.vercel.app"
UNRELATED_ORIGIN = "https://evil.example.com"


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/api/conversations/00000000-0000-0000-0000-000000000000",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
        },
    )


def test_cors_allows_vercel_origin():
    client = TestClient(app)
    response = _preflight(client, VERCEL_ORIGIN)
    assert response.headers["access-control-allow-origin"] == VERCEL_ORIGIN


def test_cors_rejects_unrelated_origin():
    client = TestClient(app)
    response = _preflight(client, UNRELATED_ORIGIN)
    assert response.headers.get("access-control-allow-origin") is None
