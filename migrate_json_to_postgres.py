"""JSON-to-PostgreSQL migration for CogniSync.

Imports legacy JSON data (data/users, data/conversations,
data/users/revoked.json) into the PostgreSQL schema from alembic.

Usage:
    python migrate_json_to_postgres.py --yes            # run
    python migrate_json_to_postgres.py --dry-run        # preview without writes
Optional:
    --legacy-user <username>   Assign ownerless legacy conversations to an
                               existing user account. Without it, ownerless
                               conversations are imported as unowned (they
                               will not appear for any user until reassigned).

Properties:
- Reads DATABASE_URL from the environment; never accepts a connection string
  on the command line.
- Idempotent/resumable: rows that already exist (by id/username) are skipped.
- Validates records; malformed files are skipped and reported, never dropped
  silently.
- Never deletes or modifies the source JSON files.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from backend.database import Base, SessionLocal, engine, _normalize_url
from backend.models import Conversation, Message, RevokedToken, User

DATA_DIR = Path("data/users")
CONVERSATIONS_DIR = Path("data/conversations")
REVOKED_PATH = DATA_DIR / "revoked.json"


class Report:
    def __init__(self):
        self.users = {"imported": 0, "skipped_existing": 0, "malformed": []}
        self.conversations = {"imported": 0, "messages": 0, "unowned": 0,
                              "assigned": 0, "skipped_existing": 0, "malformed": []}
        self.revoked = {"imported": 0, "skipped_existing": 0, "malformed": []}


def _parse_dt(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _user_row(path: Path, data: Any):
    """Validate one user JSON record; return (id, username, hashed_password, created_at) or None."""
    if not isinstance(data, dict):
        return None
    user_id = data.get("id") or path.stem
    username = data.get("username") or ""
    hashed = data.get("hashed_password") or data.get("password_hash") or ""
    created_at = _parse_dt(data.get("created_at"))
    if not user_id or not username or not hashed:
        return None
    return user_id, username, hashed, created_at


def _conversation_row(path: Path, data: Any, legacy_user_id: Optional[str]):
    """Validate one conversation JSON record; return (row, messages) or None."""
    if not isinstance(data, dict):
        return None
    conv_id = data.get("id") or path.stem
    if not conv_id:
        return None
    title = data.get("title") or "New Conversation"
    user_id = data.get("user_id") or None
    unowned = user_id is None
    if user_id is None and legacy_user_id:
        user_id = legacy_user_id
    messages = []
    for m in data.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role") or "user"
        if role == "assistant" and m.get("stage3") is not None:
            # Runtime storage serializes assistant entries as JSON content.
            entry = dict(m)
            entry.pop("id", None)
            entry.pop("created_at", None)
            content = json.dumps(entry)
        else:
            content = m.get("content") or ""
            if not isinstance(content, str):
                content = json.dumps(m.get("content"))
        messages.append((role, content, _parse_dt(m.get("created_at"))))
    return {
        "id": conv_id,
        "title": title[:255],
        "user_id": user_id,
        "created_at": _parse_dt(data.get("created_at")),
        "updated_at": _parse_dt(data.get("updated_at")),
    }, messages, unowned


def _load_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def migrate_users(session, report: Report, dry_run: bool) -> None:
    existing_ids = {row[0] for row in session.query(User.id).all()}
    existing_names = {row[0] for row in session.query(User.username).all()}
    if not DATA_DIR.exists():
        print(f"[users] no directory at {DATA_DIR} — skipping")
        return
    for path in sorted(DATA_DIR.glob("*.json")):
        if path.name == "revoked.json":
            continue
        row = _user_row(path, _load_json(path))
        if row is None:
            report.users["malformed"].append(str(path))
            continue
        user_id, username, hashed, created_at = row
        if user_id in existing_ids or username in existing_names:
            report.users["skipped_existing"] += 1
            continue
        session.add(User(id=user_id, username=username, hashed_password=hashed,
                         created_at=created_at))
        existing_ids.add(user_id)
        existing_names.add(username)
        report.users["imported"] += 1
    if not dry_run:
        session.commit()
    else:
        session.rollback()


def migrate_conversations(session, report: Report, legacy_user_id: Optional[str], dry_run: bool) -> None:
    existing_ids = {row[0] for row in session.query(Conversation.id).all()}
    known_users = {row[0] for row in session.query(User.id).all()}
    if not CONVERSATIONS_DIR.exists():
        print(f"[conversations] no directory at {CONVERSATIONS_DIR} — skipping")
        return
    if legacy_user_id and legacy_user_id not in known_users:
        raise SystemExit(
            f"--legacy-user '{legacy_user_id}' does not exist in the database."
        )
    for path in sorted(CONVERSATIONS_DIR.glob("*.json")):
        loaded = _load_json(path)
        if loaded is None:
            report.conversations["malformed"].append(str(path))
            continue
        parsed = _conversation_row(path, loaded, legacy_user_id)
        if parsed is None:
            report.conversations["malformed"].append(str(path))
            continue
        row, messages, unowned = parsed
        if row["id"] in existing_ids:
            report.conversations["skipped_existing"] += 1
            continue
        if row["user_id"] is not None and row["user_id"] not in known_users:
            # Owner does not exist; import unowned rather than violate the FK.
            row = dict(row, user_id=None)
            unowned = True
        if unowned:
            report.conversations["unowned"] += 1
        elif legacy_user_id:
            report.conversations["assigned"] += 1
        if dry_run:
            report.conversations["imported"] += 1
            report.conversations["messages"] += len(messages)
            continue
        session.add(Conversation(**row))
        existing_ids.add(row["id"])
        for i, (role, content, created_at) in enumerate(messages):
            session.add(Message(conversation_id=row["id"], role=role,
                                content=content, created_at=created_at))
            # created_at may collide; the query sequence is authoritative.
        report.conversations["imported"] += 1
        report.conversations["messages"] += len(messages)
    if dry_run:
        session.rollback()
    else:
        session.commit()


def migrate_revoked_tokens(session, report: Report, dry_run: bool) -> None:
    if not REVOKED_PATH.exists():
        print(f"[revoked] no file at {REVOKED_PATH} — skipping")
        return
    data = _load_json(REVOKED_PATH)
    if not isinstance(data, dict):
        report.revoked["malformed"].append(str(REVOKED_PATH))
        return
    existing = {row[0] for row in session.query(RevokedToken.jti).all()}
    for jti, revoked_at in data.items():
        if jti in existing:
            report.revoked["skipped_existing"] += 1
            continue
        session.add(RevokedToken(jti=jti, user_id=None, revoked_at=_parse_dt(revoked_at)))
        existing.add(jti)
        report.revoked["imported"] += 1
    if dry_run:
        session.rollback()
    else:
        session.commit()


def _print_report(report: Report) -> None:
    r = report
    print("\n=== Migration report ===")
    print(f"users:          imported={r.users['imported']} "
          f"skipped_existing={r.users['skipped_existing']} malformed={len(r.users['malformed'])}")
    print(f"conversations:  imported={r.conversations['imported']} "
          f"messages={r.conversations['messages']} unowned={r.conversations['unowned']} "
          f"reassigned={r.conversations['assigned']} "
          f"skipped_existing={r.conversations['skipped_existing']} "
          f"malformed={len(r.conversations['malformed'])}")
    print(f"revoked tokens: imported={r.revoked['imported']} "
          f"skipped_existing={r.revoked['skipped_existing']} malformed={len(r.revoked['malformed'])}")
    for section in (r.users, r.conversations, r.revoked):
        if section["malformed"]:
            print("malformed records (skipped, files left untouched):")
            for path in section["malformed"]:
                print(f"  {path}")
    if r.conversations["unowned"]:
        print(
            "NOTE: ownerless conversations exist. Pass --legacy-user <existing username> "
            "to assign them, or update conversations.user_id manually."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true",
                        help="required to actually import data")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and report without writing")
    parser.add_argument("--legacy-user", default=None,
                        help="existing username to own ownerless legacy conversations")
    args = parser.parse_args()

    if args.yes and args.dry_run:
        parser.error("--yes and --dry-run are mutually exclusive")
    if not args.yes and not args.dry_run:
        parser.error("pass --yes to import, or --dry-run to preview")

    url = _normalize_url(str(engine.url))
    if not url.startswith("postgresql"):
        print("WARNING: DATABASE_URL does not point at PostgreSQL "
              f"({url}) — is that intended?")

    print("Creating missing tables if needed (idempotent)...")
    Base.metadata.create_all(bind=engine)

    report = Report()
    session = SessionLocal()
    try:
        migrate_users(session, report, args.dry_run)
        migrate_conversations(session, report, args.legacy_user, args.dry_run)
        migrate_revoked_tokens(session, report, args.dry_run)
    except IntegrityError as exc:
        session.rollback()
        print(f"Migration failed mid-way (rolled back): {exc.orig}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        session.close()

    _print_report(report)
    if args.dry_run:
        print("\nDry run only — nothing was written. Re-run with --yes to import.")


if __name__ == "__main__":
    main()
