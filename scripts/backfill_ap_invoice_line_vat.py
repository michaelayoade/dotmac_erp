"""
Backfill AP invoice line VAT metadata for existing invoices.

Safe automatic fixes:
1. Single-line AP invoices where header tax exists but line tax is blank:
   - Set line tax amount = invoice tax amount
   - Set line tax code = active purchase VAT code
   - Create ap.supplier_invoice_line_tax row
2. Lines with tax amount but missing tax code:
   - Set line tax code = active purchase VAT code
   - Create line-tax rows when absent

All other mismatches are reported for manual review.

Usage:
    python scripts/backfill_ap_invoice_line_vat.py --dry-run
    python scripts/backfill_ap_invoice_line_vat.py --execute
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid as uuid_lib
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402
from app.models.finance.ap.supplier_invoice import SupplierInvoice  # noqa: E402
from app.models.finance.ap.supplier_invoice_line import (
    SupplierInvoiceLine,  # noqa: E402
)
from app.models.finance.ap.supplier_invoice_line_tax import (  # noqa: E402
    SupplierInvoiceLineTax,
)
from app.models.finance.tax.tax_code import TaxCode, TaxType  # noqa: E402

logger = logging.getLogger("backfill_ap_invoice_vat")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)

QTY = Decimal("0.000001")


@dataclass
class FixStats:
    invoices_scanned: int = 0
    lines_scanned: int = 0
    single_line_blank_candidates: int = 0
    line_code_only_candidates: int = 0
    line_tax_rows_to_create: int = 0
    unresolved_multi_line: int = 0
    unresolved_other_mismatch: int = 0
    lines_updated: int = 0
    line_tax_rows_created: int = 0


def _sum_tax(lines: Iterable[SupplierInvoiceLine]) -> Decimal:
    return sum((line.tax_amount or Decimal("0")) for line in lines)


def _get_active_purchase_vat_code(db, org_id: UUID) -> TaxCode:
    vat_codes = list(
        db.scalars(
            select(TaxCode).where(
                TaxCode.organization_id == org_id,
                TaxCode.tax_type.in_([TaxType.VAT, TaxType.GST]),
                TaxCode.is_active.is_(True),
                TaxCode.applies_to_purchases.is_(True),
            )
        ).all()
    )
    if not vat_codes:
        raise RuntimeError("No active purchase VAT/GST tax code found for organization")
    if len(vat_codes) > 1:
        codes = ", ".join(tc.tax_code for tc in vat_codes)
        raise RuntimeError(
            f"Multiple active purchase VAT/GST tax codes found ({codes}). "
            "Auto-backfill requires exactly one."
        )
    return vat_codes[0]


def _ensure_line_tax_row(
    db,
    line: SupplierInvoiceLine,
    tax_code: TaxCode,
    tax_amount: Decimal,
) -> bool:
    if line.line_taxes:
        return False

    recoverable_amount = Decimal("0")
    if tax_code.is_recoverable:
        recoverable_amount = (tax_amount * tax_code.recovery_rate).quantize(
            QTY, rounding=ROUND_HALF_UP
        )

    db.add(
        SupplierInvoiceLineTax(
            line_tax_id=uuid_lib.uuid4(),
            line_id=line.line_id,
            tax_code_id=tax_code.tax_code_id,
            base_amount=(line.line_amount or Decimal("0")).quantize(
                QTY, rounding=ROUND_HALF_UP
            ),
            tax_rate=tax_code.tax_rate,
            tax_amount=tax_amount.quantize(QTY, rounding=ROUND_HALF_UP),
            is_inclusive=bool(tax_code.is_inclusive),
            sequence=1,
            is_recoverable=bool(tax_code.is_recoverable),
            recoverable_amount=recoverable_amount,
        )
    )
    return True


def run(org_id: UUID, execute: bool) -> FixStats:
    stats = FixStats()
    with SessionLocal() as db:
        vat_code = _get_active_purchase_vat_code(db, org_id)
        logger.info(
            "Using VAT code: %s (%s @ %s)",
            vat_code.tax_code,
            vat_code.tax_name,
            vat_code.tax_rate,
        )

        invoices = list(
            db.scalars(
                select(SupplierInvoice)
                .where(
                    SupplierInvoice.organization_id == org_id,
                    SupplierInvoice.tax_amount != Decimal("0"),
                )
                .options(
                    selectinload(SupplierInvoice.lines).selectinload(
                        SupplierInvoiceLine.line_taxes
                    )
                )
                .order_by(SupplierInvoice.invoice_date, SupplierInvoice.invoice_number)
            ).all()
        )
        stats.invoices_scanned = len(invoices)

        for inv in invoices:
            lines = sorted(inv.lines, key=lambda l: l.line_number)
            if not lines:
                continue

            stats.lines_scanned += len(lines)
            header_tax = inv.tax_amount or Decimal("0")
            line_tax_sum = _sum_tax(lines)
            abs_diff = abs((header_tax - line_tax_sum).quantize(QTY))

            # Deterministic fix 1: single-line invoice, header tax but blank line tax.
            if (
                len(lines) == 1
                and header_tax != 0
                and lines[0].tax_amount == 0
                and lines[0].tax_code_id is None
            ):
                stats.single_line_blank_candidates += 1
                if execute:
                    line = lines[0]
                    line.tax_amount = header_tax.quantize(QTY, rounding=ROUND_HALF_UP)
                    line.tax_code_id = vat_code.tax_code_id
                    stats.lines_updated += 1
                    if _ensure_line_tax_row(db, line, vat_code, line.tax_amount):
                        stats.line_tax_rows_created += 1
                else:
                    stats.line_tax_rows_to_create += 1
                continue

            # Deterministic fix 2: tax on line but missing tax code.
            for line in lines:
                if (line.tax_amount or Decimal("0")) == 0:
                    continue
                if line.tax_code_id is None:
                    stats.line_code_only_candidates += 1
                    if execute:
                        line.tax_code_id = vat_code.tax_code_id
                        stats.lines_updated += 1
                        if _ensure_line_tax_row(db, line, vat_code, line.tax_amount):
                            stats.line_tax_rows_created += 1
                    else:
                        if not line.line_taxes:
                            stats.line_tax_rows_to_create += 1

            if abs_diff > Decimal("0.01"):
                if len(lines) > 1 and line_tax_sum == 0:
                    stats.unresolved_multi_line += 1
                else:
                    stats.unresolved_other_mismatch += 1

        if execute:
            db.commit()
        else:
            db.rollback()

    return stats


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Backfill AP invoice line VAT metadata for existing invoices.",
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Preview only")
    group.add_argument("--execute", action="store_true", help="Apply updates")
    p.add_argument(
        "--org-id",
        default="00000000-0000-0000-0000-000000000001",
        help="Organization ID",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    org_id = UUID(args.org_id)
    stats = run(org_id=org_id, execute=bool(args.execute))

    logger.info("Invoices scanned: %d", stats.invoices_scanned)
    logger.info("Lines scanned: %d", stats.lines_scanned)
    logger.info(
        "Candidates: single-line blank=%d, line-code-only=%d",
        stats.single_line_blank_candidates,
        stats.line_code_only_candidates,
    )
    logger.info("Line-tax rows planned/created: %d", stats.line_tax_rows_to_create)
    logger.info(
        "Unresolved mismatches: multi-line=%d, other=%d",
        stats.unresolved_multi_line,
        stats.unresolved_other_mismatch,
    )
    logger.info(
        "Applied: lines_updated=%d, line_tax_rows_created=%d",
        stats.lines_updated,
        stats.line_tax_rows_created,
    )
    if args.dry_run:
        logger.info("Dry run complete. Re-run with --execute to apply changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
