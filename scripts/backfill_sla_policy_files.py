#!/usr/bin/env python
"""Backfill ``dotmac-files`` records for pre-cutover SLA policy documents.

Legacy ``HelpArticleOverride.file_path`` values are
``sla_policies/<org>/<article>/<file>`` object keys with no
``stored_files`` row. For each such row this script reads the bytes,
re-uploads through the tenant staging seam, and rewrites ``file_path``
to the canonical tenant storage key -- one transaction per document,
failures named, legacy objects left for orphan reconciliation.

Usage:
    python scripts/backfill_sla_policy_files.py --database-url postgresql://... --dry-run
    python scripts/backfill_sla_policy_files.py --database-url postgresql://...

``--dry-run`` performs no writes. Exit code 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.help.models import HelpArticleOverride
from app.services.sla_policies_admin_web import _stage_sla_document
from app.services.storage import get_dotmac_files_provider

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
