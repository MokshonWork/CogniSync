"""Minimal tool interface for future MCP integration.

This module defines a small, typed abstraction for invoking named tools and
receiving normalized results. It is intentionally lightweight: no agent
framework, no automatic tool selection, no general-purpose autonomy.

Design goals:
- A single `ToolResult` structure that can wrap search, file, or any future
  MCP tool output.
- A structural `ToolProtocol` so new tools (including MCP-backed ones) can be
  added without changing callers.
- Tool selection is explicit and allowlisted — the model never gets unrestricted
  tool execution. Tools are invoked by the server-side orchestration layer,
  never directly by the LLM in this phase.

This keeps the door open for MCP later without requiring MCP now.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@dataclass
class ToolResult:
    """Normalized result of invoking a single tool.

    Attributes:
        tool_name: The name of the tool that was invoked (e.g. ``"duckduckgo_search"``).
        success: Whether the tool invocation succeeded.
        data: Structured result data when successful. Contents depend on the tool.
        error: Concise error description when the invocation failed. Never
            contains raw upstream error bodies or credentials.
        sources: Optional list of source references produced by the tool.
        duration_ms: Wall-clock execution time in milliseconds, when available.
    """

    tool_name: str
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    sources: Optional[List[Dict[str, Any]]] = None
    duration_ms: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain-dict copy suitable for JSON serialization."""
        return asdict(self)


@runtime_checkable
class ToolProtocol(Protocol):
    """Structural interface for an invokable tool.

    Any future tool (MCP-backed or otherwise) that exposes an async
    ``invoke`` method matching this signature qualifies.
    """

    name: str

    async def invoke(self, input_data: Dict[str, Any]) -> ToolResult:
        """Invoke the tool with validated input and return a normalized result."""
        ...


def validate_tool_name(tool_name: str, allowed: List[str]) -> str:
    """Validate that a tool name is on the allowlist.

    Raises ValueError if the tool is not allowed. Tool execution is restricted
    to an explicitly configured allowlist so no tool can be invoked arbitrarily.
    """
    if tool_name not in allowed:
        raise ValueError(f"Tool '{tool_name}' is not in the allowed tool list")
    return tool_name
