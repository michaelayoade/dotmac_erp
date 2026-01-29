#!/usr/bin/env python3
"""
Fill missing person display names from first and last name.

Defaults to dry-run; pass --apply to commit.
"""

from __future__ import annotations

import argparse

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.person import Person


def _derive_display_name(person: Person) -> str:
    return f"{person.first_name or ''} {person.last_name or ''}".strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill missing display_name values.")
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
        display_name = func.coalesce(Person.display_name, "")
        cleaned = func.lower(func.trim(display_name))
        persons = session.execute(
            select(Person).where(
                (Person.display_name.is_(None))
                | (func.trim(display_name) == "")
                | (cleaned.in_(["none", "null"]))
            )
        ).scalars()

        for person in persons:
            display_name = _derive_display_name(person)
            if not display_name:
                skipped += 1
                continue

            person.display_name = display_name
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
