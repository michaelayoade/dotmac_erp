#!/usr/bin/env python
"""Backfill ``dotmac-files`` records for pre-cutover SLA policy documents.

Legacy ``HelpArticleOverride.file_path`` values are
``sla_policies/<org>/<article>/<file>`` object keys with no
``stored_files`` row. For each such row this script reads the bytes,
re-uploads through the tenant staging seam, and rewrites ``file_path``
to the canonical tenant storage key -- one transaction per document,
failures named, legacy objects left for orphan reconciliation.

Usage:
    python scripts/backfill_sla_policy_files.py --dry-run
    python scripts/backfill_sla_policy_files.py

``--dry-run`` performs no writes. Exit code 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session_context import session_for_org
from app.models.help.models import HelpArticleOverride
from app.services.sla_policies_admin_web import _stage_sla_document
from app.services.storage import get_dotmac_files_provider
from app.tenant_catalog import organization_ids

MODULE_KEY = "sla_policies"


def backfill(db: Session, *, dry_run: bool) -> dict:
    provider = get_dotmac_files_provider()
    summary = {"candidates": 0, "present": 0, "backfilled": 0, "failures": []}
    rows = db.scalars(
        select(HelpArticleOverride).where(
            HelpArticleOverride.module_key == MODULE_KEY,
            HelpArticleOverride.file_path.is_not(None),
        )
    ).all()
    for policy in rows:
        if (policy.file_path or "").startswith("tenants/"):
            continue
        summary["candidates"] += 1
        key = policy.file_path
        if not provider.exists(key):
            summary["failures"].append(
                {
                    "article_id": str(policy.article_id),
                    "detail": f"object {key} missing",
                }
            )
            continue
        summary["present"] += 1
        if dry_run:
            continue
        try:
            with provider.open(key) as readable:
                content = readable.read()
            prepared = _stage_sla_document(
                db,
                policy.organization_id,
                file_data=content,
                content_type=policy.file_content_type,
                original_filename=policy.file_name,
            )
            policy.file_path = prepared.storage_key
            policy.file_size_bytes = prepared.size_bytes
            policy.content_hash = prepared.checksum_sha256.removeprefix("sha256:")
            db.commit()
            summary["backfilled"] += 1
        except Exception as exc:  # named per document; the run continues
            db.rollback()
            summary["failures"].append(
                {
                    "article_id": str(policy.article_id),
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
