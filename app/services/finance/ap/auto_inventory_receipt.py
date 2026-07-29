"""
Automatic inventory receipts for submitted AP invoices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.inventory.inventory_transaction import (
    InventoryTransaction,
    TransactionType,
)
from app.models.inventory.item import Item
from app.models.inventory.warehouse import Warehouse
from app.services.common import NotFoundError, ValidationError, coerce_uuid
from app.services.finance.gl.period_guard import PeriodGuardService
from app.services.inventory.transaction import (
    InventoryTransactionService,
    TransactionInput,
)

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc


AUTO_RECEIPT_SOURCE_DOCUMENT_TYPE = "AP_INVOICE"


@dataclass(frozen=True)
class APInvoiceAutoReceiptResult:
    """Outcome from an AP invoice auto-receipt attempt."""

    created_count: int
    skipped_count: int
    transaction_ids: list[UUID]


class APInvoiceAutoReceiptService:
    """Create inventory receipts from submitted AP invoices."""

    @staticmethod
    def _is_stock_line(item: Item | None, line: SupplierInvoiceLine) -> bool:
        return bool(
            item
            and item.track_inventory
            and line.item_id
            and (line.quantity or Decimal("0")) > Decimal("0")
        )

    @staticmethod
    def _fiscal_period_for_date(
        db: Session,
        organization_id: UUID,
        transaction_date: datetime,
    ) -> FiscalPeriod:
        period = PeriodGuardService.get_period_for_date(
            db,
            organization_id,
            transaction_date.date(),
        )
        if not period:
            raise ValidationError(
                "Cannot create inventory receipt: no fiscal period exists for today"
            )
        return period

    @staticmethod
    def _existing_transaction_for_line(
        db: Session,
        organization_id: UUID,
        line: SupplierInvoiceLine,
    ) -> InventoryTransaction | None:
        return db.scalars(
            select(InventoryTransaction).where(
                InventoryTransaction.organization_id == organization_id,
                InventoryTransaction.transaction_type == TransactionType.RECEIPT,
                InventoryTransaction.source_document_type
                == AUTO_RECEIPT_SOURCE_DOCUMENT_TYPE,
                InventoryTransaction.source_document_line_id == line.line_id,
            )
        ).first()

    @staticmethod
    def _generated_serial_numbers(
        invoice: SupplierInvoice,
        line: SupplierInvoiceLine,
    ) -> list[str]:
        quantity = line.quantity or Decimal("0")
        if quantity != quantity.to_integral_value():
            raise ValidationError(
                "Cannot create inventory receipt: serial-tracked invoice line "
                f"{line.line_number} must have a whole-number quantity"
            )
        count = int(quantity)
        invoice_part = re.sub(r"[^A-Za-z0-9-]+", "-", invoice.invoice_number or "APINV")
        return [
            f"{invoice_part}-L{line.line_number}-{idx:04d}"[:100]
            for idx in range(1, count + 1)
        ]

    @staticmethod
    def _serial_numbers_for_line(
        invoice: SupplierInvoice,
        line: SupplierInvoiceLine,
        item: Item,
    ) -> list[str] | None:
        if not item.track_serial_numbers:
            return None
        if line.receipt_auto_generate_serials:
            return APInvoiceAutoReceiptService._generated_serial_numbers(invoice, line)
        serial_numbers = line.receipt_serial_numbers or []
        return serial_numbers or None

    @staticmethod
    def create_for_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        created_by_user_id: UUID,
    ) -> APInvoiceAutoReceiptResult:
        """Create missing inventory receipt transactions for a submitted AP invoice."""
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(created_by_user_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise NotFoundError("Invoice not found")

        if not getattr(invoice, "auto_create_inventory_receipt", False):
            return APInvoiceAutoReceiptResult(0, 0, [])
        receiptable_statuses = {
            SupplierInvoiceStatus.SUBMITTED,
            SupplierInvoiceStatus.PENDING_APPROVAL,
            SupplierInvoiceStatus.APPROVED,
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
            SupplierInvoiceStatus.PAID,
        }
        if invoice.status not in receiptable_statuses:
            return APInvoiceAutoReceiptResult(0, 0, [])

        lines = list(
            db.scalars(
                select(SupplierInvoiceLine)
                .where(SupplierInvoiceLine.invoice_id == invoice.invoice_id)
                .order_by(SupplierInvoiceLine.line_number)
            ).all()
        )

        transaction_date = datetime.now(UTC)
        fiscal_period: FiscalPeriod | None = None
        created_ids: list[UUID] = []
        skipped = 0
        for line in lines:
            item = db.get(Item, line.item_id) if line.item_id else None
            if not APInvoiceAutoReceiptService._is_stock_line(item, line):
                skipped += 1
                continue
            if item is None or line.item_id is None:
                skipped += 1
                continue

            if line.auto_receipt_transaction_id:
                skipped += 1
                continue

            existing = APInvoiceAutoReceiptService._existing_transaction_for_line(
                db, org_id, line
            )
            if existing:
                line.auto_receipt_transaction_id = existing.transaction_id
                skipped += 1
                continue

            if not line.receipt_warehouse_id:
                raise ValidationError(
                    "Cannot create inventory receipt: warehouse is required for "
                    f"stock-tracked line {line.line_number}"
                )

            warehouse = db.get(Warehouse, line.receipt_warehouse_id)
            if not warehouse or warehouse.organization_id != org_id:
                raise ValidationError(
                    "Cannot create inventory receipt: warehouse not found for "
                    f"line {line.line_number}"
                )

            serial_numbers = APInvoiceAutoReceiptService._serial_numbers_for_line(
                invoice, line, item
            )
            if fiscal_period is None:
                fiscal_period = APInvoiceAutoReceiptService._fiscal_period_for_date(
                    db, org_id, transaction_date
                )
            txn_input = TransactionInput(
                transaction_type=TransactionType.RECEIPT,
                transaction_date=transaction_date,
                fiscal_period_id=fiscal_period.fiscal_period_id,
                item_id=line.item_id,
                warehouse_id=line.receipt_warehouse_id,
                quantity=line.quantity,
                unit_cost=line.unit_price,
                uom=item.base_uom or "",
                currency_code=invoice.currency_code,
                source_document_type=AUTO_RECEIPT_SOURCE_DOCUMENT_TYPE,
                source_document_id=invoice.invoice_id,
                source_document_line_id=line.line_id,
                reference=line.receipt_reference
                or invoice.supplier_invoice_number
                or invoice.invoice_number,
                serial_numbers=serial_numbers,
                allow_missing_serial_numbers=bool(
                    item.track_serial_numbers and not serial_numbers
                ),
            )

            try:
                transaction = InventoryTransactionService.create_receipt(
                    db, org_id, txn_input, user_id
                )
            except HTTPException as exc:
                raise ValidationError(
                    f"Cannot create inventory receipt: {exc.detail}"
                ) from exc

            line.auto_receipt_transaction_id = transaction.transaction_id
            created_ids.append(transaction.transaction_id)

        return APInvoiceAutoReceiptResult(
            created_count=len(created_ids),
            skipped_count=skipped,
            transaction_ids=created_ids,
        )


ap_invoice_auto_receipt_service = APInvoiceAutoReceiptService()
