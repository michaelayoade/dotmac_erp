#!/usr/bin/env python
"""
Post AP supplier invoices that are missing GL journal entries.

Finds supplier invoices in APPROVED/POSTED/PAID/PARTIALLY_PAID status with
journal_entry_id IS NULL and calls the AP posting adapter.

Closes the ₦96.3M AP subledger/GL gap.

Idempotent: invoices with existing journal_entry_id are skipped.

Usage:
  # Dry run (default)
  docker exec dotmac_erp_app python scripts/post_unposted_ap_invoices.py

  # Execute
  docker exec dotmac_erp_app python scripts/post_unposted_ap_invoices.py --commit
"""

from __future__ import annotations

import argparse
import logging
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text

from app.db import SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")


def run(*, commit: bool = False) -> dict[str, int]:
    """Post unposted AP invoices."""
    results: dict[str, int] = {
        "invoices_found": 0,
        "invoices_posted": 0,
        "invoices_skipped": 0,
        "errors": 0,
        "total_amount": 0,
    }

    with SessionLocal() as db:
        db.execute(
            text("SET app.current_organization_id = :org_id"),
            {"org_id": str(ORG_ID)},
        )

        from app.models.finance.ap.supplier_invoice import (
            SupplierInvoice,
            SupplierInvoiceStatus,
        )

        postable_statuses = [
            SupplierInvoiceStatus.APPROVED,
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PAID,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]

        stmt = (
            select(SupplierInvoice)
            .where(
                SupplierInvoice.organization_id == ORG_ID,
                SupplierInvoice.status.in_(postable_statuses),
                SupplierInvoice.journal_entry_id.is_(None),
                SupplierInvoice.total_amount > Decimal("0"),
            )
            .order_by(SupplierInvoice.invoice_date)
        )
        invoices = list(db.scalars(stmt).all())
        results["invoices_found"] = len(invoices)

        total_amount = Decimal("0")
        for inv in invoices:
            total_amount += inv.total_amount or Decimal("0")
            logger.info(
                "  %s | %s | %s | ₦%s",
                inv.invoice_number,
                inv.invoice_date,
                inv.status.value,
                f"{inv.total_amount:,.2f}",
            )

        results["total_amount"] = int(total_amount)
        logger.info(
            "Found %d unposted AP invoices totalling ₦%s",
            len(invoices),
            f"{total_amount:,.2f}",
        )

        if not invoices:
            return results

        if not commit:
            logger.info("DRY RUN — run with --commit to post these invoices.")
            return results

        from app.services.finance.ap.ap_posting_adapter import APPostingAdapter

        for inv in invoices:
            try:
                user_id = inv.created_by_user_id or SYSTEM_USER_ID
                result = APPostingAdapter.post_invoice(
                    db=db,
                    organization_id=ORG_ID,
                    invoice_id=inv.invoice_id,
                    posting_date=inv.invoice_date,
                    posted_by_user_id=user_id,
                    idempotency_key=f"backfill-ap-{inv.invoice_id}",
                )
                if result.success and result.journal_entry_id:
                    inv.journal_entry_id = result.journal_entry_id
                    results["invoices_posted"] += 1
                    logger.info(
                        "Posted %s → journal %s",
                        inv.invoice_number,
                        result.journal_entry_id,
                    )
                elif result.success:
                    results["invoices_skipped"] += 1
                    logger.info("Skipped %s: %s", inv.invoice_number, result.message)
                else:
                    results["errors"] += 1
                    logger.warning("Failed %s: %s", inv.invoice_number, result.message)
            except Exception as exc:
                results["errors"] += 1
                logger.exception("Error posting %s: %s", inv.invoice_number, exc)

        if results["invoices_posted"] > 0:
            db.commit()
            logger.info("Committed %d GL postings", results["invoices_posted"])

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post unposted AP supplier invoices to GL"
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually post to GL (default: dry run)",
    )
    args = parser.parse_args()

    results = run(commit=args.commit)
    logger.info("Results: %s", results)


if __name__ == "__main__":
    main()
