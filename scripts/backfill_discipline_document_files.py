#!/usr/bin/env python
"""Backfill ``dotmac-files`` records for pre-cutover discipline documents.

Legacy ``people.case_document.file_path`` values are relative paths
served from ``discipline/<file_path>`` object keys (or a local upload
tree). For each such row this script reads the bytes, re-uploads
through the tenant staging seam under the case's organization, and
rewrites ``file_path`` to the canonical tenant storage key -- one
transaction per document, failures named, legacy objects left for
orphan reconciliation.

Usage:
    python scripts/backfill_discipline_document_files.py --dry-run
    python scripts/backfill_discipline_document_files.py

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
from app.models.people.discipline import CaseDocument, DisciplinaryCase
from app.services.people.discipline.attachment_service import (
    _stage_discipline_file,
    staged_file_id,
)
from app.services.storage import get_dotmac_files_provider, get_storage
from app.tenant_catalog import organization_ids


def _read_legacy(document: CaseDocument) -> bytes | None:
    file_path = document.file_path or ""
    if Path(file_path).is_absolute():
        local = Path(file_path)
        return local.read_bytes() if local.is_file() else None
    key = (
        file_path if file_path.startswith("discipline/") else f"discipline/{file_path}"
    )
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
    documents = db.scalars(select(CaseDocument)).all()
    for document in documents:
        if staged_file_id(document) is not None:
            continue
        summary["candidates"] += 1
        row_id = str(document.document_id)
        try:
            case = db.get(DisciplinaryCase, document.case_id)
            if case is None:
                summary["failures"].append(
                    {
                        "document_id": row_id,
                        "detail": f"case not found: {document.case_id}",
                    }
                )
                continue
            content = _read_legacy(document)
        except Exception as exc:  # named per row; the run continues
            summary["failures"].append(
                {"document_id": row_id, "detail": f"{type(exc).__name__}: {exc}"}
            )
            continue
        if content is None:
            summary["failures"].append(
                {
                    "document_id": row_id,
                    "detail": f"legacy object not found: {document.file_path}",
                }
            )
            continue
        summary["present"] += 1
        if dry_run:
            continue
        try:
            prepared = _stage_discipline_file(
                db,
                case.organization_id,
                file_data=content,
                content_type=document.mime_type,
                original_filename=document.file_name,
            )
            document.file_path = prepared.storage_key
            document.file_size = prepared.size_bytes
            db.commit()
            summary["backfilled"] += 1
        except Exception as exc:  # named per row; the run continues
            db.rollback()
            summary["failures"].append(
                {"document_id": row_id, "detail": f"{type(exc).__name__}: {exc}"}
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
