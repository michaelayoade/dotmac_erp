#!/usr/bin/env python
"""Backfill ``dotmac-files`` records for pre-cutover resumes.

Legacy ``JobApplicant.resume_url`` values point at
``/files/resumes/<org>/<file_id>.<ext>`` objects. For each such
applicant this script reads the bytes, re-uploads through the tenant
staging seam, and rewrites ``resume_url`` to the staged by-id URL
(``/files/resumes/<staged-id>``) -- one transaction per applicant,
failures named, legacy objects left for orphan reconciliation.

Usage:
    python scripts/backfill_resume_files.py --dry-run
    python scripts/backfill_resume_files.py

``--dry-run`` performs no writes. Exit code 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session_context import session_for_org
from app.models.people.recruit.job_applicant import JobApplicant
from app.services.careers.resume_service import ResumeService, _stage_resume_file
from app.services.storage import get_dotmac_files_provider, get_storage
from app.tenant_catalog import organization_ids


def _legacy_parts(resume_url: str) -> tuple[uuid.UUID, str] | None:
    parts = [p for p in resume_url.split("/") if p]
    # Legacy: files/resumes/<org>/<filename>; staged: files/resumes/<id>
    if len(parts) != 4 or parts[:2] != ["files", "resumes"]:
        return None
    try:
        return uuid.UUID(parts[2]), parts[3]
    except ValueError:
        return None


def _read_legacy(org_id: uuid.UUID, filename: str) -> bytes | None:
    key = f"resumes/{org_id}/{filename}"
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
    config = ResumeService().upload_service.config
    applicants = db.scalars(
        select(JobApplicant).where(JobApplicant.resume_url.is_not(None))
    ).all()
    for applicant in applicants:
        parts = _legacy_parts(applicant.resume_url or "")
        if parts is None:
            continue
        url_org_id, filename = parts
        row_id = str(applicant.applicant_id)
        summary["candidates"] += 1
        if url_org_id != applicant.organization_id:
            summary["failures"].append(
                {
                    "applicant_id": row_id,
                    "detail": "resume_url organization disagrees with applicant",
                }
            )
            continue
        try:
            content = _read_legacy(url_org_id, filename)
        except Exception as exc:  # named per row; the run continues
            summary["failures"].append(
                {"applicant_id": row_id, "detail": f"{type(exc).__name__}: {exc}"}
            )
            continue
        if content is None:
            summary["failures"].append(
                {
                    "applicant_id": row_id,
                    "detail": f"legacy object not found: {applicant.resume_url}",
                }
            )
            continue
        summary["present"] += 1
        if dry_run:
            continue
        try:
            prepared = _stage_resume_file(
                db,
                url_org_id,
                file_data=content,
                original_filename=filename,
                config=config,
            )
            applicant.resume_url = f"/files/resumes/{prepared.id}"
            db.commit()
            summary["backfilled"] += 1
        except Exception as exc:  # named per row; the run continues
            db.rollback()
            summary["failures"].append(
                {"applicant_id": row_id, "detail": f"{type(exc).__name__}: {exc}"}
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
