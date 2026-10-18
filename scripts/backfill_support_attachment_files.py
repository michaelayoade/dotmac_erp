#!/usr/bin/env python
"""Backfill ``dotmac-files`` records for pre-cutover support attachments.

Legacy ``support.ticket_attachment.storage_path`` values are relative
paths served from ``support/<storage_path>`` object keys (or a local
upload tree). For each such row this script reads the bytes, re-uploads
through the tenant staging seam under the ticket's organization, and
rewrites ``storage_path`` to the canonical tenant storage key -- one
transaction per attachment, failures named, legacy objects left for
orphan reconciliation.

Usage:
    python scripts/backfill_support_attachment_files.py --database-url postgresql://... --dry-run
    python scripts/backfill_support_attachment_files.py --database-url postgresql://...

``--dry-run`` performs no writes. Exit code 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.support.attachment import TicketAttachment
from app.models.support.ticket import Ticket
from app.services.storage import get_dotmac_files_provider, get_storage
from app.services.support.attachment import _stage_support_file, staged_file_id


def _read_legacy(attachment: TicketAttachment) -> bytes | None:
    storage_path = attachment.storage_path or ""
    if Path(storage_path).is_absolute():
        local = Path(storage_path)
        return local.read_bytes() if local.is_file() else None
    key = (
        storage_path
        if storage_path.startswith("support/")
        else f"support/{storage_path}"
    )
    provider = get_dotmac_files_provider()
    if provider.exists(key):
        with provider.open(key) as readable:
            return readable.read()
    storage = get_storage()
    if storage.exists(key):
        chunks, _content_type, _length = storage.stream(key)
        return b"".join(chunks)
    local = Path(storage_path)
    if local.is_file():
        return local.read_bytes()
    return None


def backfill(db: Session, *, dry_run: bool) -> dict:
    summary = {"candidates": 0, "present": 0, "backfilled": 0, "failures": []}
    attachments = db.scalars(select(TicketAttachment)).all()
    for attachment in attachments:
        if staged_file_id(attachment) is not None:
            continue
        summary["candidates"] += 1
        try:
            ticket = db.get(Ticket, attachment.ticket_id)
            if ticket is None:
                summary["failures"].append(
                    {
                        "attachment_id": str(attachment.attachment_id),
                        "detail": f"ticket not found: {attachment.ticket_id}",
                    }
                )
                continue
            content = _read_legacy(attachment)
        except Exception as exc:  # named per row; the run continues
            summary["failures"].append(
                {
                    "attachment_id": str(attachment.attachment_id),
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        if content is None:
            summary["failures"].append(
                {
                    "attachment_id": str(attachment.attachment_id),
                    "detail": f"legacy object not found: {attachment.storage_path}",
                }
            )
            continue
        summary["present"] += 1
        if dry_run:
            continue
        try:
            prepared = _stage_support_file(
                db,
                ticket.organization_id,
                file_data=content,
                content_type=attachment.content_type,
                original_filename=attachment.filename,
            )
            attachment.storage_path = prepared.storage_key
            attachment.file_size = prepared.size_bytes
            db.commit()
            summary["backfilled"] += 1
        except Exception as exc:  # named per row; the run continues
            db.rollback()
            summary["failures"].append(
                {
                    "attachment_id": str(attachment.attachment_id),
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
