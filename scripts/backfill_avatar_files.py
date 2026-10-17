#!/usr/bin/env python
"""Backfill ``dotmac-files`` records for pre-cutover avatar objects.

Avatars uploaded before the files cutover live at legacy keys
(``avatars/<person>_<rand>.<ext>``) with no ``platform_stored_files``
row, and ``person.avatar_url`` points at the filename form. For each
such person this script re-uploads the object bytes through
``prepare_upload`` (canonical platform key, policy-checked), stages the
file record, and rewrites ``avatar_url`` to the file-id form -- one
transaction per person, so a failure leaves that person untouched and
re-running is safe. Legacy objects are left in place for the existing
orphan-reconciliation machinery; nothing is deleted here.

Usage:
    python scripts/backfill_avatar_files.py --database-url postgresql://... --dry-run
    python scripts/backfill_avatar_files.py --database-url postgresql://...

``--dry-run`` performs no writes: it reports how many avatar URLs are
legacy-shaped and how many of their objects are actually present.
Output is a JSON summary; failures are named per person, never skipped
silently. Exit code 1 when any candidate could not be backfilled.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import PurePath

from dotmac_files import FilePolicy, prepare_upload
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.person import Person
from app.services.avatar import avatar_file_id
from app.services.file_object_cleanup import stage_platform_file
from app.tenancy import platform_scope
from app.services.storage import get_dotmac_files_provider

_MEDIA_BY_EXTENSION = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _legacy_filename(avatar_url: str | None) -> str | None:
    if not avatar_url or avatar_file_id(avatar_url) is not None:
        return None
    for prefix in ("/files/avatars/", f"{settings.avatar_url_prefix}/"):
        if avatar_url.startswith(prefix):
            name = avatar_url[len(prefix) :]
            return name if name and PurePath(name).name == name else None
    return None


def backfill(db: Session, *, dry_run: bool) -> dict:
    provider = get_dotmac_files_provider()
    policy = FilePolicy(
        max_bytes=settings.avatar_max_size_bytes,
        allowed_extensions=frozenset(_MEDIA_BY_EXTENSION),
        allowed_media_types=frozenset(_MEDIA_BY_EXTENSION.values()),
    )
    people = db.scalars(select(Person).where(Person.avatar_url.is_not(None))).all()
    summary = {"candidates": 0, "present": 0, "backfilled": 0, "failures": []}
    for person in people:
        filename = _legacy_filename(person.avatar_url)
        if filename is None:
            continue
        summary["candidates"] += 1
        key = f"avatars/{filename}"
        if not provider.exists(key):
            summary["failures"].append(
                {"person_id": str(person.id), "detail": f"object {key} missing"}
            )
            continue
        summary["present"] += 1
        if dry_run:
            continue
        try:
            with provider.open(key) as readable:
                content = readable.read()
            prepared = prepare_upload(
                provider,
                scope=platform_scope(),
                policy=policy,
                original_filename=filename,
                declared_media_type=_MEDIA_BY_EXTENSION.get(
                    PurePath(filename).suffix.lower(), ""
                ),
                chunks=iter([content]),
            )
            stage_platform_file(db, prepared=prepared)
            person.avatar_url = f"/files/avatars/{prepared.id}"
            db.commit()
            summary["backfilled"] += 1
        except Exception as exc:  # named per person; the run continues
            db.rollback()
            summary["failures"].append(
                {"person_id": str(person.id), "detail": f"{type(exc).__name__}: {exc}"}
            )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    engine = create_engine(args.database_url)
    try:
        with sessionmaker(bind=engine)() as db:
            summary = backfill(db, dry_run=args.dry_run)
    finally:
        engine.dispose()
    json.dump(summary, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 1 if summary["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
