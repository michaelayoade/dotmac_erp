"""
Store approval queue for AP invoice inventory receipts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ap.invoice_inventory_receipt_approval import (
    InvoiceInventoryReceiptApproval,
    InvoiceInventoryReceiptApprovalStatus,
)
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.inventory.inventory_transaction import TransactionType
from app.models.inventory.item import Item
from app.models.inventory.warehouse import Warehouse
from app.models.notification import EntityType, NotificationType
from app.services.common import NotFoundError, ValidationError, coerce_uuid
from app.services.finance.ap.auto_inventory_receipt import (
    APInvoiceAutoReceiptService,
    AUTO_RECEIPT_SOURCE_DOCUMENT_TYPE,
)
from app.services.inventory.transaction import (
    InventoryTransactionService,
    TransactionInput,
)
from app.services.notification import notification_service
from app.services.rbac import get_users_with_permission

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc


logger = logging.getLogger(__name__)
STORE_RECEIPT_APPROVER_PERMISSION = "inventory:receipt_approvals:approve"


@dataclass(frozen=True)
class APInventoryReceiptApprovalQueueResult:
    """Outcome from creating AP inventory receipt approval requests."""

    created_count: int
    skipped_count: int
    approval_ids: list[UUID]


class APInventoryReceiptApprovalService:
    """Create and manage store approvals for AP invoice stock receipts."""

    @staticmethod
    def _is_mock_like(db: Session) -> bool:
        """Avoid notification side effects in lightweight mock-session tests."""
        return type(db).__module__.startswith("unittest.mock")

    @staticmethod
    def list_pending(
        db: Session,
        organization_id: UUID,
        *,
        status: InvoiceInventoryReceiptApprovalStatus
        | None = InvoiceInventoryReceiptApprovalStatus.PENDING,
    ) -> list[InvoiceInventoryReceiptApproval]:
        org_id = coerce_uuid(organization_id)
        stmt = select(InvoiceInventoryReceiptApproval).where(
            InvoiceInventoryReceiptApproval.organization_id == org_id
        )
        if status is not None:
            stmt = stmt.where(InvoiceInventoryReceiptApproval.status == status)
        return list(
            db.scalars(
                stmt.order_by(InvoiceInventoryReceiptApproval.created_at.desc())
            ).all()
        )

    @staticmethod
    def get(
        db: Session,
        organization_id: UUID,
        approval_id: UUID,
    ) -> InvoiceInventoryReceiptApproval:
        org_id = coerce_uuid(organization_id)
        app_id = coerce_uuid(approval_id)
        approval = db.get(InvoiceInventoryReceiptApproval, app_id)
        if not approval or approval.organization_id != org_id:
            raise NotFoundError("Receipt approval not found")
        return approval

    @staticmethod
    def _existing_approval_for_line(
        db: Session,
        organization_id: UUID,
        line: SupplierInvoiceLine,
    ) -> InvoiceInventoryReceiptApproval | None:
        return db.scalars(
            select(InvoiceInventoryReceiptApproval).where(
                InvoiceInventoryReceiptApproval.organization_id == organization_id,
                InvoiceInventoryReceiptApproval.supplier_invoice_line_id
                == line.line_id,
            )
        ).first()

    @staticmethod
    def create_pending_from_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        submitted_by_user_id: UUID,
    ) -> APInventoryReceiptApprovalQueueResult:
        """Create pending store approvals for stock-tracked AP invoice lines."""
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(submitted_by_user_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise NotFoundError("Invoice not found")

        eligible_statuses = {
            SupplierInvoiceStatus.SUBMITTED,
            SupplierInvoiceStatus.PENDING_APPROVAL,
            SupplierInvoiceStatus.APPROVED,
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
            SupplierInvoiceStatus.PAID,
        }
        if invoice.status not in eligible_statuses:
            return APInventoryReceiptApprovalQueueResult(0, 0, [])

        lines = list(
            db.scalars(
                select(SupplierInvoiceLine)
                .where(SupplierInvoiceLine.invoice_id == invoice.invoice_id)
                .order_by(SupplierInvoiceLine.line_number)
            ).all()
        )

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
            if APInventoryReceiptApprovalService._existing_approval_for_line(
                db, org_id, line
            ):
                skipped += 1
                continue
            if not line.receipt_warehouse_id:
                raise ValidationError(
                    "Cannot request store receipt approval: warehouse is required for "
                    f"stock-tracked line {line.line_number}"
                )
            warehouse = db.get(Warehouse, line.receipt_warehouse_id)
            if not warehouse or warehouse.organization_id != org_id:
                raise ValidationError(
                    "Cannot request store receipt approval: warehouse not found for "
                    f"line {line.line_number}"
                )

            serial_numbers = APInvoiceAutoReceiptService._serial_numbers_for_line(
                invoice, line, item
            )
            approval = InvoiceInventoryReceiptApproval(
                organization_id=org_id,
                supplier_invoice_id=invoice.invoice_id,
                supplier_invoice_line_id=line.line_id,
                item_id=line.item_id,
                warehouse_id=line.receipt_warehouse_id,
                requested_quantity=line.quantity or Decimal("0"),
                receipt_reference=line.receipt_reference
                or invoice.supplier_invoice_number
                or invoice.invoice_number,
                receipt_serial_numbers=serial_numbers,
                status=InvoiceInventoryReceiptApprovalStatus.PENDING,
                submitted_by_user_id=user_id,
            )
            db.add(approval)
            db.flush()
            created_ids.append(approval.approval_id)
            APInventoryReceiptApprovalService._notify_store_approvers(
                db,
                org_id,
                invoice,
                approval,
                actor_id=user_id,
            )

        return APInventoryReceiptApprovalQueueResult(
            created_count=len(created_ids),
            skipped_count=skipped,
            approval_ids=created_ids,
        )

    @staticmethod
    def _fiscal_period_for_date(
        db: Session,
        organization_id: UUID,
        transaction_date: datetime,
    ) -> FiscalPeriod:
        period = db.scalars(
            select(FiscalPeriod).where(
                FiscalPeriod.organization_id == organization_id,
                FiscalPeriod.start_date <= transaction_date.date(),
                FiscalPeriod.end_date >= transaction_date.date(),
            )
        ).first()
        if not period:
            raise ValidationError(
                "Cannot approve inventory receipt: no fiscal period exists for today"
            )
        return period

    @staticmethod
    def _invoice_notification_recipients(
        invoice: SupplierInvoice,
        actor_id: UUID,
    ) -> set[UUID]:
        recipients: set[UUID] = set()
        for attr in (
            "created_by_user_id",
            "submitted_by_user_id",
            "approved_by_user_id",
            "posted_by_user_id",
        ):
            recipient_id = getattr(invoice, attr, None)
            if recipient_id and recipient_id != actor_id:
                recipients.add(recipient_id)
        return recipients

    @staticmethod
    def _notify_store_approvers(
        db: Session,
        organization_id: UUID,
        invoice: SupplierInvoice,
        approval: InvoiceInventoryReceiptApproval,
        *,
        actor_id: UUID,
    ) -> None:
        if APInventoryReceiptApprovalService._is_mock_like(db):
            return
        try:
            approver_roles = get_users_with_permission(
                db,
                organization_id,
                STORE_RECEIPT_APPROVER_PERMISSION,
            )
            invoice_ref = invoice.invoice_number or invoice.supplier_invoice_number
            for role in approver_roles:
                recipient_id = getattr(role, "person_id", None)
                if not recipient_id:
                    continue
                notification_service.create(
                    db,
                    organization_id=organization_id,
                    recipient_id=recipient_id,
                    entity_type=EntityType.APPROVAL,
                    entity_id=approval.approval_id,
                    notification_type=NotificationType.SUBMITTED,
                    title="Stock receipt approval needed",
                    message=(
                        f"Invoice {invoice_ref} has stock items waiting for store "
                        "confirmation."
                    ),
                    action_url=f"/inventory/receipt-approvals/{approval.approval_id}",
                    actor_id=actor_id,
                )
        except Exception:
            logger.exception(
                "Failed to notify store approvers for AP receipt approval %s",
                approval.approval_id,
            )

    @staticmethod
    def notify_store_approvers_of_draft_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        *,
        actor_id: UUID,
    ) -> None:
        """Notify stores that a draft inventory invoice is being prepared."""
        if APInventoryReceiptApprovalService._is_mock_like(db):
            return
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(actor_id)
        try:
            invoice = db.get(SupplierInvoice, inv_id)
            if not invoice or invoice.organization_id != org_id:
                return
            lines = list(
                db.scalars(
                    select(SupplierInvoiceLine).where(
                        SupplierInvoiceLine.invoice_id == invoice.invoice_id
                    )
                ).all()
            )
            has_stock_line = False
            for line in lines:
                item = db.get(Item, line.item_id) if line.item_id else None
                if APInvoiceAutoReceiptService._is_stock_line(item, line):
                    has_stock_line = True
                    break
            if not has_stock_line:
                return

            approver_roles = get_users_with_permission(
                db,
                org_id,
                STORE_RECEIPT_APPROVER_PERMISSION,
            )
            invoice_ref = invoice.invoice_number or invoice.supplier_invoice_number
            for role in approver_roles:
                recipient_id = getattr(role, "person_id", None)
                if not recipient_id:
                    continue
                notification_service.create(
                    db,
                    organization_id=org_id,
                    recipient_id=recipient_id,
                    entity_type=EntityType.INVOICE,
                    entity_id=invoice.invoice_id,
                    notification_type=NotificationType.INFO,
                    title="Draft inventory invoice prepared",
                    message=(
                        f"Draft invoice {invoice_ref} includes stock items for store "
                        "receipt review. Approval will be available after AP submits it."
                    ),
                    action_url="/inventory/receipt-approvals",
                    actor_id=user_id,
                )
        except Exception:
            logger.exception(
                "Failed to notify store approvers for draft AP invoice %s",
                inv_id,
            )

    @staticmethod
    def _notify_invoice_users(
        db: Session,
        organization_id: UUID,
        invoice: SupplierInvoice,
        approval: InvoiceInventoryReceiptApproval,
        *,
        actor_id: UUID,
        notification_type: NotificationType,
        title: str,
        message: str,
    ) -> None:
        if APInventoryReceiptApprovalService._is_mock_like(db):
            return
        try:
            for (
                recipient_id
            ) in APInventoryReceiptApprovalService._invoice_notification_recipients(
                invoice,
                actor_id,
            ):
                notification_service.create(
                    db,
                    organization_id=organization_id,
                    recipient_id=recipient_id,
                    entity_type=EntityType.APPROVAL,
                    entity_id=approval.approval_id,
                    notification_type=notification_type,
                    title=title,
                    message=message,
                    action_url=f"/finance/ap/invoices/{invoice.invoice_id}",
                    actor_id=actor_id,
                )
        except Exception:
            logger.exception(
                "Failed to notify AP users for AP receipt approval %s",
                approval.approval_id,
            )

    @staticmethod
    def approve(
        db: Session,
        organization_id: UUID,
        approval_id: UUID,
        approved_by_user_id: UUID,
        *,
        approved_quantity: Decimal | None = None,
        warehouse_id: UUID | None = None,
        serial_numbers: list[str] | None = None,
    ) -> InvoiceInventoryReceiptApproval:
        """Approve a pending store receipt and post the inventory receipt."""
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(approved_by_user_id)
        approval = APInventoryReceiptApprovalService.get(db, org_id, approval_id)

        if approval.status != InvoiceInventoryReceiptApprovalStatus.PENDING:
            raise ValidationError("Only pending receipt approvals can be approved")

        invoice = db.get(SupplierInvoice, approval.supplier_invoice_id)
        if not invoice or invoice.organization_id != org_id:
            raise NotFoundError("Invoice not found")

        line = db.get(SupplierInvoiceLine, approval.supplier_invoice_line_id)
        if not line or line.invoice_id != invoice.invoice_id:
            raise NotFoundError("Invoice line not found")

        item = db.get(Item, approval.item_id)
        if not item or item.organization_id != org_id:
            raise NotFoundError("Item not found")
        if not item.track_inventory:
            raise ValidationError("Only stock-tracked items can be approved")

        qty = (
            approved_quantity
            if approved_quantity is not None
            else approval.requested_quantity
        )
        if line.auto_receipt_transaction_id:
            raise ValidationError("Invoice line has already been received")
        if qty <= Decimal("0"):
            raise ValidationError("Approved quantity must be greater than zero")
        if qty > approval.requested_quantity:
            raise ValidationError("Approved quantity cannot exceed requested quantity")

        wh_id = coerce_uuid(warehouse_id or approval.warehouse_id)
        warehouse = db.get(Warehouse, wh_id)
        if not warehouse or warehouse.organization_id != org_id:
            raise ValidationError("Warehouse not found")

        effective_serials = serial_numbers or approval.receipt_serial_numbers
        transaction_date = datetime.now(UTC)
        fiscal_period = APInventoryReceiptApprovalService._fiscal_period_for_date(
            db, org_id, transaction_date
        )

        txn_input = TransactionInput(
            transaction_type=TransactionType.RECEIPT,
            transaction_date=transaction_date,
            fiscal_period_id=fiscal_period.fiscal_period_id,
            item_id=approval.item_id,
            warehouse_id=wh_id,
            quantity=qty,
            unit_cost=line.unit_price,
            uom=item.base_uom or "",
            currency_code=invoice.currency_code,
            source_document_type=AUTO_RECEIPT_SOURCE_DOCUMENT_TYPE,
            source_document_id=invoice.invoice_id,
            source_document_line_id=line.line_id,
            reference=approval.receipt_reference
            or line.receipt_reference
            or invoice.supplier_invoice_number
            or invoice.invoice_number,
            serial_numbers=effective_serials,
        )

        try:
            transaction = InventoryTransactionService.create_receipt(
                db, org_id, txn_input, user_id
            )
        except HTTPException as exc:
            raise ValidationError(
                f"Cannot approve inventory receipt: {exc.detail}"
            ) from exc

        approval.approved_quantity = qty
        approval.warehouse_id = wh_id
        approval.receipt_serial_numbers = effective_serials
        remaining_quantity = approval.requested_quantity - qty
        is_partial = remaining_quantity > Decimal("0")
        approval.status = (
            InvoiceInventoryReceiptApprovalStatus.PARTIALLY_RECEIVED
            if is_partial
            else InvoiceInventoryReceiptApprovalStatus.POSTED_TO_INVENTORY
        )
        approval.approved_by_user_id = user_id
        approval.approved_at = transaction_date
        approval.inventory_transaction_id = transaction.transaction_id
        if not is_partial:
            line.auto_receipt_transaction_id = transaction.transaction_id
        else:
            residual = InvoiceInventoryReceiptApproval(
                organization_id=org_id,
                supplier_invoice_id=invoice.invoice_id,
                supplier_invoice_line_id=line.line_id,
                item_id=approval.item_id,
                warehouse_id=wh_id,
                requested_quantity=remaining_quantity,
                receipt_reference=approval.receipt_reference
                or line.receipt_reference
                or invoice.supplier_invoice_number
                or invoice.invoice_number,
                status=InvoiceInventoryReceiptApprovalStatus.PENDING,
                submitted_by_user_id=user_id,
            )
            db.add(residual)
        db.flush()
        invoice_ref = invoice.invoice_number or invoice.supplier_invoice_number
        if is_partial:
            APInventoryReceiptApprovalService._notify_invoice_users(
                db,
                org_id,
                invoice,
                approval,
                actor_id=user_id,
                notification_type=NotificationType.STATUS_CHANGE,
                title="Partial stock receipt posted",
                message=(
                    f"Store received {qty} of {approval.requested_quantity} for "
                    f"invoice {invoice_ref}. {remaining_quantity} remains pending."
                ),
            )
        else:
            APInventoryReceiptApprovalService._notify_invoice_users(
                db,
                org_id,
                invoice,
                approval,
                actor_id=user_id,
                notification_type=NotificationType.APPROVED,
                title="Stock receipt posted",
                message=f"Store received {qty} item(s) for invoice {invoice_ref}.",
            )
        return approval

    @staticmethod
    def reject(
        db: Session,
        organization_id: UUID,
        approval_id: UUID,
        rejected_by_user_id: UUID,
        *,
        rejection_reason: str,
    ) -> InvoiceInventoryReceiptApproval:
        """Reject a pending store receipt without changing inventory."""
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(rejected_by_user_id)
        approval = APInventoryReceiptApprovalService.get(db, org_id, approval_id)
        if approval.status != InvoiceInventoryReceiptApprovalStatus.PENDING:
            raise ValidationError("Only pending receipt approvals can be rejected")
        reason = rejection_reason.strip()
        if not reason:
            raise ValidationError("Rejection reason is required")

        approval.status = InvoiceInventoryReceiptApprovalStatus.REJECTED
        approval.rejected_by_user_id = user_id
        approval.rejected_at = datetime.now(UTC)
        approval.rejection_reason = reason[:500]
        db.flush()
        invoice = db.get(SupplierInvoice, approval.supplier_invoice_id)
        if invoice and invoice.organization_id == org_id:
            invoice_ref = invoice.invoice_number or invoice.supplier_invoice_number
            APInventoryReceiptApprovalService._notify_invoice_users(
                db,
                org_id,
                invoice,
                approval,
                actor_id=user_id,
                notification_type=NotificationType.REJECTED,
                title="Stock receipt rejected",
                message=f"Store rejected receipt for invoice {invoice_ref}: {reason[:500]}",
            )
        return approval

    @staticmethod
    def reject_pending_for_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        rejected_by_user_id: UUID,
        *,
        rejection_reason: str,
    ) -> int:
        """Reject all pending store receipt approvals for a rejected AP invoice."""
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(rejected_by_user_id)
        reason = rejection_reason.strip()
        if not reason:
            reason = "AP invoice rejected"
        now = datetime.now(UTC)
        approvals = list(
            db.scalars(
                select(InvoiceInventoryReceiptApproval).where(
                    InvoiceInventoryReceiptApproval.organization_id == org_id,
                    InvoiceInventoryReceiptApproval.supplier_invoice_id == inv_id,
                    InvoiceInventoryReceiptApproval.status
                    == InvoiceInventoryReceiptApprovalStatus.PENDING,
                )
            ).all()
        )
        for approval in approvals:
            approval.status = InvoiceInventoryReceiptApprovalStatus.REJECTED
            approval.rejected_by_user_id = user_id
            approval.rejected_at = now
            approval.rejection_reason = reason[:500]
        if approvals:
            db.flush()
        return len(approvals)


ap_inventory_receipt_approval_service = APInventoryReceiptApprovalService()
