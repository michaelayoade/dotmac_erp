"""
ARInvoiceService - AR invoice lifecycle management.

Manages creation, approval workflow, posting, and payment tracking.
"""

from __future__ import annotations

import logging
import uuid as uuid_lib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.finance.ar.contract import Contract
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.invoice_line_tax import InvoiceLineTax
from app.models.finance.ar.payment_allocation import PaymentAllocation
from app.models.finance.ar.payment_terms import PaymentTerms
from app.models.finance.ar.performance_obligation import PerformanceObligation
from app.models.finance.audit.audit_log import AuditAction
from app.models.finance.core_config.numbering_sequence import SequenceType
from app.models.finance.core_org.cost_center import CostCenter
from app.models.finance.core_org.project import Project
from app.models.finance.core_org.reporting_segment import ReportingSegment
from app.models.finance.gl.account import Account
from app.models.finance.tax.tax_code import TaxCode
from app.models.inventory.item import Item
from app.services.audit_dispatcher import fire_audit_event
from app.services.common import NotFoundError, ValidationError, coerce_uuid
from app.services.finance.ar.input_utils import (
    parse_date_str,
    parse_decimal,
    parse_json_list,
    require_uuid,
    resolve_currency_code,
)
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
    revenue_account_id: UUID | None = None
    item_id: UUID | None = None
    # Multiple tax codes per line (replaces single tax_code_id)
    tax_code_ids: list[UUID] = field(default_factory=list)
    # Keep legacy field for backwards compatibility during transition
    tax_code_id: UUID | None = None
    discount_amount: Decimal = Decimal("0")
    cost_center_id: UUID | None = None
    project_id: UUID | None = None
    segment_id: UUID | None = None
    contract_id: UUID | None = None
    performance_obligation_id: UUID | None = None


@dataclass
class ARInvoiceInput:
    """Input for creating/updating an AR invoice."""

    customer_id: UUID
    invoice_type: InvoiceType
    invoice_date: date
    due_date: date
    currency_code: str
    lines: list[ARInvoiceLineInput] = field(default_factory=list)
    contract_id: UUID | None = None
    exchange_rate: Decimal | None = None
    exchange_rate_type_id: UUID | None = None
    payment_terms_id: UUID | None = None
    billing_address: dict[str, Any] | None = None
    shipping_address: dict[str, Any] | None = None
    notes: str | None = None
    internal_notes: str | None = None
    is_intercompany: bool = False
    intercompany_org_id: UUID | None = None
    correlation_id: str | None = None


