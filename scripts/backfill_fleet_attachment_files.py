#!/usr/bin/env python
"""Backfill ``dotmac-files`` records for pre-cutover fleet files.

Legacy fleet attachments are ``common.attachment`` rows with entity
types ``FLEET_FUEL_LOG``, ``FLEET_INCIDENT`` and ``FLEET_MAINTENANCE``;
legacy vehicle documents are ``fleet.vehicle_documents`` rows. Both
hold relative paths served from ``attachments/<file_path>`` object
keys. For each such row this script reads the bytes, re-uploads
through the tenant staging seam under the family's own upload policy,
and rewrites ``file_path`` to the canonical tenant storage key -- one
transaction per row, failures named, legacy objects left for orphan
reconciliation.

Usage:
    python scripts/backfill_fleet_attachment_files.py --dry-run
    python scripts/backfill_fleet_attachment_files.py

``--dry-run`` performs no writes. Exit code 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session_context import session_for_org
from app.models.finance.common.attachment import Attachment
from app.models.fleet.vehicle_document import VehicleDocument
from app.services.file_upload import (
    get_finance_attachment_upload,
    get_fleet_fuel_receipt_upload,
    get_fleet_incident_attachment_upload,
    get_fleet_maintenance_attachment_upload,
)
from app.services.finance.common.attachment import (
    _stage_attachment_file,
    staged_file_id,
)
from app.services.fleet.document_service import vehicle_document_staged_file_id
from app.services.storage import get_dotmac_files_provider, get_storage
from app.tenant_catalog import organization_ids

_CONFIG_BY_ENTITY_TYPE = {
    "FLEET_FUEL_LOG": get_fleet_fuel_receipt_upload,
    "FLEET_INCIDENT": get_fleet_incident_attachment_upload,
    "FLEET_MAINTENANCE": get_fleet_maintenance_attachment_upload,
}


def _read_legacy(file_path: str | None) -> bytes | None:
    value = file_path or ""
    key = value if value.startswith("attachments/") else f"attachments/{value}"
    provider = get_dotmac_files_provider()
    if provider.exists(key):
        with provider.open(key) as readable:
            return readable.read()
    storage = get_storage()
    if storage.exists(key):
        chunks, _content_type, _length = storage.stream(key)
        return b"".join(chunks)
    local = Path(value)
    if local.is_file():
        return local.read_bytes()
    return None


def _backfill_attachment(db: Session, attachment: Attachment, summary: dict) -> None:
    summary["candidates"] += 1
    label = {"attachment_id": str(attachment.attachment_id)}
    try:
        content = _read_legacy(attachment.file_path)
    except Exception as exc:  # named per row; the run continues
        summary["failures"].append({**label, "detail": f"{type(exc).__name__}: {exc}"})
        return
    if content is None:
        summary["failures"].append(
            {**label, "detail": f"legacy object not found: {attachment.file_path}"}
        )
        return
    summary["present"] += 1
    if summary["dry_run"]:
        return
    try:
        prepared = _stage_attachment_file(
            db,
            attachment.organization_id,
            file_data=content,
            content_type=attachment.content_type,
            original_filename=attachment.file_name,
            config=_CONFIG_BY_ENTITY_TYPE[attachment.entity_type]().config,
        )
        attachment.file_path = prepared.storage_key
        attachment.file_size = prepared.size_bytes
        attachment.checksum = prepared.checksum_sha256.removeprefix("sha256:")
        db.commit()
        summary["backfilled"] += 1
    except Exception as exc:  # named per row; the run continues
        db.rollback()
        summary["failures"].append({**label, "detail": f"{type(exc).__name__}: {exc}"})


def _backfill_document(db: Session, document: VehicleDocument, summary: dict) -> None:
    summary["candidates"] += 1
    label = {"document_id": str(document.document_id)}
    try:
        content = _read_legacy(document.file_path)
    except Exception as exc:  # named per row; the run continues
        summary["failures"].append({**label, "detail": f"{type(exc).__name__}: {exc}"})
        return
    if content is None:
        summary["failures"].append(
            {**label, "detail": f"legacy object not found: {document.file_path}"}
        )
        return
    summary["present"] += 1
    if summary["dry_run"]:
        return
    try:
        prepared = _stage_attachment_file(
            db,
            document.organization_id,
            file_data=content,
            content_type=mimetypes.guess_type(document.file_name or "")[0] or "",
            original_filename=document.file_name or "document",
            config=get_finance_attachment_upload().config,
        )
        document.file_path = prepared.storage_key
        db.commit()
        summary["backfilled"] += 1
    except Exception as exc:  # named per row; the run continues
        db.rollback()
        summary["failures"].append({**label, "detail": f"{type(exc).__name__}: {exc}"})


def backfill(db: Session, *, dry_run: bool) -> dict:
    summary = {
        "candidates": 0,
        "present": 0,
        "backfilled": 0,
        "failures": [],
        "dry_run": dry_run,
    }
    for attachment in db.scalars(select(Attachment)).all():
        if attachment.entity_type not in _CONFIG_BY_ENTITY_TYPE:
            continue
        if staged_file_id(attachment) is not None:
            continue
        _backfill_attachment(db, attachment, summary)
    for document in db.scalars(select(VehicleDocument)).all():
        if not document.file_path:
            continue
        if vehicle_document_staged_file_id(document.file_path) is not None:
            continue
        _backfill_document(db, document, summary)
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
