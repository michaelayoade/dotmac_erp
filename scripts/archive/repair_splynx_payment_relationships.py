#!/usr/bin/env python
"""
Repair Splynx payment->invoice relationships in AR allocations.

Usage:
  python scripts/repair_splynx_payment_relationships.py --org-id UUID --ar-account UUID --dry-run
  python scripts/repair_splynx_payment_relationships.py --org-id UUID --ar-account UUID --execute
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.services.splynx import SplynxConfig, SplynxSyncService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def run_repair(
    organization_id: UUID,
    ar_control_account_id: UUID,
    *,
    date_from: date | None,
    date_to: date | None,
    batch_size: int | None,
    execute: bool,
) -> dict:
    with SessionLocal() as db:
        service = SplynxSyncService(
            db=db,
            organization_id=organization_id,
            ar_control_account_id=ar_control_account_id,
            config=SplynxConfig.from_settings(),
        )
        try:
            summary = service.repair_payment_invoice_relationships(
                date_from=date_from,
                date_to=date_to,
                batch_size=batch_size,
            )
            if execute:
                db.commit()
                logger.info("Committed relationship repairs.")
            else:
                db.rollback()
                logger.info("Dry run complete; rolled back all changes.")
            return summary
        except Exception:
            db.rollback()
            raise
        finally:
            service.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repair Splynx payment->invoice allocation relationships"
    )
    parser.add_argument("--org-id", required=True, help="Organization UUID")
    parser.add_argument("--ar-account", required=True, help="AR Control Account UUID")
    parser.add_argument("--from-date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to-date", help="End date (YYYY-MM-DD)")
    parser.add_argument("--batch-size", type=int, help="Maximum payments to process")
    parser.add_argument(
        "--output-json",
        action="store_true",
        help="Print summary as JSON only",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Run repair but rollback changes",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Run repair and commit changes",
    )

    args = parser.parse_args()
    summary = run_repair(
        organization_id=UUID(args.org_id),
        ar_control_account_id=UUID(args.ar_account),
        date_from=_parse_date(args.from_date),
        date_to=_parse_date(args.to_date),
        batch_size=args.batch_size,
        execute=args.execute,
    )

    if args.output_json:
        print(json.dumps(summary, default=str))
        return

    logger.info("=" * 60)
    logger.info("SPLYNX RELATIONSHIP REPAIR SUMMARY")
    logger.info("=" * 60)
    for key in (
        "processed",
        "fixed",
        "already_correct",
        "created_allocations",
        "relinked_allocations",
        "updated_amounts",
        "no_invoice_link",
        "missing_local_payment",
        "missing_local_invoice",
        "customer_mismatch",
        "overallocated_invoices",
    ):
        logger.info("  %-24s %s", key + ":", summary.get(key))
    errors = summary.get("errors") or []
    logger.info("  %-24s %s", "errors:", len(errors))
    if errors:
        for err in errors[:20]:
            logger.warning("    %s", err)
        if len(errors) > 20:
            logger.warning("    ... and %d more errors", len(errors) - 20)


if __name__ == "__main__":
    main()
