"""Direct official-API integrations for GitHub and Notion.

Connection type: standard REST calls to the official APIs — NOT MCP protocol
sessions. Credentials come exclusively from server-side environment
variables (GITHUB_TOKEN, NOTION_TOKEN) and are never returned to the
frontend or logged. Base URLs are fixed constants, which removes any SSRF
surface from user-supplied input.

All operations are read-only. Write operations are deliberately not
implemented in this phase.
"""

import httpx
from typing import Any, Dict, List, Optional

from .tools import ToolResult
from .config import (
    GITHUB_API_URL,
    GITHUB_TOKEN,
    GITHUB_TIMEOUT,
    NOTION_API_URL,
    NOTION_API_VERSION,
    NOTION_TOKEN,
    NOTION_TIMEOUT,
)


def _normalize_error(exc: Exception) -> str:
    """Coarse, user-safe error text. Never includes tokens or raw bodies."""
    if isinstance(exc, httpx.TimeoutException):
        return "Request timed out. The integration endpoint did not respond in time."
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (401, 403):
        return f"Authentication failed (HTTP {status}). Check the configured token and its scopes/permissions."
    if status == 429:
        return "Rate limited by the provider. Wait before retrying."
    if status == 404:
        return "Not found (HTTP 404). Check the requested resource or permissions."
    if isinstance(exc, httpx.RequestError):
        return "Network error while contacting the provider."
    return "Unexpected error while contacting the provider."


class GitHubClient:
    """GitHub REST API client (read-only).

    Works unauthenticated for public repositories at reduced rate limits;
    set GITHUB_TOKEN on the server for higher limits and private repos the
    token can access.
    """

    name = "github"

    def __init__(self, token: Optional[str] = GITHUB_TOKEN, api_url: str = GITHUB_API_URL, timeout: float = GITHUB_TIMEOUT):
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def test_connection(self) -> ToolResult:
        """Health check: authenticated user, or rate-limit limits when unauthenticated."""
        import time

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if self.token:
                    resp = await client.get(f"{self.api_url}/user", headers=self._headers())
                else:
                    resp = await client.get(f"{self.api_url}/rate_limit", headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
            # rate_limit returns nested resources; extract a top-level login when present
            login = data.get("login")
            if login is None and isinstance(data.get("resources"), dict):
                login = (data["resources"].get("core", {}) or {}).get("login")  # not provided; keep None
            detail = f"Authenticated as {login}" if login else "Unauthenticated public access verified"
            return ToolResult(tool_name="github.test", success=True, data={"detail": detail},
                              duration_ms=int((time.monotonic() - start) * 1000))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool_name="github.test", success=False, error=_normalize_error(exc))

    async def search_repositories(self, query: str, per_page: int = 5) -> ToolResult:
        """Search repositories by query string."""
        query = (query or "").strip()
        if not query:
            return ToolResult(tool_name="github.search", success=False, error="Query must not be empty.")
        per_page = max(1, min(10, int(per_page)))
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.api_url}/search/repositories",
                    headers=self._headers(),
                    params={"q": query, "per_page": per_page},
                )
                resp.raise_for_status()
                items = resp.json().get("items", [])
            results = [
                {
                    "full_name": it.get("full_name"),
                    "description": it.get("description"),
                    "stars": it.get("stargazers_count", 0),
                    "url": it.get("html_url"),
                    "language": it.get("language"),
                }
                for it in items
            ]
            return ToolResult(tool_name="github.search", success=True, data={"query": query, "results": results})
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool_name="github.search", success=False, error=_normalize_error(exc))

    async def get_file_contents(self, owner: str, repo: str, path: str, ref: str = "") -> ToolResult:
        """Read one file from a repository."""
        owner = (owner or "").strip().rstrip("/")
        repo = (repo or "").strip().rstrip("/")
        path = (path or "").strip().lstrip("/")
        if not owner or not repo or not path:
            return ToolResult(tool_name="github.read", success=False, error="owner, repo, and path are required.")
        # Basic validation: refuse traversal-like paths.
        if ".." in path or any(p in {".", ""} for p in path.split("/")):
            return ToolResult(tool_name="github.read", success=False, error="Invalid file path.")
        url = f"{self.api_url}/repos/{owner}/{repo}/contents/{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, headers=self._headers(), params={"ref": ref} if ref else None)
                resp.raise_for_status()
                data = resp.json()
            if not isinstance(data, dict) or data.get("type") != "file":
                return ToolResult(tool_name="github.read", success=False, error="Requested path is not a file.")
            import base64

            content = base64.b64decode(data.get("content") or "").decode("utf-8", errors="replace")
            return ToolResult(
                tool_name="github.read",
                success=True,
                data={
                    "path": data.get("path"),
                    "size": data.get("size"),
                    "encoding": "utf-8",
                    "content": content[:20000],
                    "url": data.get("html_url"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool_name="github.read", success=False, error=_normalize_error(exc))

    async def list_issues(self, owner: str, repo: str, state: str = "open", per_page: int = 10) -> ToolResult:
        """List issues (and PRs) for a repository."""
        owner = (owner or "").strip().rstrip("/")
        repo = (repo or "").strip().rstrip("/")
        if not owner or not repo:
            return ToolResult(tool_name="github.issues", success=False, error="owner and repo are required.")
        state = state if state in ("open", "closed", "all") else "open"
        per_page = max(1, min(20, int(per_page)))
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.api_url}/repos/{owner}/{repo}/issues",
                    headers=self._headers(),
                    params={"state": state, "per_page": per_page},
                )
                resp.raise_for_status()
                items = resp.json()
            results = [
                {
                    "number": it.get("number"),
                    "title": it.get("title"),
                    "state": it.get("state"),
                    "is_pull_request": "pull_request" in it,
                    "url": it.get("html_url"),
                }
                for it in items
            ]
            return ToolResult(tool_name="github.issues", success=True, data={"owner": owner, "repo": repo, "state": state, "results": results})
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool_name="github.issues", success=False, error=_normalize_error(exc))


