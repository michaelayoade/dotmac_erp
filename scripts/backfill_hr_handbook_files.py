#!/usr/bin/env python
"""Backfill ``dotmac-files`` records for pre-cutover HR handbook documents.

Legacy ``hr_documents.file_path`` values are handbook object keys
(``hr_documents/<org>/<file>``, sometimes ``s3://``-prefixed or
prefix-relative). For each such row this script reads the bytes,
re-uploads through the handbook staging seam, and rewrites
``file_path`` to the canonical tenant storage key -- one transaction
per document, failures named, legacy objects left for orphan
reconciliation.

Usage:
    python scripts/backfill_hr_handbook_files.py --dry-run
    python scripts/backfill_hr_handbook_files.py

``--dry-run`` performs no writes. Exit code 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session_context import session_for_org
from app.models.people.hr.handbook import HRDocument
from app.services.people.hr.handbook_service import HRDocumentService
from app.services.storage import get_dotmac_files_provider, get_storage
from app.tenant_catalog import organization_ids


def _read_legacy(document: HRDocument) -> bytes | None:
    key = HRDocumentService._document_storage_key(document)
    provider = get_dotmac_files_provider()
    if provider.exists(key):
        with provider.open(key) as readable:
            return readable.read()
    storage = get_storage()
    if storage.exists(key):
        chunks, _content_type, _length = storage.stream(key)
        return b"".join(chunks)
    return None


def backfill(db: Session, *, dry_run: bool) -> dict:
    summary = {"candidates": 0, "present": 0, "backfilled": 0, "failures": []}
    service = HRDocumentService(db)
    for document in db.scalars(select(HRDocument)).all():
        if HRDocumentService._staged_file_id(document) is not None:
            continue
        summary["candidates"] += 1
        label = {"document_id": str(document.document_id)}
        try:
            content = _read_legacy(document)
        except Exception as exc:  # named per row; the run continues
            summary["failures"].append(
                {**label, "detail": f"{type(exc).__name__}: {exc}"}
            )
            continue
        if content is None:
            summary["failures"].append(
                {**label, "detail": f"legacy object not found: {document.file_path}"}
            )
            continue
        summary["present"] += 1
        if dry_run:
            continue
        try:
            file_path, file_size, content_hash = service.save_document_file(
                document.organization_id,
                document.file_name,
                content,
                content_type=document.content_type,
            )
            document.file_path = file_path
            document.file_size_bytes = file_size
            document.content_hash = content_hash
            db.commit()
            summary["backfilled"] += 1
        except Exception as exc:  # named per row; the run continues
            db.rollback()
            summary["failures"].append(
                {**label, "detail": f"{type(exc).__name__}: {exc}"}
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
