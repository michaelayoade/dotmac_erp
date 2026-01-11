"""
ARInvoiceService - AR invoice lifecycle management.

Manages creation, approval workflow, posting, and payment tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID
import uuid as uuid_lib

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.ifrs.ar.contract import Contract
from app.models.ifrs.ar.customer import Customer
from app.models.ifrs.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.ifrs.ar.invoice_line import InvoiceLine
from app.models.ifrs.ar.performance_obligation import PerformanceObligation
from app.models.ifrs.core_config.numbering_sequence import SequenceType
from app.models.ifrs.core_org.cost_center import CostCenter
from app.models.ifrs.core_org.project import Project
from app.models.ifrs.core_org.reporting_segment import ReportingSegment
from app.models.ifrs.gl.account import Account
from app.models.ifrs.inv.item import Item
from app.models.ifrs.tax.tax_code import TaxCode
from app.services.common import coerce_uuid
from app.services.ifrs.platform.sequence import SequenceService
from app.services.response import ListResponseMixin


@dataclass
class ARInvoiceLineInput:
    """Input for an AR invoice line."""

    description: str
    quantity: Decimal
    unit_price: Decimal
    revenue_account_id: Optional[UUID] = None
    item_id: Optional[UUID] = None
    tax_code_id: Optional[UUID] = None
    tax_amount: Decimal = Decimal("0")
    discount_amount: Decimal = Decimal("0")
    cost_center_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    segment_id: Optional[UUID] = None
    contract_id: Optional[UUID] = None
    performance_obligation_id: Optional[UUID] = None


@dataclass
class ARInvoiceInput:
    """Input for creating/updating an AR invoice."""

    customer_id: UUID
    invoice_type: InvoiceType
    invoice_date: date
    due_date: date
    currency_code: str
    lines: list[ARInvoiceLineInput] = field(default_factory=list)
    contract_id: Optional[UUID] = None
    exchange_rate: Optional[Decimal] = None
    exchange_rate_type_id: Optional[UUID] = None
    payment_terms_id: Optional[UUID] = None
    billing_address: Optional[dict[str, Any]] = None
    shipping_address: Optional[dict[str, Any]] = None
    notes: Optional[str] = None
    internal_notes: Optional[str] = None
    is_intercompany: bool = False
    intercompany_org_id: Optional[UUID] = None
    correlation_id: Optional[str] = None


def _require_org_match(
    db: Session,
    organization_id: UUID,
    model: type,
    record_id: Optional[UUID],
    label: str,
) -> None:
    """Ensure a referenced record belongs to the organization."""
    if not record_id:
        return
    record = db.get(model, coerce_uuid(record_id))
    if not record or getattr(record, "organization_id", None) != organization_id:
        raise HTTPException(status_code=404, detail=f"{label} not found")


class ARInvoiceService(ListResponseMixin):
    """
    Service for AR invoice lifecycle management.

    Manages creation, submission, approval, posting, and voiding.
    """

    @staticmethod
    def create_invoice(
        db: Session,
        organization_id: UUID,
        input: ARInvoiceInput,
        created_by_user_id: UUID,
    ) -> Invoice:
        """
        Create a new AR invoice in DRAFT status.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Invoice input data
            created_by_user_id: User creating the invoice

        Returns:
            Created Invoice
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)
        customer_id = coerce_uuid(input.customer_id)

        # Validate customer
        customer = db.get(Customer, customer_id)
        if not customer or customer.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Customer not found")

        if not customer.is_active:
            raise HTTPException(status_code=400, detail="Customer is not active")

        # Validate lines
        if not input.lines:
            raise HTTPException(
                status_code=400, detail="Invoice must have at least one line"
            )

        if input.contract_id:
            _require_org_match(db, org_id, Contract, input.contract_id, "Contract")

        # Calculate totals
        subtotal = Decimal("0")
        tax_total = Decimal("0")
        discount_total = Decimal("0")

        for line in input.lines:
            _require_org_match(db, org_id, Account, line.revenue_account_id, "Revenue account")
            _require_org_match(db, org_id, TaxCode, line.tax_code_id, "Tax code")
            _require_org_match(db, org_id, Item, line.item_id, "Item")
            _require_org_match(db, org_id, CostCenter, line.cost_center_id, "Cost center")
            _require_org_match(db, org_id, Project, line.project_id, "Project")
            _require_org_match(db, org_id, ReportingSegment, line.segment_id, "Reporting segment")
            _require_org_match(
                db,
                org_id,
                PerformanceObligation,
                line.performance_obligation_id,
                "Performance obligation",
            )

            line_amount = line.quantity * line.unit_price - line.discount_amount
            subtotal += line_amount
            tax_total += line.tax_amount

        total_amount = subtotal + tax_total

        # Handle credit notes
        if input.invoice_type == InvoiceType.CREDIT_NOTE:
            total_amount = -abs(total_amount)
            subtotal = -abs(subtotal)
            tax_total = -abs(tax_total)

        # Calculate functional currency amount
        exchange_rate = input.exchange_rate or Decimal("1.0")
        functional_amount = total_amount * exchange_rate

        # Generate invoice number
        invoice_number = SequenceService.get_next_number(
            db, org_id, SequenceType.INVOICE
        )

        # Create invoice
        invoice = Invoice(
            organization_id=org_id,
            customer_id=customer_id,
            contract_id=input.contract_id,
            invoice_number=invoice_number,
            invoice_type=input.invoice_type,
            invoice_date=input.invoice_date,
            due_date=input.due_date,
            currency_code=input.currency_code,
            exchange_rate=exchange_rate,
            exchange_rate_type_id=input.exchange_rate_type_id,
            subtotal=subtotal,
            tax_amount=tax_total,
            total_amount=total_amount,
            functional_currency_amount=functional_amount,
            status=InvoiceStatus.DRAFT,
            payment_terms_id=input.payment_terms_id,
            billing_address=input.billing_address or customer.billing_address,
            shipping_address=input.shipping_address or customer.shipping_address,
            notes=input.notes,
            internal_notes=input.internal_notes,
            ar_control_account_id=customer.ar_control_account_id,
            is_intercompany=input.is_intercompany,
            intercompany_org_id=input.intercompany_org_id,
            created_by_user_id=user_id,
            correlation_id=input.correlation_id or str(uuid_lib.uuid4()),
        )

        db.add(invoice)
        db.flush()

        # Create lines
        for idx, line_input in enumerate(input.lines, start=1):
            line_amount = line_input.quantity * line_input.unit_price - line_input.discount_amount
            if input.invoice_type == InvoiceType.CREDIT_NOTE:
                line_amount = -abs(line_amount)

            line = InvoiceLine(
                invoice_id=invoice.invoice_id,
                line_number=idx,
                description=line_input.description,
                quantity=line_input.quantity,
                unit_price=line_input.unit_price,
                line_amount=line_amount,
                discount_amount=line_input.discount_amount,
                tax_code_id=line_input.tax_code_id,
                tax_amount=line_input.tax_amount,
                revenue_account_id=line_input.revenue_account_id
                or customer.default_revenue_account_id,
                item_id=line_input.item_id,
                cost_center_id=line_input.cost_center_id,
                project_id=line_input.project_id,
                segment_id=line_input.segment_id,
                obligation_id=line_input.performance_obligation_id,
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
    ) -> Invoice:
        """Submit an invoice for approval."""
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(submitted_by_user_id)

        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        if invoice.status != InvoiceStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot submit invoice with status '{invoice.status.value}'",
            )

        invoice.status = InvoiceStatus.SUBMITTED
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
    ) -> Invoice:
        """Approve a submitted invoice."""
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(approved_by_user_id)

        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        if invoice.status != InvoiceStatus.SUBMITTED:
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

        invoice.status = InvoiceStatus.APPROVED
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
    ) -> Invoice:
        """Post an approved invoice to the general ledger."""
        from app.services.ifrs.ar.ar_posting_adapter import ARPostingAdapter

        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(posted_by_user_id)

        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        if invoice.status != InvoiceStatus.APPROVED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot post invoice with status '{invoice.status.value}'",
            )

        # Use ARPostingAdapter to create GL entries
        result = ARPostingAdapter.post_invoice(
            db=db,
            organization_id=org_id,
            invoice_id=inv_id,
            posting_date=posting_date or invoice.invoice_date,
            posted_by_user_id=user_id,
        )

        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)

        # Update invoice status
        invoice.status = InvoiceStatus.POSTED
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
    ) -> Invoice:
        """Void an invoice."""
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(voided_by_user_id)

        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        non_voidable = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.PAID,
            InvoiceStatus.VOID,
        ]

        if invoice.status in non_voidable:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot void invoice with status '{invoice.status.value}'",
            )

        invoice.status = InvoiceStatus.VOID
        invoice.voided_by_user_id = user_id
        invoice.voided_at = datetime.now(timezone.utc)
        invoice.void_reason = reason

        db.commit()
        db.refresh(invoice)

        return invoice

    @staticmethod
    def mark_overdue(
        db: Session,
        organization_id: UUID,
        as_of_date: Optional[date] = None,
    ) -> int:
        """
        Mark overdue invoices.

        Args:
            db: Database session
            organization_id: Organization scope
            as_of_date: Date to check against (default: today)

        Returns:
            Number of invoices marked overdue
        """
        org_id = coerce_uuid(organization_id)
        ref_date = as_of_date or date.today()

        # Find posted invoices past due date
        invoices = (
            db.query(Invoice)
            .filter(
                and_(
                    Invoice.organization_id == org_id,
                    Invoice.status.in_([InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID]),
                    Invoice.due_date < ref_date,
                )
            )
            .all()
        )

        count = 0
        for invoice in invoices:
            if invoice.balance_due > Decimal("0"):
                invoice.status = InvoiceStatus.OVERDUE
                count += 1

        db.commit()
        return count

    @staticmethod
    def record_payment(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        payment_amount: Decimal,
    ) -> Invoice:
        """Record a payment against an invoice."""
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        payable_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]

        if invoice.status not in payable_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot pay invoice with status '{invoice.status.value}'",
            )

        invoice.amount_paid += payment_amount

        if invoice.amount_paid >= invoice.total_amount:
            invoice.status = InvoiceStatus.PAID
        else:
            invoice.status = InvoiceStatus.PARTIALLY_PAID

        db.commit()
        db.refresh(invoice)

        return invoice

    @staticmethod
    def get(
        db: Session,
        organization_id: UUID,
        invoice_id: str,
    ) -> Invoice:
        """Get an invoice by ID."""
        org_id = coerce_uuid(organization_id)
        invoice = db.get(Invoice, coerce_uuid(invoice_id))
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")
        return invoice

    @staticmethod
    def get_invoice_lines(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
    ) -> list[InvoiceLine]:
        """Get lines for an invoice."""
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        return (
            db.query(InvoiceLine)
            .filter(InvoiceLine.invoice_id == inv_id)
            .order_by(InvoiceLine.line_number)
            .all()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: str,
        customer_id: Optional[str] = None,
        status: Optional[InvoiceStatus] = None,
        invoice_type: Optional[InvoiceType] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        overdue_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Invoice]:
        """List invoices with optional filters."""
        if not organization_id:
            raise HTTPException(status_code=400, detail="organization_id is required")

        org_id = coerce_uuid(organization_id)
        query = db.query(Invoice).filter(Invoice.organization_id == org_id)

        if customer_id:
            query = query.filter(Invoice.customer_id == coerce_uuid(customer_id))

        if status:
            query = query.filter(Invoice.status == status)

        if invoice_type:
            query = query.filter(Invoice.invoice_type == invoice_type)

        if from_date:
            query = query.filter(Invoice.invoice_date >= from_date)

        if to_date:
            query = query.filter(Invoice.invoice_date <= to_date)

        if overdue_only:
            query = query.filter(
                and_(
                    Invoice.due_date < date.today(),
                    Invoice.status.in_([
                        InvoiceStatus.POSTED,
                        InvoiceStatus.PARTIALLY_PAID,
                        InvoiceStatus.OVERDUE,
                    ]),
                )
            )

        query = query.order_by(Invoice.invoice_date.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
ar_invoice_service = ARInvoiceService()
