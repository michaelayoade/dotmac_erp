#!/usr/bin/env python
"""Backfill ``dotmac-files`` records for pre-cutover employee documents.

Covers both legacy path columns: ``hr.employee_document.file_path`` and
``hr.info_change_request.pending_document_path`` (evidence awaiting HR
review). Legacy values are relative paths served from
``employee_documents/<path>`` object keys (or a local upload tree).
For each row this script reads the bytes, re-uploads through the
tenant staging seam, and rewrites the path to the canonical tenant
storage key -- one transaction per row, failures named, legacy objects
left for orphan reconciliation.

Usage:
    python scripts/backfill_employee_document_files.py --dry-run
    python scripts/backfill_employee_document_files.py

``--dry-run`` performs no writes. Exit code 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session_context import session_for_org
from app.models.people.hr.employee_extended import EmployeeDocument
from app.models.people.hr.info_change_request import EmployeeInfoChangeRequest
from app.services.people.hr.employee_extended import (
    _stage_employee_file,
    staged_file_id_for_path,
)
from app.services.storage import get_dotmac_files_provider, get_storage
from app.tenant_catalog import organization_ids


def _read_legacy(path_value: str) -> bytes | None:
    if Path(path_value).is_absolute():
        local = Path(path_value)
        return local.read_bytes() if local.is_file() else None
    key = (
        path_value
        if path_value.startswith("employee_documents/")
        else f"employee_documents/{path_value}"
    )
    provider = get_dotmac_files_provider()
    if provider.exists(key):
        with provider.open(key) as readable:
            return readable.read()
    storage = get_storage()
    if storage.exists(key):
        chunks, _content_type, _length = storage.stream(key)
        return b"".join(chunks)
    local = Path("/app/uploads/employee_documents") / path_value
    if local.is_file():
        return local.read_bytes()
    return None


def _backfill_one(
    db: Session,
    summary: dict,
    *,
    row_id: uuid.UUID,
    organization_id: uuid.UUID,
    path_value: str,
    filename: str | None,
    content_type: str | None,
    apply,
    dry_run: bool,
) -> None:
    summary["candidates"] += 1
    try:
        content = _read_legacy(path_value)
    except Exception as exc:  # named per row; the run continues
        summary["failures"].append(
            {"row_id": str(row_id), "detail": f"{type(exc).__name__}: {exc}"}
        )
        return
    if content is None:
        summary["failures"].append(
            {"row_id": str(row_id), "detail": f"legacy object not found: {path_value}"}
        )
        return
    summary["present"] += 1
    if dry_run:
        return
    try:
        prepared = _stage_employee_file(
            db,
            organization_id,
            file_data=content,
            content_type=content_type,
            original_filename=filename,
        )
        apply(prepared)
        db.commit()
        summary["backfilled"] += 1
    except Exception as exc:  # named per row; the run continues
        db.rollback()
        summary["failures"].append(
            {"row_id": str(row_id), "detail": f"{type(exc).__name__}: {exc}"}
        )


def backfill(db: Session, *, dry_run: bool) -> dict:
    summary = {"candidates": 0, "present": 0, "backfilled": 0, "failures": []}

    for document in db.scalars(select(EmployeeDocument)).all():
        if staged_file_id_for_path(document.file_path) is not None:
            continue

        def _apply_document(prepared, document=document):
            document.file_path = prepared.storage_key
            document.file_size = prepared.size_bytes
            document.content_checksum = prepared.checksum_sha256.removeprefix("sha256:")

        _backfill_one(
            db,
            summary,
            row_id=document.document_id,
            organization_id=document.organization_id,
            path_value=document.file_path,
            filename=document.file_name,
            content_type=document.mime_type,
            apply=_apply_document,
            dry_run=dry_run,
        )

    pending = db.scalars(
        select(EmployeeInfoChangeRequest).where(
            EmployeeInfoChangeRequest.pending_document_path.is_not(None)
        )
    ).all()
    for request in pending:
        if staged_file_id_for_path(request.pending_document_path) is not None:
            continue

        def _apply_pending(prepared, request=request):
            request.pending_document_path = prepared.storage_key
            request.pending_document_size = prepared.size_bytes
            request.pending_document_checksum = prepared.checksum_sha256.removeprefix(
                "sha256:"
            )

        _backfill_one(
            db,
            summary,
            row_id=request.request_id,
            organization_id=request.organization_id,
            path_value=request.pending_document_path or "",
            filename=request.pending_document_name,
            content_type=request.pending_document_mime_type,
            apply=_apply_pending,
            dry_run=dry_run,
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
