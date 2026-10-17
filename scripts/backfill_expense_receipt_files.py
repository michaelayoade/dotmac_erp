#!/usr/bin/env python
"""Backfill ``dotmac-files`` records for pre-cutover expense receipts.

Legacy receipt entries in ``ExpenseClaimItem.receipt_url`` (single value
or JSON array) point at S3 keys (``expense_receipts/<org>/...``) or
local paths. For each legacy entry this script reads the bytes,
re-uploads through the tenant staging seam, and replaces the entry
with the staged file id -- one transaction per claim item, so failures
leave items untouched and re-running is safe. Remote (http/https)
entries are named and left alone. Legacy objects are not deleted;
orphan reconciliation owns that.

Usage:
    python scripts/backfill_expense_receipt_files.py --database-url postgresql://... --dry-run
    python scripts/backfill_expense_receipt_files.py --database-url postgresql://...

``--dry-run`` performs no writes. Output is a JSON summary; failures
are named per claim item. Exit code 1 when any candidate failed.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimItem
from app.services.expense.receipt_files import receipt_file_id, stage_receipt
from app.services.expense.web_common import ExpenseWebCommonMixin
from app.services.storage import get_dotmac_files_provider


def _read_legacy(entry: str) -> bytes | None:
    if entry.startswith("expense_receipts/"):
        with get_dotmac_files_provider().open(entry) as readable:
            return readable.read()
    path = Path(entry)
    if path.is_file():
        return path.read_bytes()
    return None


def backfill(db: Session, *, dry_run: bool) -> dict:
    summary = {"items": 0, "entries": 0, "backfilled": 0, "failures": []}
    rows = db.execute(
        select(ExpenseClaimItem, ExpenseClaim.organization_id)
        .join(ExpenseClaim, ExpenseClaim.claim_id == ExpenseClaimItem.claim_id)
        .where(ExpenseClaimItem.receipt_url.is_not(None))
    ).all()
    for item, org_id in rows:
        entries = ExpenseWebCommonMixin._parse_receipt_urls(item.receipt_url)
        legacy = [entry for entry in entries if receipt_file_id(entry) is None]
        if not legacy:
            continue
        summary["items"] += 1
        summary["entries"] += len(legacy)
        if dry_run:
            continue
        try:
            rewritten: list[str] = []
            for entry in entries:
                if receipt_file_id(entry) is not None:
                    rewritten.append(entry)
                    continue
                if ExpenseWebCommonMixin._is_remote_receipt(entry):
                    raise ValueError(f"remote receipt entry left for review: {entry}")
                content = _read_legacy(entry)
                if content is None:
                    raise ValueError(f"legacy receipt object not found: {entry}")
                prepared = stage_receipt(
                    db,
                    org_id,
                    file_data=content,
                    content_type=mimetypes.guess_type(entry)[0] or "",
                    original_filename=Path(entry).name,
                )
                rewritten.append(str(prepared.id))
                summary["backfilled"] += 1
            item.receipt_url = (
                rewritten[0] if len(rewritten) == 1 else json.dumps(rewritten)
            )
            db.commit()
        except Exception as exc:  # named per item; the run continues
            db.rollback()
            summary["failures"].append(
                {
                    "item_id": str(item.item_id),
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
