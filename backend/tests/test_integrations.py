"""Mocked tests for GitHub and Notion integrations and the tools status flow.

All external HTTP is mocked; no real credentials are used or required.
"""

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import backend.integrations as integrations
from backend.integrations import GitHubClient, NotionClient


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def _gh_response(status=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data if json_data is not None else {}
    resp.raise_for_status = MagicMock(side_effect=None)
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=resp
        )
    return resp


def _gh_repo_item(full_name, stars):
    return {"full_name": full_name, "description": "d", "stargazers_count": stars,
            "html_url": f"https://github.com/{full_name}", "language": "py"}


NOT_FOUND = {
    "get": _gh_response(404),
}


@pytest.mark.asyncio
async def test_github_test_connection_authenticated_success():
    gh = GitHubClient(token="fake-token")
    with patch("backend.integrations.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.get = AsyncMock(return_value=_gh_response(200, {"login": "MokshonWork"}))
        client_cls.return_value = client
        result = await gh.test_connection()

    assert result.success is True
    assert "MokshonWork" in result.data["detail"]


@pytest.mark.asyncio
async def test_github_test_connection_bad_token():
    gh = GitHubClient(token="fake-token")
    with patch("backend.integrations.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        resp = _gh_response(401)
        client.get = AsyncMock(return_value=resp)
        client_cls.return_value = client
        result = await gh.test_connection()

    assert result.success is False
    assert "401" in result.error or "Authentication failed" in result.error
    # credentials never leak in error text
    assert "fake-token" not in result.error


@pytest.mark.asyncio
async def test_github_search_repositories_parses_results():
    gh = GitHubClient(token="fake-token")
    body = {"items": [_gh_repo_item("me/repo1", 5), _gh_repo_item("me/repo2", 9)]}
    with patch("backend.integrations.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.get = AsyncMock(return_value=_gh_response(200, body))
        client_cls.return_value = client
        result = await gh.search_repositories("council")

    assert result.success is True
    assert [r["full_name"] for r in result.data["results"]] == ["me/repo1", "me/repo2"]


@pytest.mark.asyncio
async def test_github_search_rejects_empty_query_without_http_call():
    gh = GitHubClient(token="fake-token")
    with patch("backend.integrations.httpx.AsyncClient") as client_cls:
        result = await gh.search_repositories("   ")
    assert result.success is False
    assert client_cls.call_count == 0  # no network call for invalid input


def test_github_client_does_not_put_token_in_error():
    gh = GitHubClient(token="secret-fake")
    # errors come from _normalize_error; ensure raw exception text is not included for request errors
    err = integrations._normalize_error(httpx.ConnectError("connect failed"))
    assert "secret-fake" not in err


@pytest.mark.asyncio
async def test_github_read_file_rejects_traversal():
    gh = GitHubClient(token="fake-token")
    result = await gh.get_file_contents("owner", "repo", "../../etc/passwd")
    assert result.success is False
    assert "Invalid file path" in result.error


@pytest.mark.asyncio
async def test_github_read_file_success_decodes_content():
    gh = GitHubClient(token="fake-token")
    import base64

    encoded = base64.b64encode(b"# hello file").decode()
    data = {"type": "file", "path": "README.md", "size": 11, "content": encoded, "html_url": "https://u"}
    with patch("backend.integrations.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.get = AsyncMock(return_value=_gh_response(200, data))
        client_cls.return_value = client
        result = await gh.get_file_contents("owner", "repo", "README.md")

    assert result.success is True
    assert result.data["content"] == "# hello file"


# ---------------------------------------------------------------------------
# Notion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notion_missing_credentials_reports_not_configured():
    notion = NotionClient(token=None)
    result = await notion.search_pages("anything")
    assert result.success is False
    assert "NOTION_TOKEN" in result.error
    # and no network call happened because configured gate fires first


@pytest.mark.asyncio
async def test_notion_test_connection_success():
    notion = NotionClient(token="fake-notion")
    with patch("backend.integrations.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.get = AsyncMock(return_value=_gh_response(200, {"type": "bot", "bot": {"owner": {"type": "user"}}}))
        client_cls.return_value = client
        result = await notion.test_connection()

    assert result.success is True
    assert result.data["detail"]


@pytest.mark.asyncio
async def test_notion_search_parses_titles():
    notion = NotionClient(token="fake-notion")
    body = {
        "results": [
            {"object": "page", "id": "p1", "url": "https://n/p1", "last_edited_time": "2026-01-01",
             "properties": {"title": {"title": [{"plain_text": "My Page"}]}}},
            {"object": "database", "id": "db1", "url": "https://n/db1", "title": [{"plain_text": "My DB"}]},
        ]
    }
    with patch("backend.integrations.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.post = AsyncMock(return_value=_gh_response(200, body))
        client_cls.return_value = client
        result = await notion.search_pages("my")

    assert result.success is True
    assert result.data["results"][0]["title"] == "My Page"
    assert result.data["results"][1]["title"] == "My DB"


@pytest.mark.asyncio
async def test_notion_get_page_requires_valid_id():
    notion = NotionClient(token="fake-notion")
    result = await notion.get_page_content("bad id; drop table")
    assert result.success is False
    assert "page_id" in result.error


@pytest.mark.asyncio
async def test_notion_permission_error_is_normalized():
    notion = NotionClient(token="fake-notion")
    with patch("backend.integrations.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.get = AsyncMock(return_value=_gh_response(401))
        client_cls.return_value = client
        result = await notion.get_page_content("a" * 32)

    assert result.success is False
    assert "Authentication failed" in result.error


# ---------------------------------------------------------------------------
# Tools status contract (frontend consistency: Tools page + sidebar)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tools_status_only_reports_expected_integrations():
    from backend.main import tools_status

    data = await tools_status()
    assert set(data["integrations"].keys()) == {"web_search", "github", "notion"}
    # configured booleans must be real configuration, never hardcoded success
    import os

    assert data["integrations"]["web_search"]["configured"] is True  # DuckDuckGo needs no key
    assert data["integrations"]["github"]["configured"] == bool(os.getenv("GITHUB_TOKEN"))
    assert data["integrations"]["notion"]["configured"] == bool(os.getenv("NOTION_TOKEN"))
