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
    python scripts/backfill_hr_invite_attachment_files.py --dry-run
    python scripts/backfill_hr_invite_attachment_files.py

``--dry-run`` performs no writes. Exit code 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session_context import session_for_org
from app.models.domain_settings import DomainSetting, SettingDomain
from app.services.people.hr.invite_attachment import (
    DEFAULT_INVITE_ATTACHMENT_KEY,
    stage_default_invite_attachment,
    staged_file_id,
)
from app.services.storage import get_dotmac_files_provider, get_storage
from app.tenant_catalog import organization_ids


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
