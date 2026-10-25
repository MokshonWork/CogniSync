"""JSON-based storage for conversations.

Writes are atomic (temp file + os.replace) so a crash mid-write can never
corrupt a saved conversation, and a process-wide lock serializes the
read-modify-write cycles that append messages.

Empty conversations are never listed: a conversation becomes visible in
history only after its first user message is persisted.
"""

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from pathlib import Path
from .config import DATA_DIR

_LOCK = threading.RLock()

DEFAULT_TITLE = "New Conversation"
TITLE_MAX_LENGTH = 72

# ponytail: one conservative filename alphabet for both UUIDs and legacy ids.
# Rejects separators, dot segments, NULs, and encoded junk before any join.
_CONVERSATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def ensure_data_dir():
    """Ensure the data directory exists."""
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)


def get_conversation_path(conversation_id: str) -> str:
    """Get the validated file path for a conversation.

    Defense in depth for direct storage callers: only safe filename ids are
    accepted, and the resolved path must stay inside DATA_DIR (so a symlink
    cannot redirect reads/writes outside the configured store).
    """
    if not isinstance(conversation_id, str) or not _CONVERSATION_ID_RE.fullmatch(conversation_id):
        raise ValueError("Invalid conversation id")
    root = Path(DATA_DIR).resolve()
    path = (root / f"{conversation_id}.json").resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError("Invalid conversation id") from None
    return str(path)