def _require_org_match(
    db: Session,
    organization_id: UUID,
    model: type,
    record_id: UUID | None,
    label: str,
) -> None:
    """Ensure a referenced record belongs to the organization."""
    if not record_id:
        return
    record = db.get(model, coerce_uuid(record_id))
    if not record or getattr(record, "organization_id", None) != organization_id:
        raise NotFoundError(f"{label} not found")


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
            NotFoundError: If any referenced record is not found or doesn't belong to org
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
        records: list[Any] = db.scalars(
            select(model).where(pk_column.in_(uuid_ids))
        ).all()
        found_ids = set()

        for record in records:
            # Check organization scope
            if getattr(record, "organization_id", None) != organization_id:
                raise NotFoundError(f"{label} not found")
            found_ids.add(getattr(record, pk_attr))

        # Check for any missing IDs
        missing = uuid_ids - found_ids
        if missing:
            raise NotFoundError(f"{label} not found")


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
            raise NotFoundError("Customer not found")

        if not customer.is_active:
            raise ValidationError("Customer is not active")

        # Auto-detect fiscal position and remap taxes/accounts
        from app.services.finance.tax.fiscal_position_service import (
            FiscalPositionService,
        )

        fp_service = FiscalPositionService(db)
        customer_type = getattr(customer, "customer_type", None)
        customer_classification = (
            customer_type.value
            if customer_type is not None and hasattr(customer_type, "value")
            else customer_type
        )
        fiscal_position = fp_service.get_for_partner(
            organization_id=org_id,
            partner_type="customer",
            partner_classification=customer_classification,
        )
        if fiscal_position:
            for line in input.lines:
                if line.tax_code_ids:
                    line.tax_code_ids = fp_service.map_taxes(
                        fiscal_position, line.tax_code_ids
                    )
                if line.revenue_account_id:
                    line.revenue_account_id = fp_service.map_account(
                        fiscal_position, line.revenue_account_id
                    )

        # Validate lines
        if not input.lines:
            raise ValidationError("Invoice must have at least one line")

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
        Decimal("0")

        # Pre-calculate taxes for all lines
        line_tax_results: list[LineCalculationResult | None] = []
        for line in input.lines:
            gross_line_amount = line.quantity * line.unit_price - line.discount_amount

            # Build list of tax codes (support both new and legacy format)
            effective_tax_codes = list(line.tax_code_ids) if line.tax_code_ids else []
            if line.tax_code_id and line.tax_code_id not in effective_tax_codes:
                effective_tax_codes.append(line.tax_code_id)

            # Calculate taxes using centralized service
            if effective_tax_codes:
                line_tax_result = TaxCalculationService.calculate_line_taxes(
                    db=db,
                    organization_id=org_id,
                    line_amount=gross_line_amount,
                    tax_code_ids=effective_tax_codes,
                    transaction_date=input.invoice_date,
                )
                line_tax_results.append(line_tax_result)
                # Use net_amount for subtotal (handles inclusive tax extraction)
                subtotal += line_tax_result.net_amount
                tax_total += line_tax_result.total_tax
            else:
                line_tax_results.append(None)
                subtotal += gross_line_amount

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
            gross_line_amount = (
                line_input.quantity * line_input.unit_price - line_input.discount_amount
            )

            # Get the pre-calculated tax result for this line
            tax_result = line_tax_results[idx - 1]
            # Use net_amount so line_amount reflects revenue (after inclusive tax extraction)
            net_line_amount = tax_result.net_amount if tax_result else gross_line_amount
            line_tax_total = tax_result.total_tax if tax_result else Decimal("0")

            if input.invoice_type == InvoiceType.CREDIT_NOTE:
                net_line_amount = -abs(net_line_amount)
                if tax_result:
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
                line_amount=net_line_amount,
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

        try:
            from app.services.hooks import emit_hook_event
            from app.services.hooks.events import AR_INVOICE_CREATED

            emit_hook_event(
                db,
                event_name=AR_INVOICE_CREATED,
                organization_id=org_id,
                entity_type="Invoice",
                entity_id=invoice.invoice_id,
                actor_user_id=user_id,
                payload={
                    "invoice_id": str(invoice.invoice_id),
                    "invoice_number": invoice.invoice_number,
                    "invoice_type": invoice.invoice_type.value,
                    "status": invoice.status.value,
                    "customer_id": str(customer_id),
                    "total_amount": str(invoice.total_amount),
                    "currency_code": invoice.currency_code,
                },
            )
        except Exception:
            logger.exception(
                "Failed to emit ar.invoice.created hook for invoice %s",
                invoice.invoice_id,
            )

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
        coerce_uuid(updated_by_user_id)
        customer_id = coerce_uuid(input.customer_id)

        # Get existing invoice
        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise NotFoundError("Invoice not found")

        if invoice.status != InvoiceStatus.DRAFT:
            raise ValidationError(
                f"Cannot edit invoice with status '{invoice.status.value}'"
            )

        # Validate customer
        customer = db.get(Customer, customer_id)
        if not customer or customer.organization_id != org_id:
            raise NotFoundError("Customer not found")

        if not customer.is_active:
            raise ValidationError("Customer is not active")

        # Validate lines
        if not input.lines:
            raise ValidationError("Invoice must have at least one line")

        # Delete existing lines and their tax records
        existing_lines = db.scalars(
            select(InvoiceLine).where(InvoiceLine.invoice_id == inv_id)
        ).all()
        for line in existing_lines:
            db.execute(
                delete(InvoiceLineTax).where(InvoiceLineTax.line_id == line.line_id)
            )
        db.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id == inv_id))

        # Calculate totals from lines
        subtotal = Decimal("0")
        tax_total = Decimal("0")
        line_tax_results: list[LineCalculationResult | None] = []

        for line_input in input.lines:
            gross_line_amount = (
                line_input.quantity * line_input.unit_price - line_input.discount_amount
            )

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
                    line_amount=gross_line_amount,
                    tax_code_ids=effective_tax_codes,
                    transaction_date=input.invoice_date,
                )
                line_tax_results.append(line_tax_result)
                subtotal += line_tax_result.net_amount
                tax_total += line_tax_result.total_tax
            else:
                line_tax_results.append(None)
                subtotal += gross_line_amount

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
        invoice.updated_at = datetime.now(UTC)

        db.flush()

        # Create new lines and their tax records
        for idx, line_input in enumerate(input.lines, start=1):
            # Get the pre-calculated tax result for this line
            tax_result = line_tax_results[idx - 1]
            gross_line_amount = (
                line_input.quantity * line_input.unit_price - line_input.discount_amount
            )
            # Use net_amount so line_amount reflects revenue (after inclusive tax extraction)
            net_line_amount = tax_result.net_amount if tax_result else gross_line_amount
            line_tax_total = tax_result.total_tax if tax_result else Decimal("0")

            if input.invoice_type == InvoiceType.CREDIT_NOTE:
                net_line_amount = -abs(net_line_amount)
                if tax_result:
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
                line_amount=net_line_amount,
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
    def build_input_from_payload(
        db: Session,
        organization_id: UUID,
        payload: dict,
    ) -> ARInvoiceInput:
        """Build ARInvoiceInput from raw payload (strings or JSON)."""
        lines_data = parse_json_list(payload.get("lines"), "Lines")

        lines: list[ARInvoiceLineInput] = []
        for line in lines_data:
            if not line.get("description"):
                raise ValueError("Line description is required")

            tax_code_ids: list[UUID] = []
            raw_tax_code_ids = line.get("tax_code_ids") or []
            if isinstance(raw_tax_code_ids, list):
                tax_code_ids = [
                    coerce_uuid(tc_id) for tc_id in raw_tax_code_ids if tc_id
                ]
            legacy_tax_code_id = (
                coerce_uuid(line.get("tax_code_id"))
                if line.get("tax_code_id")
                else None
            )

            lines.append(
                ARInvoiceLineInput(
                    description=line.get("description", ""),
                    quantity=parse_decimal(line.get("quantity", 1), "Quantity"),
                    unit_price=parse_decimal(line.get("unit_price", 0), "Unit price"),
                    revenue_account_id=coerce_uuid(line.get("revenue_account_id"))
                    if line.get("revenue_account_id")
                    else None,
                    item_id=coerce_uuid(line.get("item_id"))
                    if line.get("item_id")
                    else None,
                    tax_code_ids=tax_code_ids,
                    tax_code_id=legacy_tax_code_id,
                    cost_center_id=coerce_uuid(line.get("cost_center_id"))
                    if line.get("cost_center_id")
                    else None,
                    project_id=coerce_uuid(line.get("project_id"))
                    if line.get("project_id")
                    else None,
                )
            )

        invoice_date = parse_date_str(payload.get("invoice_date"), "Invoice date")
        invoice_date = invoice_date or date.today()
        due_date = parse_date_str(payload.get("due_date"), "Due date") or invoice_date

        customer_id = require_uuid(payload.get("customer_id"), "Customer")
        currency_code = resolve_currency_code(
            db, coerce_uuid(organization_id), payload.get("currency_code")
        )

        exchange_rate: Decimal | None = None
        if payload.get("exchange_rate") not in (None, ""):
            exchange_rate = parse_decimal(payload.get("exchange_rate"), "Exchange rate")

        return ARInvoiceInput(
            customer_id=customer_id,
            invoice_type=InvoiceType.STANDARD,
            invoice_date=invoice_date,
            due_date=due_date,
            currency_code=currency_code,
            exchange_rate=exchange_rate,
            notes=payload.get("terms"),
            internal_notes=payload.get("notes"),
            lines=lines,
        )

    @staticmethod
    def build_credit_note_input_from_payload(
        db: Session,
        organization_id: UUID,
        payload: dict,
    ) -> ARInvoiceInput:
        """Build ARInvoiceInput from raw payload for credit notes."""
        lines_data = parse_json_list(payload.get("lines"), "Lines")

        lines: list[ARInvoiceLineInput] = []
        for line in lines_data:
            if not line.get("description") or not line.get("revenue_account_id"):
                continue

            tax_code_ids: list[UUID] = []
            raw_tax_code_ids = line.get("tax_code_ids") or []
            if isinstance(raw_tax_code_ids, list):
                tax_code_ids = [
                    coerce_uuid(tc_id) for tc_id in raw_tax_code_ids if tc_id
                ]
            legacy_tax_code_id = (
                coerce_uuid(line.get("tax_code_id"))
                if line.get("tax_code_id")
                else None
            )

            lines.append(
                ARInvoiceLineInput(
                    description=line.get("description", ""),
                    quantity=parse_decimal(line.get("quantity", 1), "Quantity"),
                    unit_price=parse_decimal(line.get("unit_price", 0), "Unit price"),
                    revenue_account_id=coerce_uuid(line.get("revenue_account_id"))
                    if line.get("revenue_account_id")
                    else None,
                    tax_code_ids=tax_code_ids,
                    tax_code_id=legacy_tax_code_id,
                    cost_center_id=coerce_uuid(line.get("cost_center_id"))
                    if line.get("cost_center_id")
                    else None,
                    project_id=coerce_uuid(line.get("project_id"))
                    if line.get("project_id")
                    else None,
                )
            )

        credit_note_date = (
            parse_date_str(payload.get("credit_note_date"), "Credit note date")
            or date.today()
        )

        customer_id = require_uuid(payload.get("customer_id"), "Customer")
        currency_code = resolve_currency_code(
            db, coerce_uuid(organization_id), payload.get("currency_code")
        )

        return ARInvoiceInput(
            customer_id=customer_id,
            invoice_type=InvoiceType.CREDIT_NOTE,
            invoice_date=credit_note_date,
            due_date=credit_note_date,
            currency_code=currency_code,
            notes=payload.get("reason"),
            internal_notes=payload.get("notes"),
            lines=lines,
        )

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
            raise NotFoundError("Invoice not found")

        if invoice.status != InvoiceStatus.DRAFT:
            raise ValidationError(
                f"Cannot submit invoice with status '{invoice.status.value}'"
            )

        invoice.status = InvoiceStatus.SUBMITTED
        invoice.submitted_by_user_id = user_id
        invoice.submitted_at = datetime.now(UTC)

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
            logger.exception("Ignored exception")

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

        try:
            from app.services.hooks import emit_hook_event
            from app.services.hooks.events import AR_INVOICE_SUBMITTED

            hook_invoice_id = getattr(invoice, "invoice_id", inv_id)
            hook_status = getattr(invoice.status, "value", str(invoice.status))
            emit_hook_event(
                db,
                event_name=AR_INVOICE_SUBMITTED,
                organization_id=org_id,
                entity_type="Invoice",
                entity_id=hook_invoice_id,
                actor_user_id=user_id,
                payload={
                    "invoice_id": str(hook_invoice_id),
                    "invoice_number": getattr(invoice, "invoice_number", ""),
                    "status": hook_status,
                    "customer_id": str(getattr(invoice, "customer_id", "")),
                    "total_amount": str(getattr(invoice, "total_amount", "0")),
                    "currency_code": getattr(invoice, "currency_code", ""),
                },
            )
        except Exception:
            logger.exception(
                "Failed to emit ar.invoice.submitted hook for invoice %s",
                getattr(invoice, "invoice_id", inv_id),
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
            raise NotFoundError("Invoice not found")

        if invoice.status != InvoiceStatus.SUBMITTED:
            raise ValidationError(
                f"Cannot approve invoice with status '{invoice.status.value}'"
            )

        # Segregation of Duties check
        if invoice.submitted_by_user_id == user_id:
            raise ValidationError(
                "Segregation of duties violation: submitter cannot approve"
            )

        invoice.status = InvoiceStatus.APPROVED
        invoice.approved_by_user_id = user_id
        invoice.approved_at = datetime.now(UTC)

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
            logger.exception("Ignored exception")

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
        posting_date: date | None = None,
    ) -> Invoice:
        """Post an approved invoice to the general ledger."""
        from app.services.finance.ar.ar_posting_adapter import ARPostingAdapter

        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(posted_by_user_id)

        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise NotFoundError("Invoice not found")

        if invoice.status != InvoiceStatus.APPROVED:
            raise ValidationError(
                f"Cannot post invoice with status '{invoice.status.value}'"
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
            raise ValidationError(result.message)

        # Update invoice status
        invoice.status = InvoiceStatus.POSTED
        invoice.posted_by_user_id = user_id
        invoice.posted_at = datetime.now(UTC)
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
            logger.exception("Ignored exception")

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
    def ensure_gl_posted(
        db: Session,
        invoice: Invoice,
        posted_by_user_id: UUID | None = None,
    ) -> bool:
        """
        Ensure an invoice that is in a posted state has its GL entries.

        For invoices created via sync/import that already have a posted status
        (POSTED, PAID, PARTIALLY_PAID) but were never run through the GL posting
        pipeline, this method idempotently creates the missing journal entries.

        Does NOT change the invoice status — only fills in missing GL entries.

        Args:
            db: Database session
            invoice: Invoice to check and post if needed
            posted_by_user_id: User to attribute posting to (defaults to creator)

        Returns:
            True if GL entries were created, False if already posted or not applicable
        """
        # Only post invoices that are in a posted state but missing GL entries
        postable_statuses = {
            InvoiceStatus.POSTED,
            InvoiceStatus.PAID,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        }
        if invoice.status not in postable_statuses:
            return False
        if invoice.journal_entry_id is not None:
            return False  # Already has GL entries
        # Zero-amount invoices have nothing to post
        if invoice.total_amount == Decimal("0"):
            return False

        try:
            from app.services.finance.ar.ar_posting_adapter import ARPostingAdapter

            user_id = (
                posted_by_user_id
                or invoice.created_by_user_id
                or UUID("00000000-0000-0000-0000-000000000000")
            )
            result = ARPostingAdapter.post_invoice(
                db=db,
                organization_id=invoice.organization_id,
                invoice_id=invoice.invoice_id,
                posting_date=invoice.invoice_date,
                posted_by_user_id=user_id,
                idempotency_key=f"ensure-gl-inv-{invoice.invoice_id}",
            )
            if result.success:
                invoice.journal_entry_id = result.journal_entry_id
                invoice.posting_batch_id = result.posting_batch_id
                invoice.posting_status = "POSTED"
                logger.info(
                    "Auto-posted invoice %s (journal %s)",
                    invoice.invoice_id,
                    result.journal_entry_id,
                )
                return True
            else:
                logger.warning(
                    "Auto-post failed for invoice %s: %s",
                    invoice.invoice_id,
                    result.message,
                )
                return False
        except Exception as e:
            logger.exception("Error auto-posting invoice %s: %s", invoice.invoice_id, e)
            return False

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
            raise NotFoundError("Invoice not found")

        non_voidable = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.PAID,
            InvoiceStatus.VOID,
        ]

        if invoice.status in non_voidable:
            raise ValidationError(
                f"Cannot void invoice with status '{invoice.status.value}'"
            )

        old_status = invoice.status.value
        invoice.status = InvoiceStatus.VOID
        invoice.voided_by_user_id = user_id
        invoice.voided_at = datetime.now(UTC)
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
        reason: str | None = None,
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
            raise NotFoundError("Invoice not found")

        cancellable = [InvoiceStatus.SUBMITTED, InvoiceStatus.APPROVED]

        if invoice.status not in cancellable:
            raise ValidationError(
                f"Cannot cancel invoice with status '{invoice.status.value}'. Only SUBMITTED or APPROVED invoices can be cancelled."
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
            logger.exception("Ignored exception")

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
        as_of_date: date | None = None,
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
        invoices = db.scalars(
            select(Invoice).where(
                and_(
                    Invoice.organization_id == org_id,
                    Invoice.status.in_(
                        [InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID]
                    ),
                    Invoice.due_date < ref_date,
                )
            )
        ).all()

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
            raise NotFoundError("Invoice not found")

        payable_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]

        if invoice.status not in payable_statuses:
            raise ValidationError(
                f"Cannot pay invoice with status '{invoice.status.value}'"
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
            raise NotFoundError("Invoice not found")
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
            raise NotFoundError("Invoice not found")

        return list(
            db.scalars(
                select(InvoiceLine)
                .where(InvoiceLine.invoice_id == inv_id)
                .order_by(InvoiceLine.line_number)
            ).all()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: str,
        customer_id: str | None = None,
        status: InvoiceStatus | None = None,
        invoice_type: InvoiceType | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
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
            raise ValidationError("organization_id is required")

        org_id = coerce_uuid(organization_id)

        # Build query with eager loading to prevent N+1 queries
        # joinedload for single object (customer), selectinload for collections (lines)
        query = select(Invoice).options(
            joinedload(Invoice.customer)
        )  # Eager load customer (1:1)
        query = query.where(Invoice.organization_id == org_id)

        # Optionally eager load lines (heavier, only when needed)
        if include_lines:
            query = query.options(selectinload(Invoice.lines))

        if customer_id:
            query = query.where(Invoice.customer_id == coerce_uuid(customer_id))

        if status:
            query = query.where(Invoice.status == status)

        if invoice_type:
            query = query.where(Invoice.invoice_type == invoice_type)

        if from_date:
            query = query.where(Invoice.invoice_date >= from_date)

        if to_date:
            query = query.where(Invoice.invoice_date <= to_date)

        if overdue_only:
            query = query.where(
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
        return list(db.scalars(query.limit(limit).offset(offset)).unique().all())

    @staticmethod
    def delete_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
    ) -> None:
        """Delete an invoice in DRAFT status."""
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise NotFoundError("Invoice not found")

        if invoice.status != InvoiceStatus.DRAFT:
            raise ValidationError(
                f"Cannot delete invoice with status '{invoice.status.value}'. "
                "Only DRAFT invoices can be deleted."
            )

        allocation_count = (
            db.scalar(
                select(func.count(PaymentAllocation.allocation_id)).where(
                    PaymentAllocation.invoice_id == inv_id
                )
            )
            or 0
        )
        if allocation_count > 0:
            raise ValidationError(
                f"Cannot delete invoice with {allocation_count} payment allocation(s)."
            )

        line_ids = [
            line.line_id
            for line in db.scalars(
                select(InvoiceLine).where(InvoiceLine.invoice_id == inv_id)
            ).all()
        ]
        if line_ids:
            db.execute(
                delete(InvoiceLineTax).where(InvoiceLineTax.line_id.in_(line_ids))
            )
        db.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id == inv_id))
        db.delete(invoice)
        db.flush()
        db.commit()

    @staticmethod
    def delete_credit_note(
        db: Session,
        organization_id: UUID,
        credit_note_id: UUID,
    ) -> None:
        """Delete a credit note (DRAFT only)."""
        org_id = coerce_uuid(organization_id)
        cn_id = coerce_uuid(credit_note_id)

        credit_note = db.get(Invoice, cn_id)
        if not credit_note or credit_note.organization_id != org_id:
            raise NotFoundError("Credit note not found")

        if credit_note.invoice_type != InvoiceType.CREDIT_NOTE:
            raise ValidationError("Document is not a credit note")

        if credit_note.status != InvoiceStatus.DRAFT:
            raise ValidationError(
                f"Cannot delete credit note with status '{credit_note.status.value}'. "
                "Only DRAFT credit notes can be deleted."
            )

        allocation_count = (
            db.scalar(
                select(func.count(PaymentAllocation.allocation_id)).where(
                    PaymentAllocation.invoice_id == cn_id
                )
            )
            or 0
        )
        if allocation_count > 0:
            raise ValidationError(
                f"Cannot delete credit note with {allocation_count} allocation(s)."
            )

        line_ids = [
            line.line_id
            for line in db.scalars(
                select(InvoiceLine).where(InvoiceLine.invoice_id == cn_id)
            ).all()
        ]
        if line_ids:
            db.execute(
                delete(InvoiceLineTax).where(InvoiceLineTax.line_id.in_(line_ids))
            )
        db.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id == cn_id))
        db.delete(credit_note)
        db.flush()
        db.commit()


# Module-level singleton instance
ar_invoice_service = ARInvoiceService()
