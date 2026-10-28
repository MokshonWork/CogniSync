"""Conversational storage for CogniSync.

PostgreSQL-backed (SQLAlchemy ORM, models in backend.models), with the
SQLite fallback engine from backend.database for local development/tests.

Preserves the legacy dict-shaped API the app and tests rely on:

- user messages are stored verbatim (may be a plain string or a JSON
  envelope with ``content``);
- assistant messages serialize the full 3-stage entry ({stage1, stage2,
  stage3, final_answer, final_answer_error, sources, metadata}) as JSON in
  the content column and are re-materialized as a dict on read;
- titles derive from the first meaningful user message;
- empty conversations are never listed by default;
- retries do not duplicate turns.

Ownership: every function takes an optional ``user_id``. When supplied
(production always does), reads/writes/deletes are scoped to it; ``None``
targets only unowned conversations. Unowned conversations cannot be
created through the API — they exist only for legacy data import.
"""

import json
import logging
import re
import uuid as uuid_module
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .database import get_db
from .models import Conversation, Message, User

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "New Conversation"
TITLE_MAX_LENGTH = 72

# One conservative id alphabet; API paths are UUIDs, but direct callers get
# defense in depth against separators/traversal here too.
_CONVERSATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def validate_conversation_id(conversation_id: str) -> None:
    if not isinstance(conversation_id, str) or not _CONVERSATION_ID_RE.fullmatch(conversation_id):
        raise ValueError("Invalid conversation id")


def _session() -> Session:
    return next(get_db())


def _no_owner(user_id: Optional[str]) -> bool:
    return user_id is None or user_id == ""


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def create_user(username: str, password: str, user_id: Optional[str] = None) -> User:
    """Create a new user with an Argon2id-hashed password."""
    from sqlalchemy.exc import IntegrityError

    from .auth import hash_password

    user_id = user_id if user_id is not None else str(uuid_module.uuid4())
    db_user = User(id=user_id, username=username, hashed_password=hash_password(password))

    db = _session()
    try:
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user
    except IntegrityError:
        db.rollback()
        raise ValueError(f"Username '{username}' is already taken") from None
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_user_by_id(user_id: str) -> Optional[User]:
    db = _session()
    try:
        return db.query(User).filter(User.id == user_id).first()
    finally:
        db.close()


def get_user_by_username(username: str) -> Optional[User]:
    db = _session()
    try:
        return db.query(User).filter(User.username == username).first()
    finally:
        db.close()


def user_exists(username: str) -> bool:
    return get_user_by_username(username) is not None


# ---------------------------------------------------------------------------
# Titles / content helpers (legacy semantics)
# ---------------------------------------------------------------------------


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
    """Derive a short readable title from the first meaningful user message."""
    text = " ".join(_decode_content(content).split())
    if not text:
        return ""
    if len(text) > TITLE_MAX_LENGTH:
        text = text[: TITLE_MAX_LENGTH - 1].rstrip() + "…"
    return text


def _derive_title(conversation: Dict[str, Any]) -> str:
    """Stored title when meaningful, otherwise the first user message."""
    stored = (conversation.get("title") or "").strip()
    if stored and stored != DEFAULT_TITLE:
        return stored
    for message in conversation.get("messages") or []:
        if isinstance(message, dict) and message.get("role") == "user":
            derived = _title_from_content(message.get("content"))
            if derived:
                return derived
    return DEFAULT_TITLE


def _is_failed_assistant(message: Dict[str, Any]) -> bool:
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return False
    stage3 = message.get("stage3") or {}
    return stage3.get("success") is False


# ---------------------------------------------------------------------------
# Conversation operations
# ---------------------------------------------------------------------------


