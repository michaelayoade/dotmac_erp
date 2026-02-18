"""
Backfill VAT tax on historical Splynx-synced invoices.

Previously, Splynx invoices were synced with subtotal = total and tax = 0.
This script retroactively extracts the VAT component from total amounts
for invoices where splynx_id IS NOT NULL.

Usage:
    python scripts/backfill_splynx_tax.py --dry-run     # Preview changes
    python scripts/backfill_splynx_tax.py --execute      # Apply changes
    python scripts/backfill_splynx_tax.py --execute --org-id <UUID>  # Single org
"""

from __future__ import annotations

import argparse
import logging
import sys
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

# -- Bootstrap app config so models/DB resolve correctly ----------------------
sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402
from app.models.finance.ar.invoice import Invoice  # noqa: E402
from app.models.finance.ar.invoice_line import InvoiceLine  # noqa: E402
from app.models.finance.ar.invoice_line_tax import InvoiceLineTax  # noqa: E402
from app.models.finance.tax.tax_code import TaxCode, TaxType  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TWO_DP = Decimal("0.01")


# ---------------------------------------------------------------------------
# Tax helpers
# ---------------------------------------------------------------------------


def find_vat_tax_code(db: Session, org_id: UUID) -> TaxCode | None:
    """Find the active VAT sales tax code for an organization."""
    from datetime import date

    today = date.today()
    stmt = (
        select(TaxCode)
        .where(
            TaxCode.organization_id == org_id,
            TaxCode.tax_type == TaxType.VAT,
            TaxCode.applies_to_sales.is_(True),
            TaxCode.is_active.is_(True),
            TaxCode.effective_from <= today,
            or_(TaxCode.effective_to.is_(None), TaxCode.effective_to >= today),
        )
        .order_by(TaxCode.effective_from.desc())
        .limit(1)
    )
    return db.scalar(stmt)


def extract_inclusive_tax(total: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]:
    """Extract subtotal and tax from a VAT-inclusive total.

    Returns (subtotal, tax_amount).
    """
    if rate == ZERO:
        return total, ZERO
    divisor = Decimal("1") + rate
    tax = (total * rate / divisor).quantize(TWO_DP, rounding=ROUND_HALF_UP)
    return total - tax, tax


# ---------------------------------------------------------------------------
# Backfill logic
# ---------------------------------------------------------------------------


