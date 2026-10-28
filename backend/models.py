"""SQLAlchemy ORM models for CogniSync with Neon PostgreSQL.

This module replaces the JSON-based data store with PostgreSQL tables.
"""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    ForeignKey,
)
from sqlalchemy.orm import (
    relationship,
    DeclarativeBase,
)

from .database import Base


class User(Base):
    """User model replacing the JSON per-user file store."""

    __tablename__ = "users"

    id: str = Column(String, primary_key=True, index=True)
    username: str = Column(String, unique=True, index=True, nullable=False)
    hashed_password: str = Column(String, nullable=False)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    conversations = relationship("Conversation", back_populates="owner", cascade="all, delete-orphan")
    revoked_tokens = relationship("RevokedToken", back_populates="user", cascade="all, delete-orphan")


class Conversation(Base):
    """Conversation model replacing JSON files per conversation."""

    __tablename__ = "conversations"

    id: str = Column(String, primary_key=True, index=True)
    title: str = Column(String, default="New Conversation")
    user_id = Column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    owner = relationship("User", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    """Message model replacing inline messages within conversation JSON."""

    __tablename__ = "messages"

    id: int = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(
        String,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: str = Column(String, nullable=False)  # "user" or "assistant"
    content: str = Column(Text, nullable=False)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    conversation = relationship("Conversation", back_populates="messages")


class RevokedToken(Base):
    """JTI-based revoked token store, replacing JSON file revoked.json."""

    __tablename__ = "revoked_tokens"

    jti: str = Column(String, primary_key=True, unique=True, nullable=False)
    user_id = Column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    revoked_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    user = relationship("User", back_populates="revoked_tokens")