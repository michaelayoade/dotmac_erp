"""
ARInvoiceService - AR invoice lifecycle management.

Manages creation, approval workflow, posting, and payment tracking.
"""

from __future__ import annotations

import logging
import uuid as uuid_lib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.finance.ar.contract import Contract
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.invoice_line_tax import InvoiceLineTax
from app.models.finance.ar.payment_terms import PaymentTerms
from app.models.finance.ar.performance_obligation import PerformanceObligation
from app.models.finance.core_config.numbering_sequence import SequenceType
from app.models.finance.core_org.cost_center import CostCenter
from app.models.finance.core_org.project import Project
from app.models.finance.core_org.reporting_segment import ReportingSegment
from app.models.finance.gl.account import Account
from app.models.finance.tax.tax_code import TaxCode
from app.models.inventory.item import Item
from app.models.finance.audit.audit_log import AuditAction
from app.services.audit_dispatcher import fire_audit_event
from app.services.common import coerce_uuid
from app.services.finance.platform.sequence import SequenceService
from app.services.finance.tax.tax_calculation import (
    LineCalculationResult,
    TaxCalculationService,
)
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class ARInvoiceLineInput:
    """Input for an AR invoice line."""

    description: str
    quantity: Decimal
    unit_price: Decimal
    revenue_account_id: Optional[UUID] = None
    item_id: Optional[UUID] = None
    # Multiple tax codes per line (replaces single tax_code_id)
    tax_code_ids: list[UUID] = field(default_factory=list)
    # Keep legacy field for backwards compatibility during transition
    tax_code_id: Optional[UUID] = None
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