class NotionClient:
    """Notion official API client (read-only search + page retrieval).

    Requires a server-side NOTION_TOKEN (internal integration secret) and
    pages shared with the integration. Pages not shared are invisible —
    that is Notion's permission model.
    """

    name = "notion"

    def __init__(self, token: Optional[str] = NOTION_TOKEN, api_url: str = NOTION_API_URL, timeout: float = NOTION_TIMEOUT):
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}" if self.token else "",
            "Notion-Version": NOTION_API_VERSION,
        }

    def _unconfigured(self, tool: str) -> ToolResult:
        return ToolResult(
            tool_name=tool,
            success=False,
            error="Not configured: set NOTION_TOKEN on the server and share pages with the integration.",
        )

    async def test_connection(self) -> ToolResult:
        """Health check: GET /users/me as the integration identity."""
        import time

        start = time.monotonic()
        if not self.configured:
            return self._unconfigured("notion.test")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(f"{self.api_url}/users/me", headers=self._headers())
                resp.raise_for_status()
                me = resp.json()
            label = (me.get("bot") or {}).get("owner", {}).get("type") or me.get("type") or "integration"
            return ToolResult(
                tool_name="notion.test",
                success=True,
                data={"detail": f"Integration identity verified ({label})"},
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool_name="notion.test", success=False, error=_normalize_error(exc))

    async def search_pages(self, query: str, limit: int = 5) -> ToolResult:
        """Search pages/databases shared with the integration."""
        if not self.configured:
            return self._unconfigured("notion.search")
        limit = max(1, min(10, int(limit or 5)))
        body: Dict[str, Any] = {"page_size": limit}
        q = (query or "").strip()
        if q:
            body["query"] = q
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.api_url}/search", headers=self._headers(), json=body)
                resp.raise_for_status()
                data = resp.json()
            results = []
            for item in (data.get("results") or [])[:limit]:
                obj = item.get("object")
                title = obj
                props = item.get("properties") or {}
                if obj == "page":
                    title_parts = (props.get("title") or {}).get("title")
                    if not title_parts and "Name" in props:
                        title_parts = props["Name"].get("title")
                    title = "".join(p.get("plain_text", "") for p in (title_parts or [])) or "(untitled page)"
                elif obj == "database":
                    titles = item.get("title") or []
                    title = "".join(p.get("plain_text", "") for p in titles) or "(untitled database)"
                results.append({
                    "title": title,
                    "type": obj,
                    "id": item.get("id"),
                    "url": item.get("url"),
                    "last_edited": item.get("last_edited_time"),
                })
            return ToolResult(tool_name="notion.search", success=True, data={"query": q, "results": results})
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool_name="notion.search", success=False, error=_normalize_error(exc))

    async def get_page_content(self, page_id: str) -> ToolResult:
        """Retrieve the text blocks of a page."""
        if not self.configured:
            return self._unconfigured("notion.page")
        import re as _re

        page_id = (page_id or "").strip()
        if not _re.fullmatch(r"[a-f0-9-]{32,36}", page_id, flags=_re.IGNORECASE):
            return ToolResult(tool_name="notion.page", success=False, error="page_id must be a valid Notion page id (uuid).")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.api_url}/blocks/{page_id}/children",
                    headers=self._headers(),
                    params={"page_size": 100},
                )
                resp.raise_for_status()
                data = resp.json()
            blocks: List[str] = []
            for block in data.get("results", []):
                btype = block.get("type")
                text_parts = ((block.get(btype) or {}).get("rich_text")) or []
                text = "".join(p.get("plain_text", "") for p in text_parts)
                if text:
                    blocks.append(text)
            return ToolResult(tool_name="notion.page", success=True, data={"page_id": page_id, "text": "\n".join(blocks)[:20000]})
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool_name="notion.page", success=False, error=_normalize_error(exc))
