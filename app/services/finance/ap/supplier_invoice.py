"""
SupplierInvoiceService - AP invoice lifecycle management.

Manages creation, approval workflow, posting, and payment tracking.
"""

from __future__ import annotations

import builtins
import logging
import uuid as uuid_lib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.models.finance.ap.ap_payment_allocation import APPaymentAllocation
from app.models.finance.ap.goods_receipt import GoodsReceipt
from app.models.finance.ap.goods_receipt_line import GoodsReceiptLine
from app.models.finance.ap.purchase_order import PurchaseOrder
from app.models.finance.ap.purchase_order_line import PurchaseOrderLine
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.ap.supplier_invoice_line_tax import SupplierInvoiceLineTax
from app.models.finance.audit.audit_log import AuditAction
from app.models.finance.core_config.numbering_sequence import SequenceType
from app.models.finance.core_org.cost_center import CostCenter
from app.models.finance.core_org.project import Project
from app.models.finance.core_org.reporting_segment import ReportingSegment
from app.models.finance.gl.account import Account
from app.models.finance.tax.tax_code import TaxCode
from app.models.inventory.item import CostingMethod, Item
from app.services.audit_dispatcher import fire_audit_event
from app.services.common import NotFoundError, ValidationError, coerce_uuid
from app.services.finance.ap.input_utils import (
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
class InvoiceLineInput:
    """Input for an invoice line."""

    description: str
    quantity: Decimal
    unit_price: Decimal
    expense_account_id: UUID | None = None
    asset_account_id: UUID | None = None
    po_line_id: UUID | None = None
    goods_receipt_line_id: UUID | None = None
    item_id: UUID | None = None
    # Multiple tax codes per line (replaces single tax_code_id)
    tax_code_ids: list[UUID] = field(default_factory=list)
    # Keep legacy field for backwards compatibility
    tax_code_id: UUID | None = None
    cost_center_id: UUID | None = None
    project_id: UUID | None = None
    segment_id: UUID | None = None
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
    supplier_invoice_number: str | None = None
    exchange_rate: Decimal | None = None
    exchange_rate_type_id: UUID | None = None
    is_prepayment: bool = False
    is_intercompany: bool = False
    intercompany_org_id: UUID | None = None
    correlation_id: str | None = None


class SupplierInvoiceService(ListResponseMixin):
    """
    Service for supplier invoice lifecycle management.

    Manages creation, submission, approval, posting, and voiding.
    """

    @staticmethod
    def build_input_from_payload(
        db: Session,
        organization_id: UUID,
        payload: dict,
    ) -> SupplierInvoiceInput:
        """Build SupplierInvoiceInput from raw payload (strings or JSON)."""
        org_id = coerce_uuid(organization_id)

        lines_data = parse_json_list(payload.get("lines"), "Lines")
        lines: list[InvoiceLineInput] = []

        for line in lines_data:
            if not line.get("expense_account_id") or not line.get("description"):
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
                InvoiceLineInput(
                    description=line.get("description", ""),
                    quantity=parse_decimal(line.get("quantity", 1), "Quantity"),
                    unit_price=parse_decimal(line.get("unit_price", 0), "Unit price"),
                    item_id=coerce_uuid(line.get("item_id"))
                    if line.get("item_id")
                    else None,
                    expense_account_id=coerce_uuid(line.get("expense_account_id"))
                    if line.get("expense_account_id")
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

        invoice_date = (
            parse_date_str(payload.get("invoice_date"), "Invoice date") or date.today()
        )
        received_date = (
            parse_date_str(payload.get("received_date"), "Received date")
            or invoice_date
        )
        due_date = parse_date_str(payload.get("due_date"), "Due date") or invoice_date

        exchange_rate: Decimal | None = None
        if payload.get("exchange_rate") not in (None, ""):
            exchange_rate = parse_decimal(payload.get("exchange_rate"), "Exchange rate")

        return SupplierInvoiceInput(
            supplier_id=require_uuid(payload.get("supplier_id"), "Supplier"),
            invoice_type=SupplierInvoiceType.STANDARD,
            invoice_date=invoice_date,
            received_date=received_date,
            due_date=due_date,
            currency_code=resolve_currency_code(
                db, org_id, payload.get("currency_code")
            ),
            exchange_rate=exchange_rate,
            supplier_invoice_number=payload.get("invoice_number") or None,
            lines=lines,
        )

    @staticmethod
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

    @staticmethod
    def _require_po_line_org(
        db: Session,
        organization_id: UUID,
        po_line_id: UUID | None,
    ) -> None:
        if not po_line_id:
            return
        line = db.get(PurchaseOrderLine, coerce_uuid(po_line_id))
        if not line:
            raise NotFoundError("Purchase order line not found")
        po = db.get(PurchaseOrder, line.po_id)
        if not po or po.organization_id != organization_id:
            raise NotFoundError("Purchase order line not found")

    @staticmethod
    def _require_gr_line_org(
        db: Session,
        organization_id: UUID,
        gr_line_id: UUID | None,
    ) -> None:
        if not gr_line_id:
            return
        line = db.get(GoodsReceiptLine, coerce_uuid(gr_line_id))
        if not line:
            raise NotFoundError("Goods receipt line not found")
        receipt = db.get(GoodsReceipt, line.receipt_id)
        if not receipt or receipt.organization_id != organization_id:
            raise NotFoundError("Goods receipt line not found")

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
            ValidationError: If validation fails
            NotFoundError: If supplier not found
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)
        supplier_id = coerce_uuid(input.supplier_id)

        # Validate supplier exists and is active
        supplier = db.get(Supplier, supplier_id)
        if not supplier or supplier.organization_id != org_id:
            raise NotFoundError("Supplier not found")

        if not supplier.is_active:
            raise ValidationError("Supplier is not active")

        # Validate lines
        if not input.lines:
            raise ValidationError("Invoice must have at least one line")

        for line in input.lines:
            SupplierInvoiceService._require_org_match(
                db, org_id, Account, line.expense_account_id, "Expense account"
            )
            SupplierInvoiceService._require_org_match(
                db, org_id, Account, line.asset_account_id, "Asset account"
            )
            SupplierInvoiceService._require_org_match(
                db, org_id, Item, line.item_id, "Item"
            )
            SupplierInvoiceService._require_org_match(
                db, org_id, CostCenter, line.cost_center_id, "Cost center"
            )
            SupplierInvoiceService._require_org_match(
                db, org_id, Project, line.project_id, "Project"
            )
            SupplierInvoiceService._require_org_match(
                db, org_id, ReportingSegment, line.segment_id, "Reporting segment"
            )
            SupplierInvoiceService._require_po_line_org(db, org_id, line.po_line_id)
            SupplierInvoiceService._require_gr_line_org(
                db, org_id, line.goods_receipt_line_id
            )
            for tax_code_id in line.tax_code_ids:
                SupplierInvoiceService._require_org_match(
                    db, org_id, TaxCode, tax_code_id, "Tax code"
                )
            if line.tax_code_id and not line.tax_code_ids:
                SupplierInvoiceService._require_org_match(
                    db, org_id, TaxCode, line.tax_code_id, "Tax code"
                )

        # Calculate totals with auto tax calculation
        subtotal = Decimal("0")
        tax_total = Decimal("0")

        # Pre-calculate taxes for all lines
        line_tax_results: list[LineCalculationResult | None] = []
        for line in input.lines:
            line_amount = line.quantity * line.unit_price
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

        # Create lines and their tax records
        for idx, line_input in enumerate(input.lines, start=1):
            line_amount = line_input.quantity * line_input.unit_price
            if input.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
                line_amount = -abs(line_amount)

            # Get the pre-calculated tax result for this line
            tax_result = line_tax_results[idx - 1]
            line_tax_total = tax_result.total_tax if tax_result else Decimal("0")
            if input.invoice_type == SupplierInvoiceType.CREDIT_NOTE and tax_result:
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

            invoice_line = SupplierInvoiceLine(
                invoice_id=invoice.invoice_id,
                line_number=idx,
                description=line_input.description,
                quantity=line_input.quantity,
                unit_price=line_input.unit_price,
                line_amount=line_amount,
                tax_code_id=primary_tax_code_id,  # Primary tax for backwards compatibility
                tax_amount=line_tax_total,  # Total of all taxes on this line
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
            db.add(invoice_line)
            db.flush()  # Get line_id for tax records

            # Create SupplierInvoiceLineTax records for each tax (with recoverability)
            if tax_result and tax_result.taxes:
                for tax_detail in tax_result.taxes:
                    tax_amount = tax_detail.tax_amount
                    base_amount = tax_detail.base_amount
                    recoverable = tax_detail.recoverable_amount
                    if input.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
                        tax_amount = -abs(tax_amount)
                        base_amount = -abs(base_amount)
                        recoverable = -abs(recoverable)

                    line_tax = SupplierInvoiceLineTax(
                        line_id=invoice_line.line_id,
                        tax_code_id=tax_detail.tax_code_id,
                        base_amount=base_amount,
                        tax_rate=tax_detail.tax_rate,
                        tax_amount=tax_amount,
                        is_inclusive=tax_detail.is_inclusive,
                        sequence=tax_detail.sequence,
                        is_recoverable=tax_detail.is_recoverable,
                        recoverable_amount=recoverable,
                    )
                    db.add(line_tax)

        db.commit()
        db.refresh(invoice)

        fire_audit_event(
            db=db,
            organization_id=org_id,
            table_schema="ap",
            table_name="supplier_invoice",
            record_id=str(invoice.invoice_id),
            action=AuditAction.INSERT,
            new_values={
                "invoice_number": invoice.invoice_number,
                "supplier_id": str(supplier_id),
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
            NotFoundError: If invoice not found
            ValidationError: If invoice not in DRAFT status
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise NotFoundError("Invoice not found")

        if invoice.status != SupplierInvoiceStatus.DRAFT:
            raise ValidationError(
                f"Cannot update invoice with status '{invoice.status.value}'"
            )

        # Delete existing line tax and line records
        line_ids = list(
            db.scalars(
                select(SupplierInvoiceLine.line_id).where(
                    SupplierInvoiceLine.invoice_id == inv_id
                )
            ).all()
        )
        if line_ids:
            db.execute(
                delete(SupplierInvoiceLineTax).where(
                    SupplierInvoiceLineTax.line_id.in_(line_ids)
                )
            )
        db.execute(
            delete(SupplierInvoiceLine).where(SupplierInvoiceLine.invoice_id == inv_id)
        )

        # Recalculate totals
        subtotal = Decimal("0")
        tax_total = Decimal("0")

        # Pre-calculate taxes for all lines
        line_tax_results: list[LineCalculationResult | None] = []
        for line in input.lines:
            line_amount = line.quantity * line.unit_price
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
        if not supplier or supplier.organization_id != org_id:
            raise NotFoundError("Supplier not found")

        for line in input.lines:
            SupplierInvoiceService._require_org_match(
                db, org_id, Account, line.expense_account_id, "Expense account"
            )
            SupplierInvoiceService._require_org_match(
                db, org_id, Account, line.asset_account_id, "Asset account"
            )
            SupplierInvoiceService._require_org_match(
                db, org_id, Item, line.item_id, "Item"
            )
            SupplierInvoiceService._require_org_match(
                db, org_id, CostCenter, line.cost_center_id, "Cost center"
            )
            SupplierInvoiceService._require_org_match(
                db, org_id, Project, line.project_id, "Project"
            )
            SupplierInvoiceService._require_org_match(
                db, org_id, ReportingSegment, line.segment_id, "Reporting segment"
            )
            SupplierInvoiceService._require_po_line_org(db, org_id, line.po_line_id)
            SupplierInvoiceService._require_gr_line_org(
                db, org_id, line.goods_receipt_line_id
            )
            for tax_code_id in line.tax_code_ids:
                SupplierInvoiceService._require_org_match(
                    db, org_id, TaxCode, tax_code_id, "Tax code"
                )
            if line.tax_code_id and not line.tax_code_ids:
                SupplierInvoiceService._require_org_match(
                    db, org_id, TaxCode, line.tax_code_id, "Tax code"
                )

        for idx, line_input in enumerate(input.lines, start=1):
            line_amount = line_input.quantity * line_input.unit_price
            if input.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
                line_amount = -abs(line_amount)

            tax_result = line_tax_results[idx - 1]
            line_tax_total = tax_result.total_tax if tax_result else Decimal("0")
            if input.invoice_type == SupplierInvoiceType.CREDIT_NOTE and tax_result:
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

            invoice_line = SupplierInvoiceLine(
                invoice_id=inv_id,
                line_number=idx,
                description=line_input.description,
                quantity=line_input.quantity,
                unit_price=line_input.unit_price,
                line_amount=line_amount,
                tax_code_id=primary_tax_code_id,
                tax_amount=line_tax_total,
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
            db.add(invoice_line)
            db.flush()

            if tax_result and tax_result.taxes:
                for tax_detail in tax_result.taxes:
                    tax_amount = tax_detail.tax_amount
                    base_amount = tax_detail.base_amount
                    recoverable = tax_detail.recoverable_amount
                    if input.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
                        tax_amount = -abs(tax_amount)
                        base_amount = -abs(base_amount)
                        recoverable = -abs(recoverable)

                    line_tax = SupplierInvoiceLineTax(
                        line_id=invoice_line.line_id,
                        tax_code_id=tax_detail.tax_code_id,
                        base_amount=base_amount,
                        tax_rate=tax_detail.tax_rate,
                        tax_amount=tax_amount,
                        is_inclusive=tax_detail.is_inclusive,
                        sequence=tax_detail.sequence,
                        is_recoverable=tax_detail.is_recoverable,
                        recoverable_amount=recoverable,
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
            NotFoundError: If invoice not found
            ValidationError: If invoice cannot be submitted
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(submitted_by_user_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise NotFoundError("Invoice not found")

        if invoice.status != SupplierInvoiceStatus.DRAFT:
            raise ValidationError(
                f"Cannot submit invoice with status '{invoice.status.value}'"
            )

        invoice.status = SupplierInvoiceStatus.SUBMITTED
        invoice.submitted_by_user_id = user_id
        invoice.submitted_at = datetime.now(UTC)

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=org_id,
                entity_type="BILL",
                entity_id=inv_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": "DRAFT"},
                new_values={"status": "SUBMITTED"},
                user_id=user_id,
            )
        except Exception as e:
            logger.exception(
                "Workflow event failed for invoice %s submit: %s", inv_id, e
            )

        fire_audit_event(
            db=db,
            organization_id=org_id,
            table_schema="ap",
            table_name="supplier_invoice",
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
            NotFoundError: If invoice not found
            ValidationError: If invoice cannot be approved or SoD violation
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(approved_by_user_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise NotFoundError("Invoice not found")

        if invoice.status not in [
            SupplierInvoiceStatus.SUBMITTED,
            SupplierInvoiceStatus.PENDING_APPROVAL,
        ]:
            raise ValidationError(
                f"Cannot approve invoice with status '{invoice.status.value}'"
            )

        # Segregation of Duties check
        if invoice.submitted_by_user_id == user_id:
            raise ValidationError(
                "Segregation of duties violation: submitter cannot approve"
            )

        invoice.status = SupplierInvoiceStatus.APPROVED
        invoice.approved_by_user_id = user_id
        invoice.approved_at = datetime.now(UTC)

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=org_id,
                entity_type="BILL",
                entity_id=inv_id,
                event="ON_APPROVAL",
                old_values={"status": "SUBMITTED"},
                new_values={"status": "APPROVED"},
                user_id=user_id,
            )
        except Exception as e:
            logger.exception(
                "Workflow event failed for invoice %s approval: %s", inv_id, e
            )

        fire_audit_event(
            db=db,
            organization_id=org_id,
            table_schema="ap",
            table_name="supplier_invoice",
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
            NotFoundError: If invoice not found
            ValidationError: If invoice cannot be posted
        """
        from app.services.finance.ap.ap_posting_adapter import APPostingAdapter

        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)
        user_id = coerce_uuid(posted_by_user_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise NotFoundError("Invoice not found")

        if invoice.status != SupplierInvoiceStatus.APPROVED:
            raise ValidationError(
                f"Cannot post invoice with status '{invoice.status.value}'"
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
            raise ValidationError(result.message)

        # Update invoice status
        invoice.status = SupplierInvoiceStatus.POSTED
        invoice.posted_by_user_id = user_id
        invoice.posted_at = datetime.now(UTC)
        invoice.journal_entry_id = result.journal_entry_id
        invoice.posting_batch_id = result.posting_batch_id
        invoice.posting_status = "POSTED"

        # Update item costs from invoice lines
        SupplierInvoiceService._update_item_costs_from_invoice(db, org_id, invoice)

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=org_id,
                entity_type="BILL",
                entity_id=inv_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": "APPROVED"},
                new_values={"status": "POSTED"},
                user_id=user_id,
            )
        except Exception as e:
            logger.exception("Workflow event failed for invoice %s post: %s", inv_id, e)

        fire_audit_event(
            db=db,
            organization_id=org_id,
            table_schema="ap",
            table_name="supplier_invoice",
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
        invoice: SupplierInvoice,
        posted_by_user_id: UUID | None = None,
    ) -> bool:
        """
        Ensure an AP invoice in a posted state has its GL journal entries.

        For supplier invoices created via sync/import that already have a
        posted status (POSTED, PAID, PARTIALLY_PAID) but were never run
        through the GL posting pipeline, this idempotently creates the
        missing journal entries.

        Does NOT change the invoice status — only fills in missing GL entries.

        Args:
            db: Database session
            invoice: Supplier invoice to check and post if needed
            posted_by_user_id: User to attribute posting to (defaults to creator)

        Returns:
            True if GL entries were created, False if already posted or N/A
        """
        postable_statuses = {
            SupplierInvoiceStatus.APPROVED,
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PAID,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        }
        if invoice.status not in postable_statuses:
            return False
        if invoice.journal_entry_id is not None:
            return False  # Already has GL entries
        # Zero-amount invoices have nothing to post
        if invoice.total_amount == Decimal("0"):
            return False

        try:
            from app.services.finance.ap.ap_posting_adapter import APPostingAdapter

            user_id = (
                posted_by_user_id
                or invoice.created_by_user_id
                or UUID("00000000-0000-0000-0000-000000000000")
            )
            result = APPostingAdapter.post_invoice(
                db=db,
                organization_id=invoice.organization_id,
                invoice_id=invoice.invoice_id,
                posting_date=invoice.invoice_date,
                posted_by_user_id=user_id,
                idempotency_key=f"ensure-gl-ap-inv-{invoice.invoice_id}",
            )
            if result.success:
                invoice.journal_entry_id = result.journal_entry_id
                invoice.posting_batch_id = result.posting_batch_id
                invoice.posting_status = "POSTED"
                logger.info(
                    "Auto-posted AP invoice %s (journal %s)",
                    invoice.invoice_id,
                    result.journal_entry_id,
                )
                return True
            else:
                logger.warning(
                    "Auto-post failed for AP invoice %s: %s",
                    invoice.invoice_id,
                    result.message,
                )
                return False
        except Exception as e:
            logger.exception(
                "Error auto-posting AP invoice %s: %s", invoice.invoice_id, e
            )
            return False

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
            NotFoundError: If invoice not found
            ValidationError: If invoice cannot be voided
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise NotFoundError("Invoice not found")

        # Can void DRAFT, SUBMITTED, PENDING_APPROVAL, APPROVED
        non_voidable = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
            SupplierInvoiceStatus.PAID,
            SupplierInvoiceStatus.VOID,
        ]

        if invoice.status in non_voidable:
            raise ValidationError(
                f"Cannot void invoice with status '{invoice.status.value}'"
            )

        old_status = invoice.status.value
        invoice.status = SupplierInvoiceStatus.VOID

        fire_audit_event(
            db=db,
            organization_id=org_id,
            table_schema="ap",
            table_name="supplier_invoice",
            record_id=str(inv_id),
            action=AuditAction.UPDATE,
            old_values={"status": old_status},
            new_values={"status": "VOID"},
            user_id=coerce_uuid(voided_by_user_id),
            reason=reason,
        )

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
            raise NotFoundError("Invoice not found")

        if invoice.status in [
            SupplierInvoiceStatus.PAID,
            SupplierInvoiceStatus.VOID,
        ]:
            raise ValidationError(
                f"Cannot put on hold invoice with status '{invoice.status.value}'"
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
            raise NotFoundError("Invoice not found")

        if invoice.status != SupplierInvoiceStatus.ON_HOLD:
            raise ValidationError("Invoice is not on hold")

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
            raise NotFoundError("Invoice not found")

        if invoice.status not in [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]:
            raise ValidationError(
                f"Cannot pay invoice with status '{invoice.status.value}'"
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
        organization_id: UUID | str | None = None,
    ) -> SupplierInvoice:
        """
        Get an invoice by ID.

        Args:
            db: Database session
            invoice_id: Invoice ID
            organization_id: Organization scope for multi-tenant isolation

        Returns:
            SupplierInvoice

        Raises:
            NotFoundError: If not found or not in organization
        """
        invoice = db.get(SupplierInvoice, coerce_uuid(invoice_id))
        if not invoice:
            raise NotFoundError("Invoice not found")
        if organization_id is not None and invoice.organization_id != coerce_uuid(
            organization_id
        ):
            raise NotFoundError("Invoice not found")
        return invoice

    @staticmethod
    def get_invoice_lines(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
    ) -> builtins.list[SupplierInvoiceLine]:
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
            raise NotFoundError("Invoice not found")

        return list(
            db.scalars(
                select(SupplierInvoiceLine)
                .where(SupplierInvoiceLine.invoice_id == inv_id)
                .order_by(SupplierInvoiceLine.line_number)
            ).all()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: str,
        supplier_id: str | None = None,
        status: SupplierInvoiceStatus | None = None,
        invoice_type: SupplierInvoiceType | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        overdue_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[SupplierInvoice]:
        """
        List invoices with optional filters.

        Args:
            db: Database session
            organization_id: Organization scope for multi-tenant isolation
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
        stmt = select(SupplierInvoice).where(
            SupplierInvoice.organization_id == coerce_uuid(organization_id)
        )

        if supplier_id:
            stmt = stmt.where(SupplierInvoice.supplier_id == coerce_uuid(supplier_id))

        if status:
            stmt = stmt.where(SupplierInvoice.status == status)

        if invoice_type:
            stmt = stmt.where(SupplierInvoice.invoice_type == invoice_type)

        if from_date:
            stmt = stmt.where(SupplierInvoice.invoice_date >= from_date)

        if to_date:
            stmt = stmt.where(SupplierInvoice.invoice_date <= to_date)

        if overdue_only:
            stmt = stmt.where(
                and_(
                    SupplierInvoice.due_date < date.today(),
                    SupplierInvoice.status.in_(
                        [
                            SupplierInvoiceStatus.POSTED,
                            SupplierInvoiceStatus.PARTIALLY_PAID,
                        ]
                    ),
                )
            )

        stmt = stmt.order_by(SupplierInvoice.invoice_date.desc())
        return list(db.scalars(stmt.limit(limit).offset(offset)).all())

    @staticmethod
    def _update_item_costs_from_invoice(
        db: Session,
        organization_id: UUID,
        invoice: SupplierInvoice,
    ) -> None:
        """
        Update inventory item costs from supplier invoice lines.

        For each line with an item_id:
        - Updates Item.last_purchase_cost
        - For WEIGHTED_AVERAGE costing: recalculates Item.average_cost

        Args:
            db: Database session
            organization_id: Organization scope
            invoice: The posted invoice
        """
        from decimal import ROUND_HALF_UP

        # Skip credit notes (they don't establish new costs)
        if invoice.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
            return

        # Load invoice lines
        lines = list(
            db.scalars(
                select(SupplierInvoiceLine).where(
                    SupplierInvoiceLine.invoice_id == invoice.invoice_id
                )
            ).all()
        )

        for line in lines:
            if not line.item_id or line.quantity <= Decimal("0"):
                continue

            # Get the item
            item = db.get(Item, line.item_id)
            if not item or item.organization_id != organization_id:
                continue

            # Skip non-inventory items
            if not item.track_inventory:
                continue

            # Calculate unit cost from invoice line (net of tax)
            unit_cost = line.line_amount / line.quantity
            unit_cost = unit_cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

            # Always update last purchase cost
            item.last_purchase_cost = unit_cost

            # For weighted average items, recalculate average cost
            if item.costing_method == CostingMethod.WEIGHTED_AVERAGE:
                # Get current inventory quantity
                from app.services.inventory.transaction import (
                    InventoryTransactionService,
                )

                # Sum quantity across all warehouses
                total_qty = Decimal("0")
                from app.models.inventory.warehouse import Warehouse

                warehouses = list(
                    db.scalars(
                        select(Warehouse).where(
                            Warehouse.organization_id == organization_id,
                            Warehouse.is_active == True,
                        )
                    ).all()
                )

                for warehouse in warehouses:
                    qty = InventoryTransactionService.get_current_balance(
                        db, organization_id, item.item_id, warehouse.warehouse_id
                    )
                    total_qty += qty

                # Calculate new weighted average
                current_avg = item.average_cost or Decimal("0")
                current_value = total_qty * current_avg
                new_value = line.quantity * unit_cost
                new_total_qty = total_qty + line.quantity

                if new_total_qty > 0:
                    new_avg = (current_value + new_value) / new_total_qty
                    item.average_cost = new_avg.quantize(
                        Decimal("0.000001"), rounding=ROUND_HALF_UP
                    )

    @staticmethod
    def delete_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
    ) -> None:
        """Delete a supplier invoice (DRAFT only)."""
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            raise NotFoundError("Invoice not found")

        if invoice.status != SupplierInvoiceStatus.DRAFT:
            raise ValidationError(
                f"Cannot delete invoice with status '{invoice.status.value}'. "
                "Only DRAFT invoices can be deleted."
            )

        allocation_count = (
            db.scalar(
                select(func.count(APPaymentAllocation.allocation_id)).where(
                    APPaymentAllocation.invoice_id == inv_id
                )
            )
            or 0
        )
        if allocation_count > 0:
            raise ValidationError(
                f"Cannot delete invoice with {allocation_count} payment allocation(s)."
            )

        line_ids = list(
            db.scalars(
                select(SupplierInvoiceLine.line_id).where(
                    SupplierInvoiceLine.invoice_id == inv_id
                )
            ).all()
        )
        if line_ids:
            db.execute(
                delete(SupplierInvoiceLineTax).where(
                    SupplierInvoiceLineTax.line_id.in_(line_ids)
                )
            )
        db.execute(
            delete(SupplierInvoiceLine).where(SupplierInvoiceLine.invoice_id == inv_id)
        )
        db.delete(invoice)
        db.commit()


# Module-level singleton instance
supplier_invoice_service = SupplierInvoiceService()