def create_conversation(
    conversation_id: str,
    user_id: Optional[str] = None,
    title: str = DEFAULT_TITLE,
) -> Dict[str, Any]:
    """Create a new, empty conversation owned by ``user_id`` (may be None)."""
    validate_conversation_id(conversation_id)
    db_conv = Conversation(
        id=conversation_id,
        title=title,
        user_id=user_id or None,
    )
    db = _session()
    try:
        db.add(db_conv)
        db.commit()
        db.refresh(db_conv)
        logger.info("Created conversation %s for user %s", conversation_id, user_id or "none")
        return _conversation_dict(db, db_conv)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _conversation_messages(req_db: Session, conversation_id: str) -> List[Dict[str, Any]]:
    rows = (
        req_db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.id.asc())
        .all()
    )
    return [_message_dict(m) for m in rows]


def _message_dict(m: Message) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "id": m.id,
        "role": m.role,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }
    if m.role == "assistant" and isinstance(m.content, str):
        try:
            payload = json.loads(m.content)
            if isinstance(payload, dict):
                entry.update(payload)
        except (ValueError, TypeError):
            entry["content"] = m.content
        entry.setdefault("content", m.content)
        entry["stage1"] = entry.get("stage1") or []
        entry["stage2"] = entry.get("stage2") or []
    else:
        entry["content"] = m.content
    return entry


def _conversation_dict(db: Session, conv: Conversation) -> Dict[str, Any]:
    return {
        "id": conv.id,
        "title": conv.title,
        "user_id": conv.user_id,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
        "messages": _conversation_messages(db, conv.id),
    }