def backfill_org(
    db: Session,
    org_id: UUID,
    tax_code: TaxCode,
    *,
    dry_run: bool = True,
) -> dict[str, int]:
    """Backfill tax for all Splynx invoices in one organization."""
    stats: dict[str, int] = {
        "invoices_checked": 0,
        "invoices_updated": 0,
        "lines_updated": 0,
        "line_taxes_created": 0,
        "skipped_already_has_tax": 0,
    }

    rate = tax_code.tax_rate
    is_inclusive = tax_code.is_inclusive

    if not is_inclusive:
        logger.warning(
            "Tax code %s is exclusive — backfill only supports inclusive VAT. Skipping org %s.",
            tax_code.tax_code,
            org_id,
        )
        return stats

    # Find all Splynx-sourced invoices with zero tax
    stmt = (
        select(Invoice)
        .where(
            Invoice.organization_id == org_id,
            Invoice.splynx_id.isnot(None),
            Invoice.tax_amount == ZERO,
            Invoice.total_amount > ZERO,
        )
        .order_by(Invoice.invoice_date)
    )
    invoices = list(db.scalars(stmt).all())
    stats["invoices_checked"] = len(invoices)

    logger.info(
        "Org %s: Found %d Splynx invoices with zero tax to backfill",
        org_id,
        len(invoices),
    )

    for invoice in invoices:
        # Extract tax from invoice total
        subtotal, tax_amount = extract_inclusive_tax(invoice.total_amount, rate)

        if dry_run:
            logger.debug(
                "  [DRY-RUN] Invoice %s: total=%s → subtotal=%s, tax=%s",
                invoice.invoice_number,
                invoice.total_amount,
                subtotal,
                tax_amount,
            )
        else:
            invoice.subtotal = subtotal
            invoice.tax_amount = tax_amount

        stats["invoices_updated"] += 1

        # Update invoice lines
        line_stmt = select(InvoiceLine).where(
            InvoiceLine.invoice_id == invoice.invoice_id
        )
        lines = list(db.scalars(line_stmt).all())

        for line in lines:
            # Skip lines that already have tax
            if line.tax_amount and line.tax_amount != ZERO:
                stats["skipped_already_has_tax"] += 1
                continue

            line_total = line.line_amount + (line.tax_amount or ZERO)
            line_subtotal, line_tax = extract_inclusive_tax(line_total, rate)

            if not dry_run:
                line.line_amount = line_subtotal
                line.tax_amount = line_tax
                line.tax_code_id = tax_code.tax_code_id

            stats["lines_updated"] += 1

            # Check if InvoiceLineTax record already exists
            existing_lt = db.scalar(
                select(func.count(InvoiceLineTax.line_tax_id)).where(
                    InvoiceLineTax.line_id == line.line_id,
                    InvoiceLineTax.tax_code_id == tax_code.tax_code_id,
                )
            )
            if existing_lt and existing_lt > 0:
                continue

            if not dry_run:
                line_tax_record = InvoiceLineTax(
                    line_id=line.line_id,
                    tax_code_id=tax_code.tax_code_id,
                    base_amount=line_subtotal,
                    tax_rate=rate,
                    tax_amount=line_tax,
                    is_inclusive=True,
                    sequence=1,
                )
                db.add(line_tax_record)

            stats["line_taxes_created"] += 1

    if not dry_run:
        db.flush()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill VAT tax on historical Splynx invoices"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview changes only")
    mode.add_argument("--execute", action="store_true", help="Apply changes to DB")
    parser.add_argument(
        "--org-id",
        type=str,
        default=None,
        help="Limit to a single organization UUID",
    )
    args = parser.parse_args()

    dry_run = args.dry_run
    label = "DRY-RUN" if dry_run else "EXECUTE"
    logger.info("=== Splynx Tax Backfill [%s] ===", label)

    with SessionLocal() as db:
        # Find organizations with Splynx invoices
        if args.org_id:
            org_ids = [UUID(args.org_id)]
        else:
            stmt = (
                select(Invoice.organization_id)
                .where(Invoice.splynx_id.isnot(None))
                .distinct()
            )
            org_ids = list(db.scalars(stmt).all())

        logger.info("Processing %d organization(s)", len(org_ids))

        grand_totals: dict[str, int] = {
            "invoices_checked": 0,
            "invoices_updated": 0,
            "lines_updated": 0,
            "line_taxes_created": 0,
            "skipped_already_has_tax": 0,
            "orgs_skipped_no_tax_code": 0,
        }

        for org_id in org_ids:
            tax_code = find_vat_tax_code(db, org_id)
            if not tax_code:
                logger.warning(
                    "Org %s: No active VAT sales tax code — skipping", org_id
                )
                grand_totals["orgs_skipped_no_tax_code"] += 1
                continue

            logger.info(
                "Org %s: Using tax code '%s' (rate=%s, inclusive=%s)",
                org_id,
                tax_code.tax_code,
                tax_code.tax_rate,
                tax_code.is_inclusive,
            )

            org_stats = backfill_org(db, org_id, tax_code, dry_run=dry_run)

            for key, value in org_stats.items():
                grand_totals[key] = grand_totals.get(key, 0) + value

        if not dry_run:
            db.commit()
            logger.info("Changes committed to database.")
        else:
            logger.info("Dry-run complete — no changes made.")

        logger.info("=== Summary ===")
        for key, value in grand_totals.items():
            logger.info("  %s: %d", key, value)


if __name__ == "__main__":
    main()
