"""
Fix double-VAT on Splynx invoices where service price was VAT-inclusive.

When Splynx was switched to VAT-exclusive mode, service creation/upgrade pages
displayed VAT-inclusive amounts. These baked-in prices were then synced and
taxed again, resulting in double VAT.

Affected lines have unit_price equal to a known VAT-inclusive amount
(base_price × 1.075). This script:
  1. Corrects unit_price to the true base price
  2. Fixes line_amount where it equals the inflated price (old sync format)
  3. Recalculates invoice header totals from corrected lines

Two patterns:
  - Old format (line_tax=0): unit_price & line_amount both inflated, tax at header
  - New format (line_tax>0): unit_price inflated but line_amount already extracted;
    only unit_price and invoice header need correction

Usage:
    python scripts/fix_splynx_double_vat.py --dry-run     # Preview changes
    python scripts/fix_splynx_double_vat.py --execute      # Apply changes
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select

# -- Bootstrap app config so models/DB resolve correctly ----------------------
sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402
from app.models.finance.ar.invoice import Invoice  # noqa: E402
from app.models.finance.ar.invoice_line import InvoiceLine  # noqa: E402
from app.models.finance.ar.invoice_line_tax import InvoiceLineTax  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TWO_DP = Decimal("0.01")
VAT_RATE = Decimal("0.075")

# Known VAT-inclusive prices → correct base prices
# Calculated as: base × 1.075 = inclusive
VAT_INCLUSIVE_TO_BASE: dict[Decimal, Decimal] = {
    Decimal("18812.500000"): Decimal("17500"),  # Unlimited Basic
    Decimal("37625.000000"): Decimal("35000"),  # Unlimited Compact
    Decimal("38700.000000"): Decimal("36000"),  # Unlimited Compact (old rate 36000)
    Decimal("56437.500000"): Decimal("52500"),  # Unlimited Lite
    Decimal("75250.000000"): Decimal("70000"),  # Unlimited Elite
    Decimal("77400.000000"): Decimal("72000"),  # Unlimited Elite (old rate 72000)
    Decimal("112875.000000"): Decimal("105000"),  # Unlimited Premium
}


def get_base_price(unit_price: Decimal) -> Decimal | None:
    """Look up the correct base price for a known VAT-inclusive unit price."""
    for inclusive, base in VAT_INCLUSIVE_TO_BASE.items():
        if unit_price == inclusive:
            return base
    return None


def fix_invoices(*, dry_run: bool = True) -> dict[str, int]:
    """Find and fix all double-VAT Splynx invoices."""
    stats: dict[str, int] = {
        "invoices_checked": 0,
        "invoices_fixed": 0,
        "lines_fixed": 0,
        "lines_amount_corrected": 0,
        "line_taxes_updated": 0,
        "total_overcharge": 0,
    }

    report_rows: list[dict[str, str]] = []

    with SessionLocal() as db:
        # Find all invoice lines with VAT-inclusive unit prices on Splynx invoices
        inclusive_prices = list(VAT_INCLUSIVE_TO_BASE.keys())

        stmt = (
            select(InvoiceLine)
            .join(Invoice, Invoice.invoice_id == InvoiceLine.invoice_id)
            .where(
                Invoice.splynx_id.isnot(None),
                InvoiceLine.unit_price.in_(inclusive_prices),
            )
        )
        affected_lines = list(db.scalars(stmt).all())

        if not affected_lines:
            logger.info("No double-VAT lines found. Nothing to fix.")
            return stats

        # Group by invoice_id
        invoice_ids: set[UUID] = {line.invoice_id for line in affected_lines}
        stats["invoices_checked"] = len(invoice_ids)

        logger.info(
            "Found %d affected lines across %d invoices",
            len(affected_lines),
            len(invoice_ids),
        )

        # Build lookup: line_id → correct base price
        line_fixes: dict[UUID, Decimal] = {}
        for line in affected_lines:
            base = get_base_price(line.unit_price)
            if base is not None:
                line_fixes[line.line_id] = base

        # Process each affected invoice
        for invoice_id in sorted(invoice_ids):
            invoice = db.get(Invoice, invoice_id)
            if invoice is None:
                continue

            old_subtotal = invoice.subtotal
            old_tax = invoice.tax_amount
            old_total = invoice.total_amount

            # Get ALL lines for this invoice (not just affected ones)
            all_lines_stmt = (
                select(InvoiceLine)
                .where(InvoiceLine.invoice_id == invoice_id)
                .order_by(InvoiceLine.line_number)
            )
            all_lines = list(db.scalars(all_lines_stmt).all())

            # Fix affected lines
            for line in all_lines:
                if line.line_id not in line_fixes:
                    continue

                base_price = line_fixes[line.line_id]
                old_unit_price = line.unit_price

                logger.info(
                    "  %s line %s: unit_price %s → %s",
                    invoice.invoice_number,
                    line.line_number,
                    old_unit_price,
                    base_price,
                )

                if not dry_run:
                    line.unit_price = base_price

                stats["lines_fixed"] += 1

                # Old format: line_amount == inflated unit_price (no per-line tax)
                if line.line_amount == old_unit_price:
                    new_line_amount = (base_price * line.quantity).quantize(
                        TWO_DP, rounding=ROUND_HALF_UP
                    )
                    logger.info(
                        "    line_amount %s → %s (old format, was equal to unit_price)",
                        line.line_amount,
                        new_line_amount,
                    )
                    if not dry_run:
                        line.line_amount = new_line_amount
                    stats["lines_amount_corrected"] += 1

                # New format: line_amount already extracted correctly, check
                elif line.tax_amount and line.tax_amount > ZERO:
                    expected_line_amount = (base_price * line.quantity).quantize(
                        TWO_DP, rounding=ROUND_HALF_UP
                    )
                    if line.line_amount != expected_line_amount:
                        logger.warning(
                            "    line_amount %s != expected %s — leaving as-is",
                            line.line_amount,
                            expected_line_amount,
                        )

                # Update InvoiceLineTax if it exists for this line
                lt_stmt = select(InvoiceLineTax).where(
                    InvoiceLineTax.line_id == line.line_id
                )
                line_taxes = list(db.scalars(lt_stmt).all())
                for lt in line_taxes:
                    # base_amount should match the corrected line_amount
                    if lt.base_amount != base_price:
                        logger.info(
                            "    InvoiceLineTax base_amount %s → %s",
                            lt.base_amount,
                            base_price,
                        )
                        if not dry_run:
                            lt.base_amount = base_price
                            lt.tax_amount = (base_price * lt.tax_rate).quantize(
                                TWO_DP, rounding=ROUND_HALF_UP
                            )
                        stats["line_taxes_updated"] += 1

            # Recalculate invoice header from ALL lines
            new_subtotal = ZERO
            new_tax = ZERO
            has_per_line_tax = False

            for line in all_lines:
                new_subtotal += (
                    line.line_amount
                    if dry_run is False
                    else (
                        line_fixes.get(line.line_id, line.unit_price)
                        if line.line_amount == line.unit_price
                        and line.line_id in line_fixes
                        else line.line_amount
                    )
                )
                if line.tax_amount and line.tax_amount > ZERO:
                    has_per_line_tax = True
                    new_tax += line.tax_amount

            # For old-format invoices (no per-line tax), compute header tax
            if not has_per_line_tax:
                new_tax = (new_subtotal * VAT_RATE).quantize(
                    TWO_DP, rounding=ROUND_HALF_UP
                )

            new_total = new_subtotal + new_tax
            overcharge = old_total - new_total

            logger.info(
                "  %s: subtotal %s→%s, tax %s→%s, total %s→%s (overcharge: %s)",
                invoice.invoice_number,
                old_subtotal,
                new_subtotal,
                old_tax,
                new_tax,
                old_total,
                new_total,
                overcharge,
            )

            if not dry_run:
                invoice.subtotal = new_subtotal
                invoice.tax_amount = new_tax
                invoice.total_amount = new_total

            stats["invoices_fixed"] += 1
            stats["total_overcharge"] += int(overcharge)

            report_rows.append(
                {
                    "invoice_number": str(invoice.invoice_number),
                    "invoice_date": str(invoice.invoice_date),
                    "status": str(
                        invoice.status.value
                        if hasattr(invoice.status, "value")
                        else invoice.status
                    ),
                    "old_subtotal": str(old_subtotal),
                    "old_tax": str(old_tax),
                    "old_total": str(old_total),
                    "new_subtotal": str(new_subtotal),
                    "new_tax": str(new_tax),
                    "new_total": str(new_total),
                    "overcharge": str(overcharge),
                    "amount_paid": str(invoice.amount_paid),
                }
            )

        if not dry_run:
            db.flush()
            db.commit()
            logger.info("Changes committed to database.")
        else:
            logger.info("Dry-run complete — no changes made.")

        # Write CSV report
        label = "dry_run" if dry_run else "executed"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = f"splynx_double_vat_fix_{label}_{timestamp}.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "invoice_number",
                    "invoice_date",
                    "status",
                    "old_subtotal",
                    "old_tax",
                    "old_total",
                    "new_subtotal",
                    "new_tax",
                    "new_total",
                    "overcharge",
                    "amount_paid",
                ],
            )
            writer.writeheader()
            writer.writerows(report_rows)
        logger.info("Report written to %s", csv_path)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix double-VAT on Splynx invoices with VAT-inclusive service prices"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview changes only")
    mode.add_argument("--execute", action="store_true", help="Apply changes to DB")
    args = parser.parse_args()

    dry_run = args.dry_run
    label = "DRY-RUN" if dry_run else "EXECUTE"
    logger.info("=== Splynx Double-VAT Fix [%s] ===", label)

    stats = fix_invoices(dry_run=dry_run)

    logger.info("=== Summary ===")
    for key, value in stats.items():
        logger.info("  %s: %d", key, value)


if __name__ == "__main__":
    main()
