"""Authentication for CogniSync.

Password hashing with Argon2id, signed JWT access tokens with validated
expiry/issuer/audience/subject claims, and a JTI-based revocation store so
logout has real (if short-lived) semantics.

All JWT material comes from backend.config (documented env vars), never from
ad-hoc os.getenv reads.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    JWT_ALGORITHM,
    JWT_AUDIENCE,
    JWT_ISSUER,
    JWT_SECRET,
)

# ---------------------------------------------------------------------------
# Password hashing (Argon2id)
# ---------------------------------------------------------------------------

_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    """Return an Argon2id hash of ``password``."""
    return _hasher.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify ``plain`` against ``hashed``."""
    try:
        _hasher.verify(hashed, plain)
        return True
    except VerifyMismatchError:
        return False


def authenticate_user(username: str, password: str) -> Optional[Dict]:
    """Authenticate a user by username and password."""
    user = get_user_by_username(username)
    if user is None:
        return None
    if verify_password(password, user["hashed_password"]):
        return user
    return None


# ---------------------------------------------------------------------------
# User storage (PostgreSQL via SQLAlchemy ORM)
# ---------------------------------------------------------------------------

from .database import get_db, SessionLocal  # noqa: E402
from .models import RevokedToken as RevokedTokenModel  # noqa: E402


def get_user_by_id(user_id: str) -> Optional[Dict]:
    """Get a user by ID."""
    from .storage import get_user_by_id as _db_get_user_by_id

    user = _db_get_user_by_id(user_id)
    if user is None:
        return None
    return {
        "id": user.id,
        "username": user.username,
        "hashed_password": user.hashed_password,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def get_user_by_username(username: str) -> Optional[Dict]:
    """Get a user by username."""
    from .storage import get_user_by_username as _db_get_user_by_username

    user = _db_get_user_by_username(username)
    if user is None:
        return None
    return {
        "id": user.id,
        "username": user.username,
        "hashed_password": user.hashed_password,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def create_user(username: str, password: str) -> Dict:
    """Create a new user; the password is Argon2id-hashed in storage."""
    from .storage import create_user as storage_create_user

    user = storage_create_user(username=username, password=password)
    return {
        "id": user.id,
        "username": user.username,
        "hashed_password": user.hashed_password,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


# ---------------------------------------------------------------------------
# Token revocation (PostgreSQL-backed)
# ---------------------------------------------------------------------------


def revoke_token(jti: str, user_id: str) -> None:
    """Mark a token JTI as revoked (logout). Cleanup runs opportunistically."""
    db = next(get_db())
    try:
        existing = (
            db.query(RevokedTokenModel).filter(RevokedTokenModel.jti == jti).first()
        )
        if not existing:
            db.add(RevokedTokenModel(jti=jti, user_id=user_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    cleanup_expired_revocations()


def is_token_revoked(jti: str) -> bool:
    """Check if a token JTI is revoked.

    Fail-closed: if the revocation store is unavailable, tokens can't be
    proven valid — a read failure raises instead of silently returning False.
    """
    db = next(get_db())
    try:
        return (
            db.query(RevokedTokenModel).filter(RevokedTokenModel.jti == jti).first()
            is not None
        )
    except Exception as exc:
        raise TokenStoreUnavailable("revocation store unavailable") from exc
    finally:
        db.close()


def cleanup_expired_revocations() -> None:
    """Remove revocation entries whose access tokens have already expired."""
    from .config import ACCESS_TOKEN_EXPIRE_MINUTES as _ATTEM

    db = next(get_db())
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=_ATTEM + 1
        )
        db.query(RevokedTokenModel).filter(
            RevokedTokenModel.revoked_at < cutoff
        ).delete()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Token validation
# ---------------------------------------------------------------------------


class InvalidTokenError(Exception):
    """Raised when a token is malformed, expired, signed incorrectly, or revoked."""


class TokenStoreUnavailable(Exception):
    """Raised when the revocation store cannot be read.

    Tokens can never be vouched for when revocation state is unknown, so
    callers must treat requests as unauthenticated (fail-closed).
    """


def create_access_token(user_id: str, username: str) -> str:
    """Create a signed JWT access token for a user.

    ``sub`` carries the stable user id (conversation ownership anchors on
    it); ``username`` is carried as a display claim. ``jti`` enables
    server-side revocation.
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "username": username,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": expire,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict:
    """Validate and decode an access token.

    Checks signature, expiry, issuer, audience, token type, and revocation.
    """
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("Token has expired") from exc
    except jwt.InvalidIssuerError as exc:
        raise InvalidTokenError("Invalid token issuer") from exc
    except jwt.InvalidAudienceError as exc:
        raise InvalidTokenError("Invalid token audience") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError("Invalid token") from exc

    if payload.get("type") != "access":
        raise InvalidTokenError("Invalid token type")

    jti = payload.get("jti")
    if not jti:
        raise InvalidTokenError("Invalid token claims")
    if is_token_revoked(jti):
        raise InvalidTokenError("Token has been revoked")

    return payload
