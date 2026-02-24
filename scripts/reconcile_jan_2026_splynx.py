#!/usr/bin/env python3
"""
Reconcile January 2026 bank statements using Splynx payment matching.

Runs the multi-tier matching from SplynxSyncService.reconcile_all_banks():
  - Tier 1: Match by Paystack reference token (where available)
  - Tier 2: Match by unique (date, amount) pairs
  - Tier 3: Match by (customer, date, amount) for ambiguous pairs
  - Tier 4: Bulk matching (multiple payments summing to one bank line)

Usage:
    python scripts/reconcile_jan_2026_splynx.py              # dry-run
    python scripts/reconcile_jan_2026_splynx.py --execute     # commit matches
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.finance.gl.account import Account
from app.services.splynx.sync import SplynxSyncService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("reconcile_jan_2026")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def _resolve_ar_control_account(db: Session) -> UUID | None:
    """Find the AR control account."""
    account_id = db.scalar(
        select(Account.account_id).where(
            Account.organization_id == ORG_ID,
            Account.is_active.is_(True),
            Account.is_posting_allowed.is_(True),
            Account.subledger_type == "AR",
        )
    )
    if account_id:
        return account_id

    return db.scalar(
        select(Account.account_id).where(
            Account.organization_id == ORG_ID,
            Account.account_code == "1400",
            Account.is_active.is_(True),
        )
    )


def _resolve_revenue_account(db: Session) -> UUID | None:
    """Find the default revenue account."""
    return db.scalar(
        select(Account.account_id).where(
            Account.organization_id == ORG_ID,
            Account.account_code == "4000",
            Account.is_active.is_(True),
        )
    )


class DecimalEncoder(json.JSONEncoder):
    def default(self, o: object) -> object:
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, UUID):
            return str(o)
        return super().default(o)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile Jan 2026 via Splynx matching"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit matches (default: dry-run)",
    )
    args = parser.parse_args()
    dry_run = not args.execute

    mode_label = "DRY-RUN" if dry_run else "EXECUTE"
    logger.info("=== Jan 2026 Splynx Reconciliation [%s] ===", mode_label)

    with SessionLocal() as db:
        ar_control = _resolve_ar_control_account(db)
        revenue = _resolve_revenue_account(db)

        if not ar_control:
            logger.error("Cannot resolve AR control account — aborting")
            sys.exit(1)

        logger.info("AR control account: %s", ar_control)
        logger.info("Revenue account: %s", revenue)

        service = SplynxSyncService(
            db=db,
            organization_id=ORG_ID,
            ar_control_account_id=ar_control,
            default_revenue_account_id=revenue,
        )

        # Run reconcile_all_banks with dry_run flag
        results = service.reconcile_all_banks(dry_run=dry_run)

        # Print results
        logger.info("\n" + "=" * 60)
        logger.info("RESULTS:")
        print(json.dumps(results, indent=2, cls=DecimalEncoder, default=str))

        totals = results.get("totals", {})
        logger.info(
            "\nTOTALS: "
            "By reference=%s | By date+amount=%s | By customer=%s | "
            "Bulk=%s | Ambiguous=%s | Total amount=NGN %s",
            totals.get("matched_by_reference", 0),
            totals.get("matched_by_date_amount", 0),
            totals.get("matched_by_customer", 0),
            totals.get("bulk_payments_matched", 0),
            totals.get("ambiguous_matches", 0),
            totals.get("total_matched_amount", 0),
        )

        if dry_run:
            logger.info("DRY-RUN — no changes committed")
            db.rollback()
        else:
            db.commit()
            logger.info("EXECUTE — changes committed")

    logger.info("Done.")


if __name__ == "__main__":
    main()