def _save_atomic(path: str, data: Dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _now_iso() -> str:
    """UTC timestamp with an explicit zone so browsers format it correctly."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _decode_content(content: Any) -> str:
    """User messages may be plain text or a JSON envelope with ``content``."""
    if not isinstance(content, str):
        return ""
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict) and isinstance(parsed.get("content"), str):
            return parsed["content"]
    except (ValueError, TypeError):
        pass
    return content


def _title_from_content(content: Any) -> str:
    """Derive a short readable title from the first meaningful user message.

    Normalizes all whitespace (newlines, tabs, repeated spaces) and truncates
    to a single readable line. Returns "" when there is no meaningful text.
    """
    text = " ".join(_decode_content(content).split())
    if not text:
        return ""
    if len(text) > TITLE_MAX_LENGTH:
        text = text[: TITLE_MAX_LENGTH - 1].rstrip() + "…"
    return text


def _derive_title(conversation: Dict[str, Any]) -> str:
    """Stored title when meaningful, otherwise the first user message.

    Legacy records may still carry "New Conversation" even though they have a
    user prompt; deriving on read keeps history honest without rewriting
    existing files.
    """
    stored = (conversation.get("title") or "").strip()
    if stored and stored != DEFAULT_TITLE:
        return stored
    for message in conversation.get("messages") or []:
        if isinstance(message, dict) and message.get("role") == "user":
            derived = _title_from_content(message.get("content"))
            if derived:
                return derived
    return DEFAULT_TITLE


def _normalize(conversation: Dict[str, Any]) -> Dict[str, Any]:
    conversation.setdefault("messages", [])
    if not isinstance(conversation["messages"], list):
        conversation["messages"] = []
    if "created_at" not in conversation:
        conversation["created_at"] = _now_iso()
    if "title" not in conversation:
        conversation["title"] = DEFAULT_TITLE
    return conversation


def _is_failed_assistant(message: Dict[str, Any]) -> bool:
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return False
    stage3 = message.get("stage3") or {}
    return stage3.get("success") is False


def create_conversation(conversation_id: str) -> Dict[str, Any]:
    """Create a new, empty conversation with a stable id."""
    ensure_data_dir()
    now = _now_iso()
    conversation = {
        "id": conversation_id,
        "created_at": now,
        "updated_at": now,
        "title": DEFAULT_TITLE,
        "messages": [],
    }
    with _LOCK:
        _save_atomic(get_conversation_path(conversation_id), conversation)
    return conversation


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """Load a conversation from storage (title derived for legacy records)."""
    path = get_conversation_path(conversation_id)
    if not os.path.exists(path):
        return None
    with _LOCK:
        with open(path, "r") as f:
            data = json.load(f)
    if not isinstance(data, dict):
        return None
    _normalize(data)
    if data.get("title") == DEFAULT_TITLE:
        data["title"] = _derive_title(data)
    return data


def save_conversation(conversation: Dict[str, Any]):
    """Save a conversation atomically, bumping ``updated_at`` (last activity)."""
    ensure_data_dir()
    _normalize(conversation)
    if conversation.get("title") == DEFAULT_TITLE:
        derived = _derive_title(conversation)
        if derived != DEFAULT_TITLE:
            conversation["title"] = derived
    conversation["updated_at"] = _now_iso()
    with _LOCK:
        _save_atomic(get_conversation_path(conversation["id"]), conversation)


def list_conversations(include_empty: bool = False) -> List[Dict[str, Any]]:
    """List conversation metadata, newest activity first.

    Args:
        include_empty: Include conversations with zero messages. Defaults to
            False so a session that never produced a user message never
            appears as a misleading "New Conversation" history record.

    Malformed or unreadable records are skipped rather than failing the
    whole history listing.
    """
    ensure_data_dir()
    conversations: List[Dict[str, Any]] = []

    for filename in os.listdir(DATA_DIR):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(DATA_DIR, filename)
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue  # transient write or corrupt record: skip, never crash
        if not isinstance(data, dict) or not data.get("id"):
            continue
        _normalize(data)
        message_count = len(data["messages"])
        if message_count == 0 and not include_empty:
            continue
        conversations.append({
            "id": data["id"],
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at") or data.get("created_at", ""),
            "title": _derive_title(data),
            "message_count": message_count,
        })

    # Newest last activity first; deterministic tie-break for equal
    # timestamps (created_at, then id) so ordering never jitters.
    conversations.sort(
        key=lambda c: (c["updated_at"] or "", c["created_at"] or "", c["id"]),
        reverse=True,
    )
    return conversations


def add_user_message(conversation_id: str, content: str):
    """Append a user message, idempotent for retries.

    A retry is detected when the stored conversation ends with either the
    same user message (assistant result not persisted yet) or an identical
    user message followed by a failed assistant result. In the latter case
    the failed result is removed so the retry's outcome replaces it instead
    of duplicating the turn.
    """
    with _LOCK:
        conversation = get_conversation(conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} not found")

        messages = conversation["messages"]
        if messages and messages[-1].get("role") == "user" and messages[-1].get("content") == content:
            return
        if (len(messages) >= 2
                and _is_failed_assistant(messages[-1])
                and messages[-2].get("role") == "user"
                and messages[-2].get("content") == content):
            # Retry of a failed turn: drop the failed result and keep the
            # already-persisted user message (no duplicate append).
            messages.pop()
            save_conversation(conversation)
            return

        messages.append({"role": "user", "content": content, "created_at": _now_iso()})

        if conversation.get("title", DEFAULT_TITLE) == DEFAULT_TITLE:
            title = _title_from_content(content)
            if title:
                conversation["title"] = title

        save_conversation(conversation)


def add_assistant_message(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
    final_answer: Optional[str] = None,
    final_answer_error: Optional[str] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
):
    """Append an assistant message carrying all 3 stages and the final answer."""
    with _LOCK:
        conversation = get_conversation(conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} not found")

        entry = {
            "role": "assistant",
            "stage1": stage1,
            "stage2": stage2,
            "stage3": stage3,
            "final_answer": final_answer,
            "final_answer_error": final_answer_error,
        }
        if sources:
            entry["sources"] = sources
        if metadata is not None:
            entry["metadata"] = metadata
        conversation["messages"].append(entry)
        save_conversation(conversation)


def update_conversation_title(conversation_id: str, title: str):
    """Update the title of a conversation."""
    with _LOCK:
        conversation = get_conversation(conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} not found")
        conversation["title"] = title
        save_conversation(conversation)
