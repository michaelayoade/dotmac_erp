#!/usr/bin/env python
"""Backfill ``dotmac-files`` records for pre-cutover project and task attachments.

Legacy ``common.attachment.file_path`` values are relative paths served
from ``attachments/<file_path>`` object keys (or a local upload tree).
For each such row this script reads the bytes, re-uploads through the
tenant staging seam, and rewrites ``file_path`` to the canonical tenant
storage key -- one transaction per attachment, failures named, legacy
objects left for orphan reconciliation.

Usage:
    python scripts/backfill_pm_attachment_files.py --dry-run
    python scripts/backfill_pm_attachment_files.py

``--dry-run`` performs no writes. Exit code 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session_context import session_for_org
from app.models.finance.common.attachment import Attachment, AttachmentCategory
from app.services.file_upload import get_pm_attachment_upload
from app.services.finance.common.attachment import (
    _stage_attachment_file,
    staged_file_id,
)
from app.services.storage import get_dotmac_files_provider, get_storage
from app.tenant_catalog import organization_ids


def _read_legacy(attachment: Attachment) -> bytes | None:
    file_path = attachment.file_path or ""
    key = file_path if file_path.startswith("projects/") else f"projects/{file_path}"
    provider = get_dotmac_files_provider()
    if provider.exists(key):
        with provider.open(key) as readable:
            return readable.read()
    storage = get_storage()
    if storage.exists(key):
        chunks, _content_type, _length = storage.stream(key)
        return b"".join(chunks)
    local = Path(file_path)
    if local.is_file():
        return local.read_bytes()
    return None


def backfill(db: Session, *, dry_run: bool) -> dict:
    summary = {"candidates": 0, "present": 0, "backfilled": 0, "failures": []}
    attachments = db.scalars(select(Attachment)).all()
    for attachment in attachments:
        # Only PM categories here; finance categories stage under the
        # finance upload policy in backfill_finance_attachment_files.py.
        if attachment.category not in (
            AttachmentCategory.PROJECT,
            AttachmentCategory.TASK,
        ):
            continue
        if staged_file_id(attachment) is not None:
            continue
        summary["candidates"] += 1
        try:
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
                    "detail": f"legacy object not found: {attachment.file_path}",
                }
            )
            continue
        summary["present"] += 1
        if dry_run:
            continue
        try:
            prepared = _stage_attachment_file(
                db,
                attachment.organization_id,
                file_data=content,
                content_type=attachment.content_type,
                original_filename=attachment.file_name,
                config=get_pm_attachment_upload().config,
            )
            attachment.file_path = prepared.storage_key
            attachment.file_size = prepared.size_bytes
            attachment.checksum = prepared.checksum_sha256.removeprefix("sha256:")
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


def _merge(total: dict, part: dict) -> None:
    for key, value in part.items():
        if isinstance(value, list):
            total.setdefault(key, []).extend(value)
        elif isinstance(value, int):
            total[key] = total.get(key, 0) + value
        else:
            total[key] = value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    summary: dict = {}
    # Cross-tenant authority is limited to discovering organizations.
    # Each organization's reads and writes use a fresh tenant session so
    # the ORM org filter and both RLS GUCs are primed (session_for_org).
    for org_id in organization_ids(include_inactive=True):
        with session_for_org(org_id) as db:
            _merge(summary, backfill(db, dry_run=args.dry_run))
    json.dump(summary, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 1 if summary.get("failures") else 0


if __name__ == "__main__":
    raise SystemExit(main())
