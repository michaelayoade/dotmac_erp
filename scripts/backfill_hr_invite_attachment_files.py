#!/usr/bin/env python
"""Backfill ``dotmac-files`` records for default HR invite attachments.

Legacy invite-attachment metadata (a ``DomainSetting`` JSON value per
organization) carries an ``hr_invite_attachments/...`` object key in
``s3_key``. For each active setting this script reads the bytes,
re-uploads through the tenant staging seam, and rewrites ``s3_key``
to the canonical tenant storage key -- one transaction per
organization, failures named, legacy objects left for orphan
reconciliation.

Usage:
    python scripts/backfill_hr_invite_attachment_files.py --database-url postgresql://... --dry-run
    python scripts/backfill_hr_invite_attachment_files.py --database-url postgresql://...

``--dry-run`` performs no writes. Exit code 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.domain_settings import DomainSetting, SettingDomain
from app.services.people.hr.invite_attachment import (
    DEFAULT_INVITE_ATTACHMENT_KEY,
    stage_default_invite_attachment,
    staged_file_id,
)
from app.services.storage import get_dotmac_files_provider, get_storage


def _read_legacy(s3_key: str) -> bytes | None:
    provider = get_dotmac_files_provider()
    if provider.exists(s3_key):
        with provider.open(s3_key) as readable:
            return readable.read()
    storage = get_storage()
    if storage.exists(s3_key):
        chunks, _content_type, _length = storage.stream(s3_key)
        return b"".join(chunks)
    return None


def backfill(db: Session, *, dry_run: bool) -> dict:
    summary = {"candidates": 0, "present": 0, "backfilled": 0, "failures": []}
    settings = db.scalars(
        select(DomainSetting).where(
            DomainSetting.domain == SettingDomain.settings,
            DomainSetting.key == DEFAULT_INVITE_ATTACHMENT_KEY,
            DomainSetting.is_active.is_(True),
        )
    ).all()
    for setting in settings:
        metadata = setting.value_json if isinstance(setting.value_json, dict) else None
        if not metadata or staged_file_id(metadata) is not None:
            continue
        s3_key = str(metadata.get("s3_key") or "")
        if not s3_key:
            continue
        org_id = setting.organization_id
        summary["candidates"] += 1
        try:
            content = _read_legacy(s3_key)
        except Exception as exc:  # named per row; the run continues
            summary["failures"].append(
                {
                    "organization_id": str(org_id),
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        if content is None:
            summary["failures"].append(
                {
                    "organization_id": str(org_id),
                    "detail": f"legacy object not found: {s3_key}",
                }
            )
            continue
        summary["present"] += 1
        if dry_run:
            continue
        try:
            prepared = stage_default_invite_attachment(
                db,
                org_id,
                file_data=content,
                content_type=str(metadata.get("content_type") or "") or None,
                original_filename=str(metadata.get("filename") or "") or None,
            )
            setting.value_json = {
                **metadata,
                "s3_key": prepared.storage_key,
                "file_size": prepared.size_bytes,
            }
            db.commit()
            summary["backfilled"] += 1
        except Exception as exc:  # named per row; the run continues
            db.rollback()
            summary["failures"].append(
                {
                    "organization_id": str(org_id),
                    "detail": f"{type(exc).__name__}: {exc}",
                }
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
