#!/usr/bin/env python
"""Backfill ``dotmac-files`` records for pre-cutover form-answer files.

Legacy ``forms.form_answer.file_url`` values point at
``/files/form_attachments/<org>/<filename>`` objects. For each such
answer this script reads the bytes, re-uploads through the tenant
staging seam under the submission's organization, and rewrites
``file_url`` (and the ``file_url`` inside ``value_json`` when present)
to the staged download URL -- one transaction per answer, failures
named, legacy objects left for orphan reconciliation.

Usage:
    python scripts/backfill_form_answer_files.py --dry-run
    python scripts/backfill_form_answer_files.py

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
from app.models.forms.form import DynamicFormAnswer, DynamicFormSubmission
from app.services.careers.web import _stage_form_file
from app.services.storage import get_dotmac_files_provider, get_storage
from app.tenant_catalog import organization_ids

_LEGACY_PREFIX = "/files/form_attachments/"


def _legacy_parts(file_url: str) -> tuple[uuid.UUID, str] | None:
    if not file_url.startswith(_LEGACY_PREFIX) or "/staged/" in file_url:
        return None
    parts = file_url[len(_LEGACY_PREFIX) :].split("/")
    if len(parts) != 2:
        return None
    try:
        return uuid.UUID(parts[0]), parts[1]
    except ValueError:
        return None


def _read_legacy(org_id: uuid.UUID, filename: str) -> bytes | None:
    key = f"form_attachments/{org_id}/{filename}"
    provider = get_dotmac_files_provider()
    if provider.exists(key):
        with provider.open(key) as readable:
            return readable.read()
    storage = get_storage()
    if storage.exists(key):
        chunks, _content_type, _length = storage.stream(key)
        return b"".join(chunks)
    local = Path("/app/uploads/form_attachments") / str(org_id) / filename
    if local.is_file():
        return local.read_bytes()
    return None


def backfill(db: Session, *, dry_run: bool) -> dict:
    summary = {"candidates": 0, "present": 0, "backfilled": 0, "failures": []}
    answers = db.scalars(
        select(DynamicFormAnswer).where(DynamicFormAnswer.file_url.is_not(None))
    ).all()
    for answer in answers:
        parts = _legacy_parts(answer.file_url or "")
        if parts is None:
            continue
        url_org_id, filename = parts
        summary["candidates"] += 1
        try:
            submission = db.get(DynamicFormSubmission, answer.submission_id)
            if submission is None or submission.organization_id != url_org_id:
                summary["failures"].append(
                    {
                        "answer_id": str(answer.answer_id),
                        "detail": "submission organization disagrees with file_url",
                    }
                )
                continue
            content = _read_legacy(url_org_id, filename)
        except Exception as exc:  # named per row; the run continues
            summary["failures"].append(
                {
                    "answer_id": str(answer.answer_id),
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        if content is None:
            summary["failures"].append(
                {
                    "answer_id": str(answer.answer_id),
                    "detail": f"legacy object not found: {answer.file_url}",
                }
            )
            continue
        summary["present"] += 1
        if dry_run:
            continue
        try:
            prepared = _stage_form_file(
                db,
                url_org_id,
                file_data=content,
                content_type=None,
                original_filename=answer.file_name or filename,
            )
            new_url = f"/files/form_attachments/{url_org_id}/staged/{prepared.id}"
            answer.file_url = new_url
            if isinstance(answer.value_json, dict) and "file_url" in answer.value_json:
                answer.value_json = {**answer.value_json, "file_url": new_url}
            db.commit()
            summary["backfilled"] += 1
        except Exception as exc:  # named per row; the run continues
            db.rollback()
            summary["failures"].append(
                {
                    "answer_id": str(answer.answer_id),
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
