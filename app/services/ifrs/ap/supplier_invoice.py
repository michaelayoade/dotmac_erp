"""
SupplierInvoiceService - AP invoice lifecycle management.

Manages creation, approval workflow, posting, and payment tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID
import uuid as uuid_lib

from fastapi import HTTPException
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.ifrs.ap.supplier import Supplier
from app.models.ifrs.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.models.ifrs.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.ifrs.core_config.numbering_sequence import SequenceType
from app.services.common import coerce_uuid
from app.services.ifrs.platform.sequence import SequenceService
from app.services.response import ListResponseMixin


@dataclass
class InvoiceLineInput:
    """Input for an invoice line."""

    description: str
    quantity: Decimal
    unit_price: Decimal
    expense_account_id: Optional[UUID] = None
    asset_account_id: Optional[UUID] = None
    po_line_id: Optional[UUID] = None
    goods_receipt_line_id: Optional[UUID] = None
    item_id: Optional[UUID] = None
    tax_code_id: Optional[UUID] = None
    tax_amount: Decimal = Decimal("0")
    cost_center_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    segment_id: Optional[UUID] = None
    capitalize_flag: bool = False


@dataclass
class SupplierInvoiceInput:
    """Input for creating/updating a supplier invoice."""

    supplier_id: UUID
    invoice_type: SupplierInvoiceType
    invoice_date: date
    received_date: date
    due_date: date
    currency_code: str
    lines: list[InvoiceLineInput] = field(default_factory=list)
    supplier_invoice_number: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    exchange_rate_type_id: Optional[UUID] = None
    is_prepayment: bool = False
    is_intercompany: bool = False
    intercompany_org_id: Optional[UUID] = None
    correlation_id: Optional[str] = None


class SupplierInvoiceService(ListResponseMixin):
    """
    Service for supplier invoice lifecycle management.

    Manages creation, submission, approval, posting, and voiding.
    """

    @staticmethod
    def create_invoice(
        db: Session,
        organization_id: UUID,
        input: SupplierInvoiceInput,
        created_by_user_id: UUID,
    ) -> SupplierInvoice:
        """
        Create a new supplier invoice in DRAFT status.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Invoice input data
            created_by_user_id: User creating the invoice

        Returns:
            Created SupplierInvoice

        Raises:
            HTTPException(400): If validation fails
            HTTPException(404): If supplier not found
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)
        supplier_id = coerce_uuid(input.supplier_id)

        # Validate supplier exists and is active
        supplier = db.get(Supplier, supplier_id)
        if not supplier or supplier.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Supplier not found")

        if not supplier.is_active:
            raise HTTPException(status_code=400, detail="Supplier is not active")

        # Validate lines
        if not input.lines:
            raise HTTPException(
                status_code=400, detail="Invoice must have at least one line"
            )

        # Calculate totals
        subtotal = Decimal("0")
        tax_total = Decimal("0")

        for line in input.lines:
            line_amount = line.quantity * line.unit_price
            subtotal += line_amount
            tax_total += line.tax_amount

        total_amount = subtotal + tax_total

        # Handle credit notes (negative amounts)
        if input.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
            total_amount = -abs(total_amount)
            subtotal = -abs(subtotal)
            tax_total = -abs(tax_total)

        # Calculate functional currency amount
        exchange_rate = input.exchange_rate or Decimal("1.0")
        functional_amount = total_amount * exchange_rate

        # Generate invoice number
        invoice_number = SequenceService.get_next_number(
            db, org_id, SequenceType.SUPPLIER_INVOICE
        )

        # Create invoice
        invoice = SupplierInvoice(
            organization_id=org_id,
            supplier_id=supplier_id,
            invoice_number=invoice_number,
            supplier_invoice_number=input.supplier_invoice_number,
            invoice_type=input.invoice_type,
            invoice_date=input.invoice_date,
            received_date=input.received_date,
            due_date=input.due_date,
            currency_code=input.currency_code,
            exchange_rate=exchange_rate,
            exchange_rate_type_id=input.exchange_rate_type_id,
            subtotal=subtotal,
            tax_amount=tax_total,
            total_amount=total_amount,
            functional_currency_amount=functional_amount,
            status=SupplierInvoiceStatus.DRAFT,
            ap_control_account_id=supplier.ap_control_account_id,
            is_prepayment=input.is_prepayment,
            is_intercompany=input.is_intercompany,
            intercompany_org_id=input.intercompany_org_id,
            created_by_user_id=user_id,
            correlation_id=input.correlation_id or str(uuid_lib.uuid4()),
        )

        db.add(invoice)
        db.flush()  # Get invoice ID

        # Create lines
        for idx, line_input in enumerate(input.lines, start=1):
            line_amount = line_input.quantity * line_input.unit_price
            if input.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
                line_amount = -abs(line_amount)

            line = SupplierInvoiceLine(
                invoice_id=invoice.invoice_id,
                line_number=idx,
                description=line_input.description,
                quantity=line_input.quantity,
                unit_price=line_input.unit_price,
                line_amount=line_amount,
                tax_code_id=line_input.tax_code_id,
                tax_amount=line_input.tax_amount,
                expense_account_id=line_input.expense_account_id
                or supplier.default_expense_account_id,
                asset_account_id=line_input.asset_account_id,
                po_line_id=line_input.po_line_id,
                goods_receipt_line_id=line_input.goods_receipt_line_id,
                item_id=line_input.item_id,
                cost_center_id=line_input.cost_center_id,
                project_id=line_input.project_id,
                segment_id=line_input.segment_id,
                capitalize_flag=line_input.capitalize_flag,
            )
            db.add(line)

        db.commit()
        db.refresh(invoice)

        return invoice

    @staticmethod
    def update_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        input: SupplierInvoiceInput,
    ) -> SupplierInvoice:
        """
        Update a draft invoice.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to update
            input: Updated invoice data

        Returns:
            Updated SupplierInvoice

        Raises:
            HTTPException(404): If invoice not found
            HTTPException(400): If invoice not in DRAFT status
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        if invoice.status != SupplierInvoiceStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot update invoice with status '{invoice.status.value}'",
            )

        # Delete existing lines
        db.query(SupplierInvoiceLine).filter(
            SupplierInvoiceLine.invoice_id == inv_id
        ).delete()

        # Recalculate totals
        subtotal = Decimal("0")
        tax_total = Decimal("0")

        for line in input.lines:
            line_amount = line.quantity * line.unit_price
            subtotal += line_amount
            tax_total += line.tax_amount

        total_amount = subtotal + tax_total

        if input.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
            total_amount = -abs(total_amount)
            subtotal = -abs(subtotal)
            tax_total = -abs(tax_total)

        exchange_rate = input.exchange_rate or Decimal("1.0")
        functional_amount = total_amount * exchange_rate

        # Update invoice
        invoice.supplier_invoice_number = input.supplier_invoice_number
        invoice.invoice_type = input.invoice_type
        invoice.invoice_date = input.invoice_date
        invoice.received_date = input.received_date
        invoice.due_date = input.due_date
        invoice.currency_code = input.currency_code
        invoice.exchange_rate = exchange_rate
        invoice.exchange_rate_type_id = input.exchange_rate_type_id
        invoice.subtotal = subtotal
        invoice.tax_amount = tax_total
        invoice.total_amount = total_amount
        invoice.functional_currency_amount = functional_amount
        invoice.is_prepayment = input.is_prepayment
        invoice.is_intercompany = input.is_intercompany
        invoice.intercompany_org_id = input.intercompany_org_id

        # Re-create lines
        supplier = db.get(Supplier, invoice.supplier_id)
        for idx, line_input in enumerate(input.lines, start=1):
            line_amount = line_input.quantity * line_input.unit_price
            if input.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
                line_amount = -abs(line_amount)

            line = SupplierInvoiceLine(
                invoice_id=inv_id,
                line_number=idx,
                description=line_input.description,
                quantity=line_input.quantity,
                unit_price=line_input.unit_price,
                line_amount=line_amount,
                tax_code_id=line_input.tax_code_id,
                tax_amount=line_input.tax_amount,
                expense_account_id=line_input.expense_account_id
                or (supplier.default_expense_account_id if supplier else None),
                asset_account_id=line_input.asset_account_id,
                po_line_id=line_input.po_line_id,
                goods_receipt_line_id=line_input.goods_receipt_line_id,
                item_id=line_input.item_id,
                cost_center_id=line_input.cost_center_id,
                project_id=line_input.project_id,
                segment_id=line_input.segment_id,
                capitalize_flag=line_input.capitalize_flag,
            )
            db.add(line)

        db.commit()
        db.refresh(invoice)

        return invoice

    @staticmethod
    def submit_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        submitted_by_user_id: UUID,
    ) -> SupplierInvoice:
        """
        Submit an invoice for approval.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to submit
            submitted_by_user_id: User submitting

        Returns:
            Updated SupplierInvoice

        Raises:
            HTTPException(404): If invoice not found
            HTTPException(400): If invoice cannot be submitted
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(submitted_by_user_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        if invoice.status != SupplierInvoiceStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot submit invoice with status '{invoice.status.value}'",
            )

        invoice.status = SupplierInvoiceStatus.SUBMITTED
        invoice.submitted_by_user_id = user_id
        invoice.submitted_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(invoice)

        return invoice

    @staticmethod
    def approve_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        approved_by_user_id: UUID,
    ) -> SupplierInvoice:
        """
        Approve a submitted invoice.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to approve
            approved_by_user_id: User approving

        Returns:
            Updated SupplierInvoice

        Raises:
            HTTPException(404): If invoice not found
            HTTPException(400): If invoice cannot be approved or SoD violation
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(approved_by_user_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        if invoice.status not in [
            SupplierInvoiceStatus.SUBMITTED,
            SupplierInvoiceStatus.PENDING_APPROVAL,
        ]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve invoice with status '{invoice.status.value}'",
            )

        # Segregation of Duties check
        if invoice.submitted_by_user_id == user_id:
            raise HTTPException(
                status_code=400,
                detail="Segregation of duties violation: submitter cannot approve",
            )

        invoice.status = SupplierInvoiceStatus.APPROVED
        invoice.approved_by_user_id = user_id
        invoice.approved_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(invoice)

        return invoice

    @staticmethod
    def post_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        posted_by_user_id: UUID,
        posting_date: Optional[date] = None,
    ) -> SupplierInvoice:
        """
        Post an approved invoice to the general ledger.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to post
            posted_by_user_id: User posting
            posting_date: Optional posting date (defaults to invoice date)

        Returns:
            Updated SupplierInvoice

        Raises:
            HTTPException(404): If invoice not found
            HTTPException(400): If invoice cannot be posted
        """
        from app.services.ifrs.ap.ap_posting_adapter import APPostingAdapter

        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(posted_by_user_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        if invoice.status != SupplierInvoiceStatus.APPROVED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot post invoice with status '{invoice.status.value}'",
            )

        # Use APPostingAdapter to create GL entries
        result = APPostingAdapter.post_invoice(
            db=db,
            organization_id=org_id,
            invoice_id=inv_id,
            posting_date=posting_date or invoice.invoice_date,
            posted_by_user_id=user_id,
        )

        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)

        # Update invoice status
        invoice.status = SupplierInvoiceStatus.POSTED
        invoice.posted_by_user_id = user_id
        invoice.posted_at = datetime.now(timezone.utc)
        invoice.journal_entry_id = result.journal_entry_id
        invoice.posting_batch_id = result.posting_batch_id
        invoice.posting_status = "POSTED"

        db.commit()
        db.refresh(invoice)

        return invoice

    @staticmethod
    def void_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        voided_by_user_id: UUID,
        reason: str,
    ) -> SupplierInvoice:
        """
        Void an invoice.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to void
            voided_by_user_id: User voiding
            reason: Reason for voiding

        Returns:
            Updated SupplierInvoice

        Raises:
            HTTPException(404): If invoice not found
            HTTPException(400): If invoice cannot be voided
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        # Can void DRAFT, SUBMITTED, PENDING_APPROVAL, APPROVED
        non_voidable = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
            SupplierInvoiceStatus.PAID,
            SupplierInvoiceStatus.VOID,
        ]

        if invoice.status in non_voidable:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot void invoice with status '{invoice.status.value}'",
            )

        invoice.status = SupplierInvoiceStatus.VOID

        db.commit()
        db.refresh(invoice)

        return invoice

    @staticmethod
    def put_on_hold(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        reason: str,
    ) -> SupplierInvoice:
        """
        Put an invoice on hold.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to hold
            reason: Reason for hold

        Returns:
            Updated SupplierInvoice
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        if invoice.status in [
            SupplierInvoiceStatus.PAID,
            SupplierInvoiceStatus.VOID,
        ]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot put on hold invoice with status '{invoice.status.value}'",
            )

        invoice.status = SupplierInvoiceStatus.ON_HOLD

        db.commit()
        db.refresh(invoice)

        return invoice

    @staticmethod
    def release_from_hold(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
    ) -> SupplierInvoice:
        """
        Release an invoice from hold.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to release

        Returns:
            Updated SupplierInvoice
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        if invoice.status != SupplierInvoiceStatus.ON_HOLD:
            raise HTTPException(
                status_code=400, detail="Invoice is not on hold"
            )

        # Return to APPROVED if it was posted-eligible, else SUBMITTED
        if invoice.approved_by_user_id:
            invoice.status = SupplierInvoiceStatus.APPROVED
        else:
            invoice.status = SupplierInvoiceStatus.SUBMITTED

        db.commit()
        db.refresh(invoice)

        return invoice

    @staticmethod
    def record_payment(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        payment_amount: Decimal,
    ) -> SupplierInvoice:
        """
        Record a payment against an invoice.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice being paid
            payment_amount: Amount paid

        Returns:
            Updated SupplierInvoice
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        if invoice.status not in [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot pay invoice with status '{invoice.status.value}'",
            )

        invoice.amount_paid += payment_amount

        if invoice.amount_paid >= invoice.total_amount:
            invoice.status = SupplierInvoiceStatus.PAID
        else:
            invoice.status = SupplierInvoiceStatus.PARTIALLY_PAID

        db.commit()
        db.refresh(invoice)

        return invoice

    @staticmethod
    def get(
        db: Session,
        invoice_id: str,
    ) -> SupplierInvoice:
        """
        Get an invoice by ID.

        Args:
            db: Database session
            invoice_id: Invoice ID

        Returns:
            SupplierInvoice

        Raises:
            HTTPException(404): If not found
        """
        invoice = db.get(SupplierInvoice, coerce_uuid(invoice_id))
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")
        return invoice

    @staticmethod
    def get_invoice_lines(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
    ) -> list[SupplierInvoiceLine]:
        """
        Get lines for an invoice.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice ID

        Returns:
            List of SupplierInvoiceLine objects
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        return (
            db.query(SupplierInvoiceLine)
            .filter(SupplierInvoiceLine.invoice_id == inv_id)
            .order_by(SupplierInvoiceLine.line_number)
            .all()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        supplier_id: Optional[str] = None,
        status: Optional[SupplierInvoiceStatus] = None,
        invoice_type: Optional[SupplierInvoiceType] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        overdue_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SupplierInvoice]:
        """
        List invoices with optional filters.

        Args:
            db: Database session
            organization_id: Filter by organization
            supplier_id: Filter by supplier
            status: Filter by status
            invoice_type: Filter by type
            from_date: Filter by invoice date from
            to_date: Filter by invoice date to
            overdue_only: Only overdue invoices
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of SupplierInvoice objects
        """
        query = db.query(SupplierInvoice)

        if organization_id:
            query = query.filter(
                SupplierInvoice.organization_id == coerce_uuid(organization_id)
            )

        if supplier_id:
            query = query.filter(
                SupplierInvoice.supplier_id == coerce_uuid(supplier_id)
            )

        if status:
            query = query.filter(SupplierInvoice.status == status)

        if invoice_type:
            query = query.filter(SupplierInvoice.invoice_type == invoice_type)

        if from_date:
            query = query.filter(SupplierInvoice.invoice_date >= from_date)

        if to_date:
            query = query.filter(SupplierInvoice.invoice_date <= to_date)

        if overdue_only:
            query = query.filter(
                and_(
                    SupplierInvoice.due_date < date.today(),
                    SupplierInvoice.status.in_([
                        SupplierInvoiceStatus.POSTED,
                        SupplierInvoiceStatus.PARTIALLY_PAID,
                    ]),
                )
            )

        query = query.order_by(SupplierInvoice.invoice_date.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
supplier_invoice_service = SupplierInvoiceService()
