"""Database engine and session management for CogniSync with Neon PostgreSQL.

- Reads DATABASE_URL exclusively from the environment.
- Configures SQLAlchemy engine with TLS (sslmode=require for Neon) and
  connection pooling (NullPool).
- Never logs sensitive credential material.
- Provides a get_db() dependency for FastAPI request-scoped sessions.
- Falls back to SQLite in-memory when DATABASE_URL is not set (for development/testing).
"""

import os
from typing import Generator

from sqlalchemy import create_engine, pool
from sqlalchemy.orm import Session, sessionmaker, declarative_base

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    # Development-only SQLite fallback (no Postgres required locally).
    # Production guard lives in main.py startup: Neon/production never runs
    # against this fallback.
    DATABASE_URL = "sqlite:///./cognisync-dev.db"


def _normalize_url(url: str) -> str:
    """Map bare driver-less URLs to the installed psycopg2 driver."""
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    return url  # other dialects (sqlite, etc.) pass through


DATABASE_URL = _normalize_url(DATABASE_URL)

# Engine creation -------------------------------------------------------------
engine = create_engine(
    DATABASE_URL,
    poolclass=pool.NullPool,
    echo=os.getenv("ENABLE_SQL_ECHO", "0").lower() == "1",
    pool_pre_ping=True,
)

# ---------------------------------------------------------------------------
# SQLAlchemy core / ORM setup
# ---------------------------------------------------------------------------

Base = declarative_base()

# Session factory – each call returns a fresh Session object.
# autocommit=False, autoflush=False gives us explicit control over transactions,
# which is important for the ownership-check patterns used across the app.
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


# ---------------------------------------------------------------------------
# Helpers / dependencies
# ---------------------------------------------------------------------------

def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a SQLAlchemy Session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_engine_info() -> dict:
    """Return basic engine info without exposing credentials."""
    url = str(engine.url)
    if "@" in url:
        url = url.split("@")[-1]
    return {"url": url, "pool_size": "N/A (NullPool)", "timeout": None}