def _batch_validate_org_refs(
    db: Session,
    organization_id: UUID,
    validations: list[tuple[type, set[UUID], str]],
) -> None:
    """Batch validate that referenced records belong to the organization.

    This is an optimization over individual _require_org_match calls.
    Instead of N queries (one per reference), we execute one query per model type.

    Args:
        db: Database session
        organization_id: Organization scope
        validations: List of (model_class, set_of_ids, label) tuples

    Raises:
        HTTPException: If any referenced record is not found or doesn't belong to org
    """
    for model, ids, label in validations:
        if not ids:
            continue

        # Convert all IDs to proper UUIDs
        uuid_ids = {coerce_uuid(id_) for id_ in ids if id_}
        if not uuid_ids:
            continue

        # Get the primary key column dynamically (some models use 'id', others use '<name>_id')
        mapper: Any = sa_inspect(model)
        pk_cols = mapper.primary_key
        if not pk_cols:
            raise ValueError(f"Model {model.__name__} has no primary key")
        pk_attr = pk_cols[0].name  # e.g., 'account_id', 'invoice_id', etc.
        pk_column = getattr(model, pk_attr)

        # Single query to get all records of this type
        records: list[Any] = db.query(model).filter(pk_column.in_(uuid_ids)).all()
        found_ids = set()

        for record in records:
            # Check organization scope
            if getattr(record, "organization_id", None) != organization_id:
                raise HTTPException(status_code=404, detail=f"{label} not found")
            found_ids.add(getattr(record, pk_attr))

        # Check for any missing IDs
        missing = uuid_ids - found_ids
        if missing:
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

        # Collect all referenced IDs for batch validation (reduces N+1 queries)
        contract_ids: set[UUID] = set()
        payment_terms_ids: set[UUID] = set()
        account_ids: set[UUID] = set()
        item_ids: set[UUID] = set()
        cost_center_ids: set[UUID] = set()
        project_ids: set[UUID] = set()
        segment_ids: set[UUID] = set()
        obligation_ids: set[UUID] = set()
        tax_code_ids: set[UUID] = set()

        if input.contract_id:
            contract_ids.add(input.contract_id)
        if input.payment_terms_id:
            payment_terms_ids.add(input.payment_terms_id)

        # Collect all line-level references
        for line in input.lines:
            if not line.tax_code_ids and not line.tax_code_id:
                raise HTTPException(
                    status_code=400,
                    detail="Tax code is required for each invoice line",
                )
            if line.revenue_account_id:
                account_ids.add(line.revenue_account_id)
            if line.item_id:
                item_ids.add(line.item_id)
            if line.cost_center_id:
                cost_center_ids.add(line.cost_center_id)
            if line.project_id:
                project_ids.add(line.project_id)
            if line.segment_id:
                segment_ids.add(line.segment_id)
            if line.performance_obligation_id:
                obligation_ids.add(line.performance_obligation_id)
            # Collect tax codes
            tax_code_ids.update(line.tax_code_ids)
            if line.tax_code_id and not line.tax_code_ids:
                tax_code_ids.add(line.tax_code_id)

        # Batch validate all references (one query per model type instead of N queries)
        _batch_validate_org_refs(
            db,
            org_id,
            [
                (Contract, contract_ids, "Contract"),
                (PaymentTerms, payment_terms_ids, "Payment terms"),
                (Account, account_ids, "Revenue account"),
                (Item, item_ids, "Item"),
                (CostCenter, cost_center_ids, "Cost center"),
                (Project, project_ids, "Project"),
                (ReportingSegment, segment_ids, "Reporting segment"),
                (PerformanceObligation, obligation_ids, "Performance obligation"),
                (TaxCode, tax_code_ids, "Tax code"),
            ],
        )

        # Calculate totals with auto tax calculation
        subtotal = Decimal("0")
        tax_total = Decimal("0")
        discount_total = Decimal("0")

        # Pre-calculate taxes for all lines
        line_tax_results: list[Optional[LineCalculationResult]] = []
        for line in input.lines:
            line_amount = line.quantity * line.unit_price - line.discount_amount
            subtotal += line_amount

            # Build list of tax codes (support both new and legacy format)
            effective_tax_codes = list(line.tax_code_ids) if line.tax_code_ids else []
            if line.tax_code_id and line.tax_code_id not in effective_tax_codes:
                effective_tax_codes.append(line.tax_code_id)

            # Calculate taxes using centralized service
            if effective_tax_codes:
                line_tax_result = TaxCalculationService.calculate_line_taxes(
                    db=db,
                    organization_id=org_id,
                    line_amount=line_amount,
                    tax_code_ids=effective_tax_codes,
                    transaction_date=input.invoice_date,
                )
                line_tax_results.append(line_tax_result)
                tax_total += line_tax_result.total_tax
            else:
                line_tax_results.append(None)

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

        # Create lines and their tax records
        for idx, line_input in enumerate(input.lines, start=1):
            line_amount = (
                line_input.quantity * line_input.unit_price - line_input.discount_amount
            )
            if input.invoice_type == InvoiceType.CREDIT_NOTE:
                line_amount = -abs(line_amount)

            # Get the pre-calculated tax result for this line
            tax_result = line_tax_results[idx - 1]
            line_tax_total = tax_result.total_tax if tax_result else Decimal("0")
            if input.invoice_type == InvoiceType.CREDIT_NOTE and tax_result:
                line_tax_total = -abs(line_tax_total)

            # Get primary tax code ID for legacy compatibility (first tax code)
            effective_tax_codes = (
                list(line_input.tax_code_ids) if line_input.tax_code_ids else []
            )
            if (
                line_input.tax_code_id
                and line_input.tax_code_id not in effective_tax_codes
            ):
                effective_tax_codes.append(line_input.tax_code_id)
            primary_tax_code_id = (
                effective_tax_codes[0] if effective_tax_codes else None
            )

            invoice_line = InvoiceLine(
                invoice_id=invoice.invoice_id,
                line_number=idx,
                description=line_input.description,
                quantity=line_input.quantity,
                unit_price=line_input.unit_price,
                line_amount=line_amount,
                discount_amount=line_input.discount_amount,
                tax_code_id=primary_tax_code_id,  # Primary tax for backwards compatibility
                tax_amount=line_tax_total,  # Total of all taxes on this line
                revenue_account_id=line_input.revenue_account_id
                or customer.default_revenue_account_id,
                item_id=line_input.item_id,
                cost_center_id=line_input.cost_center_id,
                project_id=line_input.project_id,
                segment_id=line_input.segment_id,
                obligation_id=line_input.performance_obligation_id,
            )
            db.add(invoice_line)
            db.flush()  # Get line_id for tax records

            # Create InvoiceLineTax records for each tax
            if tax_result and tax_result.taxes:
                for tax_detail in tax_result.taxes:
                    tax_amount = tax_detail.tax_amount
                    base_amount = tax_detail.base_amount
                    if input.invoice_type == InvoiceType.CREDIT_NOTE:
                        tax_amount = -abs(tax_amount)
                        base_amount = -abs(base_amount)

                    line_tax = InvoiceLineTax(
                        line_id=invoice_line.line_id,
                        tax_code_id=tax_detail.tax_code_id,
                        base_amount=base_amount,
                        tax_rate=tax_detail.tax_rate,
                        tax_amount=tax_amount,
                        is_inclusive=tax_detail.is_inclusive,
                        sequence=tax_detail.sequence,
                    )
                    db.add(line_tax)

        db.commit()
        db.refresh(invoice)

        fire_audit_event(
            db=db,
            organization_id=org_id,
            table_schema="ar",
            table_name="invoice",
            record_id=str(invoice.invoice_id),
            action=AuditAction.INSERT,
            new_values={
                "invoice_number": invoice.invoice_number,
                "customer_id": str(customer_id),
                "total_amount": str(invoice.total_amount),
                "currency_code": invoice.currency_code,
            },
            user_id=user_id,
        )

        return invoice

    @staticmethod
    def update_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        input: ARInvoiceInput,
        updated_by_user_id: UUID,
    ) -> Invoice:
        """
        Update an existing AR invoice (only DRAFT status).

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to update
            input: Updated invoice input data
            updated_by_user_id: User updating the invoice

        Returns:
            Updated Invoice
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(updated_by_user_id)
        customer_id = coerce_uuid(input.customer_id)

        # Get existing invoice
        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        if invoice.status != InvoiceStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot edit invoice with status '{invoice.status.value}'",
            )

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

        # Delete existing lines and their tax records
        existing_lines = (
            db.query(InvoiceLine).filter(InvoiceLine.invoice_id == inv_id).all()
        )
        for line in existing_lines:
            db.query(InvoiceLineTax).filter(
                InvoiceLineTax.line_id == line.line_id
            ).delete()
        db.query(InvoiceLine).filter(InvoiceLine.invoice_id == inv_id).delete()

        # Calculate totals from lines
        subtotal = Decimal("0")
        tax_total = Decimal("0")
        line_tax_results: list[Optional[LineCalculationResult]] = []

        for line_input in input.lines:
            line_amount = (
                line_input.quantity * line_input.unit_price - line_input.discount_amount
            )
            subtotal += line_amount

            # Determine tax codes: prefer new multi-tax field, fall back to legacy single field
            effective_tax_codes = (
                list(line_input.tax_code_ids) if line_input.tax_code_ids else []
            )
            if (
                line_input.tax_code_id
                and line_input.tax_code_id not in effective_tax_codes
            ):
                effective_tax_codes.append(line_input.tax_code_id)

            if effective_tax_codes:
                line_tax_result = TaxCalculationService.calculate_line_taxes(
                    db=db,
                    organization_id=org_id,
                    line_amount=line_amount,
                    tax_code_ids=effective_tax_codes,
                    transaction_date=input.invoice_date,
                )
                line_tax_results.append(line_tax_result)
                tax_total += line_tax_result.total_tax
            else:
                line_tax_results.append(None)

        total_amount = subtotal + tax_total

        # Handle credit notes
        if input.invoice_type == InvoiceType.CREDIT_NOTE:
            total_amount = -abs(total_amount)
            subtotal = -abs(subtotal)
            tax_total = -abs(tax_total)

        # Calculate functional currency amount
        exchange_rate = input.exchange_rate or Decimal("1.0")
        functional_amount = total_amount * exchange_rate

        # Update invoice fields
        invoice.customer_id = customer_id
        invoice.contract_id = input.contract_id
        invoice.invoice_type = input.invoice_type
        invoice.invoice_date = input.invoice_date
        invoice.due_date = input.due_date
        invoice.currency_code = input.currency_code
        invoice.exchange_rate = exchange_rate
        invoice.exchange_rate_type_id = input.exchange_rate_type_id
        invoice.subtotal = subtotal
        invoice.tax_amount = tax_total
        invoice.total_amount = total_amount
        invoice.functional_currency_amount = functional_amount
        invoice.payment_terms_id = input.payment_terms_id
        invoice.billing_address = input.billing_address or customer.billing_address
        invoice.shipping_address = input.shipping_address or customer.shipping_address
        invoice.notes = input.notes
        invoice.internal_notes = input.internal_notes
        invoice.ar_control_account_id = customer.ar_control_account_id
        invoice.is_intercompany = input.is_intercompany
        invoice.intercompany_org_id = input.intercompany_org_id
        invoice.updated_at = datetime.now(timezone.utc)

        db.flush()

        # Create new lines and their tax records
        for idx, line_input in enumerate(input.lines, start=1):
            line_amount = (
                line_input.quantity * line_input.unit_price - line_input.discount_amount
            )
            if input.invoice_type == InvoiceType.CREDIT_NOTE:
                line_amount = -abs(line_amount)

            tax_result = line_tax_results[idx - 1]
            line_tax_total = tax_result.total_tax if tax_result else Decimal("0")
            if input.invoice_type == InvoiceType.CREDIT_NOTE and tax_result:
                line_tax_total = -abs(line_tax_total)

            effective_tax_codes = (
                list(line_input.tax_code_ids) if line_input.tax_code_ids else []
            )
            if (
                line_input.tax_code_id
                and line_input.tax_code_id not in effective_tax_codes
            ):
                effective_tax_codes.append(line_input.tax_code_id)
            primary_tax_code_id = (
                effective_tax_codes[0] if effective_tax_codes else None
            )

            line = InvoiceLine(
                invoice_id=invoice.invoice_id,
                line_number=idx,
                description=line_input.description,
                quantity=line_input.quantity,
                unit_price=line_input.unit_price,
                line_amount=line_amount,
                discount_amount=line_input.discount_amount,
                tax_code_id=primary_tax_code_id,
                tax_amount=line_tax_total,
                revenue_account_id=line_input.revenue_account_id
                or customer.default_revenue_account_id,
                item_id=line_input.item_id,
                cost_center_id=line_input.cost_center_id,
                project_id=line_input.project_id,
                segment_id=line_input.segment_id,
                obligation_id=line_input.performance_obligation_id,
            )
            db.add(line)
            db.flush()

            # Create InvoiceLineTax records for each tax
            if tax_result and tax_result.taxes:
                for tax_detail in tax_result.taxes:
                    tax_amount = tax_detail.tax_amount
                    base_amount = tax_detail.base_amount
                    if input.invoice_type == InvoiceType.CREDIT_NOTE:
                        tax_amount = -abs(tax_amount)
                        base_amount = -abs(base_amount)

                    line_tax = InvoiceLineTax(
                        line_id=line.line_id,
                        tax_code_id=tax_detail.tax_code_id,
                        base_amount=base_amount,
                        tax_rate=tax_detail.tax_rate,
                        tax_amount=tax_amount,
                        is_inclusive=tax_detail.is_inclusive,
                        sequence=tax_detail.sequence,
                    )
                    db.add(line_tax)

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

        # Fire workflow automation event
        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=org_id,
                entity_type="INVOICE",
                entity_id=inv_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": "DRAFT"},
                new_values={"status": "SUBMITTED"},
                user_id=user_id,
            )
        except Exception:
            pass

        fire_audit_event(
            db=db,
            organization_id=org_id,
            table_schema="ar",
            table_name="invoice",
            record_id=str(inv_id),
            action=AuditAction.UPDATE,
            old_values={"status": "DRAFT"},
            new_values={"status": "SUBMITTED"},
            user_id=user_id,
        )

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

        # Fire workflow automation event
        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=org_id,
                entity_type="INVOICE",
                entity_id=inv_id,
                event="ON_APPROVAL",
                old_values={"status": "SUBMITTED"},
                new_values={"status": "APPROVED"},
                user_id=user_id,
            )
        except Exception:
            pass

        fire_audit_event(
            db=db,
            organization_id=org_id,
            table_schema="ar",
            table_name="invoice",
            record_id=str(inv_id),
            action=AuditAction.UPDATE,
            old_values={"status": "SUBMITTED"},
            new_values={"status": "APPROVED"},
            user_id=user_id,
        )

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
        from app.services.finance.ar.ar_posting_adapter import ARPostingAdapter

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

        # Fire workflow automation event
        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=org_id,
                entity_type="INVOICE",
                entity_id=inv_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": "APPROVED"},
                new_values={"status": "POSTED"},
                user_id=user_id,
            )
        except Exception:
            pass

        fire_audit_event(
            db=db,
            organization_id=org_id,
            table_schema="ar",
            table_name="invoice",
            record_id=str(inv_id),
            action=AuditAction.UPDATE,
            old_values={"status": "APPROVED"},
            new_values={"status": "POSTED"},
            user_id=user_id,
        )

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

        old_status = invoice.status.value
        invoice.status = InvoiceStatus.VOID
        invoice.voided_by_user_id = user_id
        invoice.voided_at = datetime.now(timezone.utc)
        invoice.void_reason = reason

        fire_audit_event(
            db=db,
            organization_id=org_id,
            table_schema="ar",
            table_name="invoice",
            record_id=str(inv_id),
            action=AuditAction.UPDATE,
            old_values={"status": old_status},
            new_values={"status": "VOID"},
            user_id=user_id,
            reason=reason,
        )

        db.commit()
        db.refresh(invoice)

        return invoice

    @staticmethod
    def cancel_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        cancelled_by_user_id: UUID,
        reason: Optional[str] = None,
    ) -> Invoice:
        """
        Cancel an invoice, returning it to DRAFT status for editing.

        Only SUBMITTED or APPROVED invoices can be cancelled.
        Posted invoices must be voided instead.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to cancel
            cancelled_by_user_id: User cancelling
            reason: Optional reason for cancellation

        Returns:
            Updated invoice in DRAFT status
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        cancellable = [InvoiceStatus.SUBMITTED, InvoiceStatus.APPROVED]

        if invoice.status not in cancellable:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel invoice with status '{invoice.status.value}'. Only SUBMITTED or APPROVED invoices can be cancelled.",
            )

        old_status = invoice.status.value
        invoice.status = InvoiceStatus.DRAFT
        invoice.posting_status = "PENDING"
        # Clear approval fields
        invoice.approved_by_user_id = None
        invoice.approved_at = None

        # Fire workflow automation event
        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=org_id,
                entity_type="INVOICE",
                entity_id=inv_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": old_status},
                new_values={"status": "DRAFT"},
                user_id=coerce_uuid(cancelled_by_user_id),
            )
        except Exception:
            pass

        fire_audit_event(
            db=db,
            organization_id=org_id,
            table_schema="ar",
            table_name="invoice",
            record_id=str(inv_id),
            action=AuditAction.UPDATE,
            old_values={"status": old_status},
            new_values={"status": "DRAFT"},
            user_id=coerce_uuid(cancelled_by_user_id),
            reason=reason,
        )

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
                    Invoice.status.in_(
                        [InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID]
                    ),
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
        include_lines: bool = False,
    ) -> list[Invoice]:
        """List invoices with optional filters.

        Args:
            db: Database session
            organization_id: Organization scope
            customer_id: Optional filter by customer
            status: Optional filter by status
            invoice_type: Optional filter by type
            from_date: Optional filter by start date
            to_date: Optional filter by end date
            overdue_only: Only return overdue invoices
            limit: Max results (default 50)
            offset: Skip first N results
            include_lines: If True, eager load invoice lines (default False)

        Returns:
            List of invoices with customer relationship eager-loaded
        """
        if not organization_id:
            raise HTTPException(status_code=400, detail="organization_id is required")

        org_id = coerce_uuid(organization_id)

        # Build query with eager loading to prevent N+1 queries
        # joinedload for single object (customer), selectinload for collections (lines)
        query = (
            db.query(Invoice)
            .options(joinedload(Invoice.customer))  # Eager load customer (1:1)
            .filter(Invoice.organization_id == org_id)
        )

        # Optionally eager load lines (heavier, only when needed)
        if include_lines:
            query = query.options(selectinload(Invoice.lines))

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
                    Invoice.status.in_(
                        [
                            InvoiceStatus.POSTED,
                            InvoiceStatus.PARTIALLY_PAID,
                            InvoiceStatus.OVERDUE,
                        ]
                    ),
                )
            )

        query = query.order_by(Invoice.invoice_date.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
ar_invoice_service = ARInvoiceService()