def get_conversation(
    conversation_id: str,
    user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Load a conversation; when ``user_id`` is given it must match the owner."""
    validate_conversation_id(conversation_id)
    db = _session()
    try:
        query = db.query(Conversation).filter(Conversation.id == conversation_id)
        if user_id is not None:
            query = query.filter(Conversation.user_id == user_id)
        conv = query.first()
        if conv is None:
            return None
        return _conversation_dict(db, conv)
    finally:
        db.close()


def get_conversation_for_user(
    conversation_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """Strictly ownership-checked read (API contract)."""
    validate_conversation_id(conversation_id)
    if not user_id:
        return None
    db = _session()
    try:
        conv = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id)
            .filter(Conversation.user_id == user_id)
            .first()
        )
        if conv is None:
            return None
        return _conversation_dict(db, conv)
    finally:
        db.close()


def get_conversations_for_user(
    user_id: str,
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    db = _session()
    try:
        query = (
            db.query(Conversation)
            .filter(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
        )
        if limit is not None:
            query = query.limit(limit).offset(offset)
        return [_conversation_dict(db, c) for c in query.all()]
    finally:
        db.close()


def list_conversations(
    user_id: Optional[str] = None,
    include_empty: bool = False,
) -> List[Dict[str, Any]]:
    """List conversation metadata, newest activity first.

    ``user_id`` scopes to one owner; None lists every user (legacy/tests).
    Malformed records are skipped rather than failing the whole listing.
    """
    db = _session()
    try:
        query = db.query(Conversation).order_by(
            Conversation.updated_at.desc(),
            Conversation.created_at.desc(),
            Conversation.id.desc(),
        )
        if user_id is not None:
            query = query.filter(Conversation.user_id == user_id)
        conversations: List[Dict[str, Any]] = []
        for conv in query.all():
            messages = _conversation_messages(db, conv.id)
            entry = {
                "id": conv.id,
                "title": conv.title,
                "user_id": conv.user_id,
                "created_at": conv.created_at.isoformat() if conv.created_at else None,
                "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
                "message_count": len(messages),
            }
            if entry["message_count"] or include_empty:
                entry["title"] = _derive_title(
                    {"title": conv.title, "messages": messages}
                )
                conversations.append(entry)
        # Newest last activity first; deterministic tie-break so ordering
        # never jitters between equal timestamps.
        conversations.sort(
            key=lambda c: (c["updated_at"] or "", c["created_at"] or "", c["id"]),
            reverse=True,
        )
        return conversations
    finally:
        db.close()


def delete_conversation_for_user(
    conversation_id: str,
    user_id: str,
) -> bool:
    """Delete one conversation, ownership-checked."""
    validate_conversation_id(conversation_id)
    db = _session()
    try:
        conv = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id)
            .filter(Conversation.user_id == user_id)
            .first()
        )
        if conv is None:
            return False
        db.delete(conv)
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def update_conversation_title(
    conversation_id: str,
    title: str,
    user_id: Optional[str] = None,
) -> bool:
    """Update the title; ownership-checked when ``user_id`` is supplied."""
    validate_conversation_id(conversation_id)
    db = _session()
    try:
        query = db.query(Conversation).filter(Conversation.id == conversation_id)
        if user_id is not None:
            query = query.filter(Conversation.user_id == user_id)
        conv = query.first()
        if conv is None:
            return False
        conv.title = title
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _touch_updated_at(conversation_id: str) -> None:
    db2 = _session()
    try:
        conv = db2.query(Conversation).filter(Conversation.id == conversation_id).first()
        if conv is not None:
            conv.updated_at = datetime.now(timezone.utc)
            db2.commit()
    except Exception:
        db2.rollback()
        raise
    finally:
        db2.close()


# ---------------------------------------------------------------------------
# Message operations
# ---------------------------------------------------------------------------


def add_user_message(
    conversation_id: str,
    content: str,
    user_id: Optional[str] = None,
) -> Message:
    """Append a user message, idempotent for retries (see module docstring)."""
    validate_conversation_id(conversation_id)
    db = _session()
    try:
        query = db.query(Conversation).filter(Conversation.id == conversation_id)
        if not _no_owner(user_id):
            query = query.filter(Conversation.user_id == user_id)
        else:
            query = query.filter(Conversation.user_id.is_(None))
        conv = query.first()
        if conv is None:
            raise ValueError(
                f"Conversation {conversation_id} not found for user {user_id or 'none'}"
            )

        tail = _conversation_messages(db, conversation_id)
        if tail and tail[-1].get("role") == "user" and tail[-1].get("content") == content:
            return db.query(Message).filter(Message.id == tail[-1]["id"]).first()
        if (
            len(tail) >= 2
            and _is_failed_assistant(tail[-1])
            and tail[-2].get("role") == "user"
            and tail[-2].get("content") == content
        ):
            # Retry of a failed turn: drop the failed result, keep the
            # already-persisted user message (no duplicate append).
            db.query(Message).filter(Message.id == tail[-1]["id"]).delete()
            db.commit()
            _touch_updated_at(conversation_id)
            return None

        msg = Message(conversation_id=conversation_id, role="user", content=content)
        db.add(msg)
        if conv.title in (None, DEFAULT_TITLE):
            title = _title_from_content(content)
            if title:
                conv.title = title
        db.commit()
        db.refresh(msg)
        _touch_updated_at(conversation_id)
        return msg
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def add_assistant_message(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
    final_answer: Optional[str] = None,
    final_answer_error: Optional[str] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
    user_id: Optional[str] = None,
) -> Message:
    """Append an assistant message carrying all 3 stages and the final answer."""
    validate_conversation_id(conversation_id)
    entry = {
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

    db = _session()
    try:
        query = db.query(Conversation).filter(Conversation.id == conversation_id)
        if not _no_owner(user_id):
            query = query.filter(Conversation.user_id == user_id)
        else:
            query = query.filter(Conversation.user_id.is_(None))
        if query.first() is None:
            raise ValueError(
                f"Conversation {conversation_id} not found for user {user_id or 'none'}"
            )
        msg = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=json.dumps(entry),
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)
        _touch_updated_at(conversation_id)
        return msg
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_messages_for_conversation(
    conversation_id: str,
    user_id: str,
) -> List[Message]:
    """All messages for an owned conversation (strict ownership check)."""
    validate_conversation_id(conversation_id)
    db = _session()
    try:
        conv = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id)
            .filter(Conversation.user_id == user_id)
            .first()
        )
        if conv is None:
            return []
        return list(
            db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.id.asc())
            .all()
        )
    finally:
        db.close()
