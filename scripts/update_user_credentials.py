#!/usr/bin/env python3
"""
Bulk update local user credentials:
- Set username to person's email
- Set password to default (hashed)
- Require password change on next login

Excludes users whose display name is "Admin".
"""

from __future__ import annotations

from datetime import datetime, timezone
import argparse

from sqlalchemy import select

from app.db import SessionLocal
from app.models.auth import AuthProvider, UserCredential
from app.models.person import Person
from app.services.auth_flow import hash_password


DEFAULT_PASSWORD = "Dotmac@123"


def _person_display_name(person: Person) -> str:
    display = (
        person.display_name
        or f"{person.first_name or ''} {person.last_name or ''}".strip()
    )
    return display.strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update usernames/passwords for local users."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry-run).",
    )
    args = parser.parse_args()

    session = SessionLocal()
    updated = 0
    skipped = 0

    try:
        persons = session.execute(select(Person)).scalars().all()
        for person in persons:
            email = (person.email or "").strip()
            if not email:
                skipped += 1
                continue

            display_name = _person_display_name(person).lower()
            if display_name == "admin":
                skipped += 1
                continue

            credential = (
                session.query(UserCredential)
                .filter(UserCredential.person_id == person.id)
                .filter(UserCredential.provider == AuthProvider.local)
                .first()
            )

            current_username = (credential.username or "").strip() if credential else ""
            if current_username.lower() == email.lower():
                skipped += 1
                continue

            if credential is None:
                credential = UserCredential(
                    person_id=person.id,
                    provider=AuthProvider.local,
                )
                session.add(credential)

            credential.username = email
            credential.password_hash = hash_password(DEFAULT_PASSWORD)
            credential.must_change_password = True
            credential.password_updated_at = datetime.now(timezone.utc)
            credential.is_active = True
            updated += 1

        if args.apply:
            session.commit()
        else:
            session.rollback()

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"{mode}: updated={updated} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
