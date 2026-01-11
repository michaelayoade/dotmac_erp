"""
AP web view service.

Provides view-focused data for AP web routes.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.ifrs.ap.supplier import Supplier, SupplierType
from app.models.ifrs.ap.supplier_invoice import SupplierInvoice, SupplierInvoiceStatus, SupplierInvoiceType
from app.models.ifrs.ap.supplier_payment import SupplierPayment, APPaymentStatus
from app.models.ifrs.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.ifrs.ap.ap_payment_allocation import APPaymentAllocation
from app.models.ifrs.ap.purchase_order import PurchaseOrder, POStatus
from app.models.ifrs.ap.purchase_order_line import PurchaseOrderLine
from app.models.ifrs.ap.goods_receipt import GoodsReceipt, ReceiptStatus
from app.models.ifrs.ap.goods_receipt_line import GoodsReceiptLine
from app.models.ifrs.core_org.cost_center import CostCenter
from app.models.ifrs.core_org.project import Project
from app.models.ifrs.gl.account import Account
from app.models.ifrs.gl.account_category import AccountCategory, IFRSCategory
from app.config import settings
from app.services.common import coerce_uuid
from app.services.ifrs.ap.ap_aging import ap_aging_service
from app.services.ifrs.ap.supplier import SupplierInput, supplier_service
from app.services.ifrs.ap.supplier_invoice import (
    InvoiceLineInput,
    SupplierInvoiceInput,
    supplier_invoice_service,
)
from app.services.ifrs.ap.supplier_payment import (
    supplier_payment_service,
    SupplierPaymentInput,
    PaymentAllocationInput,
)
from app.models.ifrs.ap.supplier_payment import APPaymentMethod, APPaymentStatus
from app.services.ifrs.common.attachment import attachment_service
from app.services.ifrs.platform.currency_context import get_currency_context


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_date(value: Optional[date]) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _format_currency(
    amount: Optional[Decimal],
    currency: str = settings.default_presentation_currency_code,
) -> Optional[str]:
    if amount is None:
        return None
    value = Decimal(str(amount))
    return f"{currency} {value:,.2f}"


def _format_file_size(size: int) -> str:
    """Format file size for display."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


def _parse_supplier_type(value: Optional[str]) -> SupplierType:
    if not value:
        return SupplierType.VENDOR
    try:
        return SupplierType(value)
    except ValueError:
        try:
            return SupplierType(value.upper())
        except ValueError:
            return SupplierType.VENDOR


def _supplier_display_name(supplier: Supplier) -> str:
    return supplier.trading_name or supplier.legal_name


def _supplier_option_view(supplier: Supplier) -> dict:
    return {
        "supplier_id": supplier.supplier_id,
        "supplier_name": _supplier_display_name(supplier),
        "supplier_code": supplier.supplier_code,
        "currency_code": supplier.currency_code,
        "payment_terms_days": supplier.payment_terms_days,
    }


def _supplier_form_view(supplier: Supplier) -> dict:
    contact = supplier.primary_contact or {}
    return {
        "supplier_id": supplier.supplier_id,
        "supplier_code": supplier.supplier_code,
        "supplier_name": _supplier_display_name(supplier),
        "tax_id": supplier.tax_identification_number,
        "currency_code": supplier.currency_code,
        "payment_terms_days": supplier.payment_terms_days,
        "payment_method": None,
        "default_expense_account_id": supplier.default_expense_account_id,
        "default_payable_account_id": supplier.ap_control_account_id,
        "email": contact.get("email"),
        "phone": contact.get("phone"),
        "address": (supplier.billing_address or {}).get("address", ""),
        "is_active": supplier.is_active,
    }


def _supplier_list_view(supplier: Supplier, balance: Decimal) -> dict:
    return {
        "supplier_id": supplier.supplier_id,
        "supplier_code": supplier.supplier_code,
        "supplier_name": _supplier_display_name(supplier),
        "tax_id": supplier.tax_identification_number,
        "payment_terms_days": supplier.payment_terms_days,
        "balance": _format_currency(balance, supplier.currency_code),
        "is_active": supplier.is_active,
    }


def _supplier_detail_view(supplier: Supplier, balance: Decimal) -> dict:
    contact = supplier.primary_contact or {}
    return {
        "supplier_id": supplier.supplier_id,
        "supplier_code": supplier.supplier_code,
        "supplier_name": _supplier_display_name(supplier),
        "tax_id": supplier.tax_identification_number,
        "currency_code": supplier.currency_code,
        "payment_terms_days": supplier.payment_terms_days,
        "balance": _format_currency(balance, supplier.currency_code),
        "default_expense_account_id": supplier.default_expense_account_id,
        "default_payable_account_id": supplier.ap_control_account_id,
        "email": contact.get("email"),
        "phone": contact.get("phone"),
        "address": (supplier.billing_address or {}).get("address", ""),
        "is_active": supplier.is_active,
    }


def _invoice_line_view(line: SupplierInvoiceLine, currency_code: str) -> dict:
    return {
        "line_id": line.line_id,
        "line_number": line.line_number,
        "description": line.description,
        "quantity": line.quantity,
        "unit_price": _format_currency(line.unit_price, currency_code),
        "tax_amount": _format_currency(line.tax_amount, currency_code),
        "line_amount": _format_currency(line.line_amount, currency_code),
        "expense_account_id": line.expense_account_id,
        "asset_account_id": line.asset_account_id,
        "cost_center_id": line.cost_center_id,
        "project_id": line.project_id,
    }


def _invoice_detail_view(invoice: SupplierInvoice, supplier: Optional[Supplier]) -> dict:
    balance = invoice.total_amount - invoice.amount_paid
    today = date.today()
    return {
        "invoice_id": invoice.invoice_id,
        "invoice_number": invoice.invoice_number,
        "supplier_invoice_number": invoice.supplier_invoice_number,
        "invoice_type": invoice.invoice_type.value,
        "supplier_id": invoice.supplier_id,
        "supplier_name": _supplier_display_name(supplier) if supplier else "",
        "invoice_date": _format_date(invoice.invoice_date),
        "received_date": _format_date(invoice.received_date),
        "due_date": _format_date(invoice.due_date),
        "currency_code": invoice.currency_code,
        "subtotal": _format_currency(invoice.subtotal, invoice.currency_code),
        "tax_amount": _format_currency(invoice.tax_amount, invoice.currency_code),
        "total_amount": _format_currency(invoice.total_amount, invoice.currency_code),
        "amount_paid": _format_currency(invoice.amount_paid, invoice.currency_code),
        "balance": _format_currency(balance, invoice.currency_code),
        "status": _invoice_status_label(invoice.status),
        "is_overdue": (
            invoice.due_date < today
            and invoice.status not in {SupplierInvoiceStatus.PAID, SupplierInvoiceStatus.VOID}
        ),
    }


def _payment_detail_view(payment: SupplierPayment, supplier: Optional[Supplier]) -> dict:
    return {
        "payment_id": payment.payment_id,
        "payment_number": payment.payment_number,
        "supplier_id": payment.supplier_id,
        "supplier_name": _supplier_display_name(supplier) if supplier else "",
        "payment_date": _format_date(payment.payment_date),
        "payment_method": payment.payment_method.value,
        "reference_number": payment.reference,
        "amount": _format_currency(payment.amount, payment.currency_code),
        "status": _payment_status_label(payment.status),
        "currency_code": payment.currency_code,
    }


def _allocation_view(
    allocation: APPaymentAllocation,
    invoice: Optional[SupplierInvoice],
    currency_code: str,
) -> dict:
    return {
        "allocation_id": allocation.allocation_id,
        "invoice_id": allocation.invoice_id,
        "invoice_number": invoice.invoice_number if invoice else "",
        "allocated_amount": _format_currency(allocation.allocated_amount, currency_code),
        "discount_taken": _format_currency(allocation.discount_taken, currency_code),
        "exchange_difference": _format_currency(
            allocation.exchange_difference,
            currency_code,
        ),
        "allocation_date": _format_date(allocation.allocation_date),
    }


def _invoice_status_label(status: SupplierInvoiceStatus) -> str:
    if status == SupplierInvoiceStatus.PENDING_APPROVAL:
        return "PENDING"
    if status == SupplierInvoiceStatus.PARTIALLY_PAID:
        return "PARTIAL"
    return str(status.value)


def _payment_status_label(status: APPaymentStatus) -> str:
    if status in {APPaymentStatus.SENT, APPaymentStatus.CLEARED}:
        return "POSTED"
    if status == APPaymentStatus.VOID:
        return "VOIDED"
    return str(status.value)


def _parse_invoice_status(value: Optional[str]) -> Optional[SupplierInvoiceStatus]:
    if not value:
        return None
    status_map = {
        "PENDING": SupplierInvoiceStatus.PENDING_APPROVAL,
        "PARTIAL": SupplierInvoiceStatus.PARTIALLY_PAID,
    }
    if value in status_map:
        return status_map[value]
    try:
        return SupplierInvoiceStatus(value)
    except ValueError:
        return None


def _parse_payment_status(value: Optional[str]) -> Optional[APPaymentStatus]:
    if not value:
        return None
    status_map = {
        "POSTED": APPaymentStatus.CLEARED,
        "VOIDED": APPaymentStatus.VOID,
    }
    if value in status_map:
        return status_map[value]
    try:
        return APPaymentStatus(value)
    except ValueError:
        return None


def _get_accounts(
    db: Session,
    organization_id: UUID,
    ifrs_category: IFRSCategory,
    subledger_type: Optional[str] = None,
) -> list[Account]:
    query = (
        db.query(Account)
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .filter(
            Account.organization_id == organization_id,
            Account.is_active.is_(True),
            AccountCategory.ifrs_category == ifrs_category,
        )
    )
    if subledger_type:
        query = query.filter(Account.subledger_type == subledger_type)
    return query.order_by(Account.account_code).all()


def _get_cost_centers(db: Session, organization_id: UUID) -> list[CostCenter]:
    return (
        db.query(CostCenter)
        .filter(
            CostCenter.organization_id == organization_id,
            CostCenter.is_active.is_(True),
        )
        .order_by(CostCenter.cost_center_code)
        .all()
    )


def _get_projects(db: Session, organization_id: UUID) -> list[Project]:
    return (
        db.query(Project)
        .filter(Project.organization_id == organization_id)
        .order_by(Project.project_code)
        .all()
    )


@dataclass
class InvoiceStats:
    total_outstanding: str
    past_due: str
    due_this_week: str
    pending_count: int


class APWebService:
    """View service for AP web routes."""

    @staticmethod
    def build_supplier_input(form_data: dict) -> SupplierInput:
        return SupplierInput(
            supplier_code=form_data.get("supplier_code", ""),
            supplier_type=_parse_supplier_type(form_data.get("supplier_type")),
            legal_name=form_data.get("supplier_name", ""),
            trading_name=form_data.get("supplier_name"),
            tax_identification_number=form_data.get("tax_id"),
                currency_code=form_data.get(
                    "currency_code",
                    settings.default_functional_currency_code,
                ),
            payment_terms_days=int(form_data.get("payment_terms_days", 30)),
            ap_control_account_id=(
                UUID(form_data["default_payable_account_id"])
                if form_data.get("default_payable_account_id")
                else UUID("00000000-0000-0000-0000-000000000001")
            ),
            default_expense_account_id=(
                UUID(form_data["default_expense_account_id"])
                if form_data.get("default_expense_account_id")
                else None
            ),
            billing_address={
                "address": form_data.get("billing_address", ""),
            }
            if form_data.get("billing_address")
            else None,
            primary_contact={
                "email": form_data.get("email", ""),
                "phone": form_data.get("phone", ""),
            }
            if form_data.get("email") or form_data.get("phone")
            else None,
        )

    @staticmethod
    def build_invoice_input(data: dict) -> SupplierInvoiceInput:
        lines_data = data.get("lines", [])
        if isinstance(lines_data, str):
            lines_data = json.loads(lines_data)

        lines = []
        for line in lines_data:
            if line.get("expense_account_id") and line.get("description"):
                lines.append(
                    InvoiceLineInput(
                        description=line.get("description", ""),
                        quantity=Decimal(str(line.get("quantity", 1))),
                        unit_price=Decimal(str(line.get("unit_price", 0))),
                        expense_account_id=UUID(line["expense_account_id"])
                        if line.get("expense_account_id")
                        else None,
                        tax_amount=Decimal(str(line.get("tax_amount", 0))),
                        cost_center_id=UUID(line["cost_center_id"])
                        if line.get("cost_center_id")
                        else None,
                        project_id=UUID(line["project_id"]) if line.get("project_id") else None,
                    )
                )

        invoice_date = _parse_date(data.get("invoice_date")) or date.today()
        due_date = _parse_date(data.get("due_date")) or invoice_date

        return SupplierInvoiceInput(
            supplier_id=UUID(data["supplier_id"]),
            invoice_type=SupplierInvoiceType.STANDARD,
            invoice_date=invoice_date,
            received_date=invoice_date,
            due_date=due_date,
            currency_code=data.get(
                "currency_code",
                settings.default_functional_currency_code,
            ),
            supplier_invoice_number=data.get("invoice_number"),
            lines=lines,
        )

    @staticmethod
    def list_suppliers_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        status: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        is_active = None
        if status == "active":
            is_active = True
        elif status == "inactive":
            is_active = False

        query = db.query(Supplier).filter(Supplier.organization_id == org_id)
        if is_active is not None:
            query = query.filter(Supplier.is_active == is_active)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (Supplier.supplier_code.ilike(search_pattern))
                | (Supplier.legal_name.ilike(search_pattern))
                | (Supplier.trading_name.ilike(search_pattern))
                | (Supplier.tax_identification_number.ilike(search_pattern))
            )

        total_count = query.with_entities(func.count(Supplier.supplier_id)).scalar() or 0
        suppliers = (
            query.order_by(Supplier.legal_name)
            .limit(limit)
            .offset(offset)
            .all()
        )

        open_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]
        balances = (
            db.query(
                SupplierInvoice.supplier_id,
                func.coalesce(
                    func.sum(SupplierInvoice.total_amount - SupplierInvoice.amount_paid), 0
                ).label("balance"),
            )
            .filter(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.status.in_(open_statuses),
            )
            .group_by(SupplierInvoice.supplier_id)
            .all()
        )
        balance_map = {row.supplier_id: row.balance for row in balances}

        suppliers_view = [
            _supplier_list_view(
                supplier,
                balance_map.get(supplier.supplier_id, Decimal("0")),
            )
            for supplier in suppliers
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "suppliers": suppliers_view,
            "search": search,
            "status": status,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def supplier_form_context(
        db: Session,
        organization_id: str,
        supplier_id: Optional[str] = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        supplier = None
        if supplier_id:
            try:
                supplier = supplier_service.get(db, org_id, supplier_id)
            except Exception:
                supplier = None
        supplier_view = _supplier_form_view(supplier) if supplier else None

        expense_accounts = _get_accounts(db, org_id, IFRSCategory.EXPENSES)
        payable_accounts = _get_accounts(db, org_id, IFRSCategory.LIABILITIES, "AP")

        context = {
            "supplier": supplier_view,
            "expense_accounts": expense_accounts,
            "payable_accounts": payable_accounts,
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def supplier_detail_context(
        db: Session,
        organization_id: str,
        supplier_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        supplier = None
        try:
            supplier = supplier_service.get(db, org_id, supplier_id)
        except Exception:
            supplier = None

        if not supplier or supplier.organization_id != org_id:
            return {"supplier": None, "open_invoices": []}

        open_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]

        balance = (
            db.query(
                func.coalesce(
                    func.sum(SupplierInvoice.total_amount - SupplierInvoice.amount_paid),
                    0,
                )
            )
            .filter(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.supplier_id == supplier.supplier_id,
                SupplierInvoice.status.in_(open_statuses),
            )
            .scalar()
            or Decimal("0")
        )

        invoices = (
            db.query(SupplierInvoice)
            .filter(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.supplier_id == supplier.supplier_id,
                SupplierInvoice.status.in_(open_statuses),
            )
            .order_by(SupplierInvoice.due_date)
            .limit(10)
            .all()
        )

        today = date.today()
        open_invoices = []
        for invoice in invoices:
            balance_due = invoice.total_amount - invoice.amount_paid
            open_invoices.append(
                {
                    "invoice_id": invoice.invoice_id,
                    "invoice_number": invoice.invoice_number,
                    "invoice_date": _format_date(invoice.invoice_date),
                    "due_date": _format_date(invoice.due_date),
                    "total_amount": _format_currency(
                        invoice.total_amount,
                        invoice.currency_code,
                    ),
                    "balance": _format_currency(
                        balance_due,
                        invoice.currency_code,
                    ),
                    "status": _invoice_status_label(invoice.status),
                    "is_overdue": (
                        invoice.due_date < today
                        and invoice.status
                        not in {SupplierInvoiceStatus.PAID, SupplierInvoiceStatus.VOID}
                    ),
                }
            )

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="SUPPLIER",
            entity_id=supplier.supplier_id,
        )
        attachments_view = [
            {
                "attachment_id": str(att.attachment_id),
                "file_name": att.file_name,
                "file_size_display": _format_file_size(att.file_size),
                "content_type": att.content_type,
                "uploaded_at": att.uploaded_at.strftime("%Y-%m-%d %H:%M"),
                "description": att.description or "",
            }
            for att in attachments
        ]

        return {
            "supplier": _supplier_detail_view(supplier, balance),
            "open_invoices": open_invoices,
            "attachments": attachments_view,
        }

    @staticmethod
    def list_invoices_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        supplier_id: Optional[str],
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit
        today = date.today()

        status_value = _parse_invoice_status(status)
        from_date = _parse_date(start_date)
        to_date = _parse_date(end_date)

        query = (
            db.query(SupplierInvoice, Supplier)
            .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
            .filter(SupplierInvoice.organization_id == org_id)
        )

        if supplier_id:
            query = query.filter(SupplierInvoice.supplier_id == coerce_uuid(supplier_id))
        if status_value:
            query = query.filter(SupplierInvoice.status == status_value)
        if from_date:
            query = query.filter(SupplierInvoice.invoice_date >= from_date)
        if to_date:
            query = query.filter(SupplierInvoice.invoice_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    SupplierInvoice.invoice_number.ilike(search_pattern),
                    Supplier.legal_name.ilike(search_pattern),
                    Supplier.trading_name.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(SupplierInvoice.invoice_id)).scalar() or 0
        invoices = (
            query.order_by(SupplierInvoice.invoice_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        open_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]
        stats_base = query.with_entities(SupplierInvoice)
        outstanding_filter = stats_base.filter(SupplierInvoice.status.in_(open_statuses))

        total_outstanding = (
            outstanding_filter.with_entities(
                func.coalesce(
                    func.sum(SupplierInvoice.total_amount - SupplierInvoice.amount_paid), 0
                )
            ).scalar()
            or Decimal("0")
        )

        past_due = (
            outstanding_filter.filter(SupplierInvoice.due_date < today)
            .with_entities(
                func.coalesce(
                    func.sum(SupplierInvoice.total_amount - SupplierInvoice.amount_paid), 0
                )
            )
            .scalar()
            or Decimal("0")
        )

        due_this_week_end = today + timedelta(days=7)
        due_this_week = (
            outstanding_filter.filter(
                SupplierInvoice.due_date >= today,
                SupplierInvoice.due_date <= due_this_week_end,
            )
            .with_entities(
                func.coalesce(
                    func.sum(SupplierInvoice.total_amount - SupplierInvoice.amount_paid), 0
                )
            )
            .scalar()
            or Decimal("0")
        )

        pending_count = (
            stats_base.filter(SupplierInvoice.status == SupplierInvoiceStatus.PENDING_APPROVAL)
            .with_entities(func.count(SupplierInvoice.invoice_id))
            .scalar()
            or 0
        )

        invoices_view = []
        for invoice, supplier in invoices:
            balance = invoice.total_amount - invoice.amount_paid
            invoices_view.append(
                {
                    "invoice_id": invoice.invoice_id,
                    "invoice_number": invoice.invoice_number,
                    "supplier_name": _supplier_display_name(supplier),
                    "invoice_date": _format_date(invoice.invoice_date),
                    "due_date": _format_date(invoice.due_date),
                    "total_amount": _format_currency(
                        invoice.total_amount, invoice.currency_code
                    ),
                    "balance": _format_currency(balance, invoice.currency_code),
                    "status": _invoice_status_label(invoice.status),
                    "is_overdue": (
                        invoice.due_date < today
                        and invoice.status not in {SupplierInvoiceStatus.PAID, SupplierInvoiceStatus.VOID}
                    ),
                }
            )

        suppliers_list = [
            _supplier_option_view(supplier)
            for supplier in supplier_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        stats = InvoiceStats(
            total_outstanding=_format_currency(total_outstanding) or "$0.00",
            past_due=_format_currency(past_due) or "$0.00",
            due_this_week=_format_currency(due_this_week) or "$0.00",
            pending_count=pending_count,
        )

        return {
            "invoices": invoices_view,
            "suppliers_list": suppliers_list,
            "stats": stats.__dict__,
            "search": search,
            "supplier_id": supplier_id,
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def invoice_form_context(
        db: Session,
        organization_id: str,
        supplier_id: Optional[str] = None,
        po_id: Optional[str] = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        suppliers_list = [
            _supplier_option_view(supplier)
            for supplier in supplier_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        expense_accounts = _get_accounts(db, org_id, IFRSCategory.EXPENSES)

        # Pre-populated data from PO
        selected_supplier = None
        selected_po = None
        po_lines = []

        if supplier_id:
            supplier_uuid = coerce_uuid(supplier_id)
            supplier = db.get(Supplier, supplier_uuid)
            if supplier and supplier.organization_id == org_id:
                selected_supplier = _supplier_option_view(supplier)

        if po_id:
            po_uuid = coerce_uuid(po_id)
            po = db.get(PurchaseOrder, po_uuid)
            if po and po.organization_id == org_id:
                supplier = db.get(Supplier, po.supplier_id)
                selected_po = {
                    "po_id": str(po.po_id),
                    "po_number": po.po_number,
                    "supplier_id": str(po.supplier_id),
                    "supplier_name": _supplier_display_name(supplier) if supplier else "",
                    "currency_code": po.currency_code,
                    "total_amount": float(po.total_amount) if po.total_amount else 0,
                }
                # If supplier not already set, use PO's supplier
                if not selected_supplier and supplier:
                    selected_supplier = _supplier_option_view(supplier)

                # Get PO lines for pre-populating invoice lines
                lines = (
                    db.query(PurchaseOrderLine)
                    .filter(PurchaseOrderLine.po_id == po_uuid)
                    .order_by(PurchaseOrderLine.line_number)
                    .all()
                )
                for line in lines:
                    po_lines.append({
                        "line_id": str(line.line_id),
                        "line_number": line.line_number,
                        "description": line.description,
                        "quantity": float(line.quantity_ordered),
                        "unit_price": float(line.unit_price),
                        "amount": float(line.quantity_ordered * line.unit_price),
                        "expense_account_id": str(line.expense_account_id) if line.expense_account_id else "",
                    })

        context = {
            "suppliers_list": suppliers_list,
            "expense_accounts": expense_accounts,
            "cost_centers": _get_cost_centers(db, org_id),
            "projects": _get_projects(db, org_id),
            "organization_id": organization_id,
            "user_id": "00000000-0000-0000-0000-000000000001",
            "selected_supplier": selected_supplier,
            "selected_po": selected_po,
            "po_lines": po_lines,
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def invoice_detail_context(
        db: Session,
        organization_id: str,
        invoice_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        invoice = None
        try:
            invoice = supplier_invoice_service.get(db, invoice_id)
        except Exception:
            invoice = None

        if not invoice or invoice.organization_id != org_id:
            return {"invoice": None, "supplier": None, "lines": []}

        supplier = None
        try:
            supplier = supplier_service.get(db, org_id, str(invoice.supplier_id))
        except Exception:
            supplier = None

        lines = supplier_invoice_service.get_invoice_lines(
            db,
            organization_id=org_id,
            invoice_id=invoice.invoice_id,
        )
        lines_view = [
            _invoice_line_view(line, invoice.currency_code) for line in lines
        ]

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="SUPPLIER_INVOICE",
            entity_id=invoice.invoice_id,
        )
        attachments_view = [
            {
                "attachment_id": str(att.attachment_id),
                "file_name": att.file_name,
                "file_size": att.file_size,
                "file_size_display": _format_file_size(att.file_size),
                "content_type": att.content_type,
                "category": att.category.value,
                "description": att.description,
                "uploaded_at": att.uploaded_at,
                "download_url": f"/ap/attachments/{att.attachment_id}/download",
            }
            for att in attachments
        ]

        return {
            "invoice": _invoice_detail_view(invoice, supplier),
            "supplier": _supplier_form_view(supplier) if supplier else None,
            "lines": lines_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def list_payments_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        supplier_id: Optional[str],
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = _parse_payment_status(status)
        from_date = _parse_date(start_date)
        to_date = _parse_date(end_date)

        query = (
            db.query(SupplierPayment, Supplier)
            .join(Supplier, SupplierPayment.supplier_id == Supplier.supplier_id)
            .filter(SupplierPayment.organization_id == org_id)
        )

        if supplier_id:
            query = query.filter(SupplierPayment.supplier_id == coerce_uuid(supplier_id))
        if status_value:
            query = query.filter(SupplierPayment.status == status_value)
        if from_date:
            query = query.filter(SupplierPayment.payment_date >= from_date)
        if to_date:
            query = query.filter(SupplierPayment.payment_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    SupplierPayment.payment_number.ilike(search_pattern),
                    SupplierPayment.reference.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(SupplierPayment.payment_id)).scalar() or 0
        payments = (
            query.order_by(SupplierPayment.payment_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        payments_view = []
        for payment, supplier in payments:
            payments_view.append(
                {
                    "payment_id": payment.payment_id,
                    "payment_number": payment.payment_number,
                    "supplier_name": _supplier_display_name(supplier),
                    "payment_date": _format_date(payment.payment_date),
                    "payment_method": payment.payment_method.value,
                    "reference_number": payment.reference,
                    "amount": _format_currency(payment.amount, payment.currency_code),
                    "status": _payment_status_label(payment.status),
                }
            )

        suppliers_list = [
            _supplier_option_view(supplier)
            for supplier in supplier_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "payments": payments_view,
            "suppliers_list": suppliers_list,
            "search": search,
            "supplier_id": supplier_id,
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def payment_form_context(
        db: Session,
        organization_id: str,
        invoice_id: Optional[str] = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        suppliers_list = [
            _supplier_option_view(supplier)
            for supplier in supplier_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        open_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]

        query = (
            db.query(SupplierInvoice, Supplier)
            .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
            .filter(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.status.in_(open_statuses),
            )
        )

        if invoice_id:
            query = query.filter(SupplierInvoice.invoice_id == coerce_uuid(invoice_id))

        rows = query.order_by(SupplierInvoice.due_date).all()

        open_invoices = []
        selected_invoice = None
        for invoice, supplier in rows:
            balance = invoice.total_amount - invoice.amount_paid
            view = {
                "invoice_id": invoice.invoice_id,
                "invoice_number": invoice.invoice_number,
                "supplier_id": invoice.supplier_id,
                "supplier_name": _supplier_display_name(supplier),
                "invoice_date": _format_date(invoice.invoice_date),
                "due_date": _format_date(invoice.due_date),
                "total_amount": _format_currency(
                    invoice.total_amount,
                    invoice.currency_code,
                ),
                "balance": _format_currency(balance, invoice.currency_code),
                "currency_code": invoice.currency_code,
            }
            open_invoices.append(view)
            if invoice_id and invoice.invoice_id == coerce_uuid(invoice_id):
                selected_invoice = view

        context = {
            "suppliers_list": suppliers_list,
            "invoice_id": invoice_id,
            "invoice": selected_invoice,
            "open_invoices": open_invoices,
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def payment_detail_context(
        db: Session,
        organization_id: str,
        payment_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        payment = None
        try:
            payment = supplier_payment_service.get(db, payment_id)
        except Exception:
            payment = None

        if not payment or payment.organization_id != org_id:
            return {"payment": None, "supplier": None, "allocations": []}

        supplier = None
        try:
            supplier = supplier_service.get(db, org_id, str(payment.supplier_id))
        except Exception:
            supplier = None

        allocations = supplier_payment_service.get_payment_allocations(
            db,
            organization_id=org_id,
            payment_id=payment.payment_id,
        )

        invoice_map: dict[UUID, SupplierInvoice] = {}
        if allocations:
            invoice_ids = [allocation.invoice_id for allocation in allocations]
            invoices = (
                db.query(SupplierInvoice)
                .filter(SupplierInvoice.invoice_id.in_(invoice_ids))
                .all()
            )
            invoice_map = {invoice.invoice_id: invoice for invoice in invoices}

        allocations_view = [
            _allocation_view(
                allocation,
                invoice_map.get(allocation.invoice_id),
                payment.currency_code,
            )
            for allocation in allocations
        ]

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="SUPPLIER_PAYMENT",
            entity_id=payment.payment_id,
        )
        attachments_view = [
            {
                "attachment_id": str(att.attachment_id),
                "file_name": att.file_name,
                "file_size": att.file_size,
                "file_size_display": _format_file_size(att.file_size),
                "content_type": att.content_type,
                "category": att.category.value,
                "description": att.description,
                "uploaded_at": att.uploaded_at,
                "download_url": f"/ap/attachments/{att.attachment_id}/download",
            }
            for att in attachments
        ]

        return {
            "payment": _payment_detail_view(payment, supplier),
            "supplier": _supplier_form_view(supplier) if supplier else None,
            "allocations": allocations_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def aging_context(
        db: Session,
        organization_id: str,
        as_of_date: Optional[str],
        supplier_id: Optional[str],
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        ref_date = _parse_date(as_of_date)

        if supplier_id:
            summary = ap_aging_service.calculate_supplier_aging(
                db, org_id, coerce_uuid(supplier_id), ref_date
            )
            aging_data = [summary]
        else:
            aging_data = ap_aging_service.get_aging_by_supplier(
                db, org_id, ref_date
            )

        suppliers_list = [
            _supplier_option_view(supplier)
            for supplier in supplier_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        return {
            "aging_data": aging_data,
            "suppliers_list": suppliers_list,
            "as_of_date": as_of_date,
            "supplier_id": supplier_id,
        }


    @staticmethod
    def delete_supplier(
        db: Session,
        organization_id: str,
        supplier_id: str,
    ) -> Optional[str]:
        """Delete a supplier. Returns error message or None on success."""
        org_id = coerce_uuid(organization_id)
        sup_id = coerce_uuid(supplier_id)

        supplier = db.get(Supplier, sup_id)
        if not supplier or supplier.organization_id != org_id:
            return "Supplier not found"

        # Check for existing invoices
        invoice_count = (
            db.query(func.count(SupplierInvoice.invoice_id))
            .filter(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.supplier_id == sup_id,
            )
            .scalar()
            or 0
        )

        if invoice_count > 0:
            return f"Cannot delete supplier with {invoice_count} invoice(s). Deactivate instead."

        # Check for existing payments
        payment_count = (
            db.query(func.count(SupplierPayment.payment_id))
            .filter(
                SupplierPayment.organization_id == org_id,
                SupplierPayment.supplier_id == sup_id,
            )
            .scalar()
            or 0
        )

        if payment_count > 0:
            return f"Cannot delete supplier with {payment_count} payment(s). Deactivate instead."

        try:
            db.delete(supplier)
            db.commit()
            return None
        except Exception as e:
            db.rollback()
            return f"Failed to delete supplier: {str(e)}"

    @staticmethod
    def delete_invoice(
        db: Session,
        organization_id: str,
        invoice_id: str,
    ) -> Optional[str]:
        """Delete an invoice. Returns error message or None on success."""
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            return "Invoice not found"

        # Only DRAFT invoices can be deleted
        if invoice.status != SupplierInvoiceStatus.DRAFT:
            return f"Cannot delete invoice with status '{invoice.status.value}'. Only DRAFT invoices can be deleted."

        # Check for existing payments/allocations
        allocation_count = (
            db.query(func.count(APPaymentAllocation.allocation_id))
            .filter(APPaymentAllocation.invoice_id == inv_id)
            .scalar()
            or 0
        )

        if allocation_count > 0:
            return f"Cannot delete invoice with {allocation_count} payment allocation(s)."

        try:
            # Delete invoice lines first
            db.query(SupplierInvoiceLine).filter(
                SupplierInvoiceLine.invoice_id == inv_id
            ).delete()
            db.delete(invoice)
            db.commit()
            return None
        except Exception as e:
            db.rollback()
            return f"Failed to delete invoice: {str(e)}"

    @staticmethod
    def build_payment_input(data: dict) -> SupplierPaymentInput:
        """Build SupplierPaymentInput from form data."""
        payment_date = _parse_date(data.get("payment_date")) or date.today()

        # Parse payment method
        method_str = data.get("payment_method", "BANK_TRANSFER")
        try:
            payment_method = APPaymentMethod(method_str)
        except ValueError:
            payment_method = APPaymentMethod.BANK_TRANSFER

        # Parse allocations if provided
        allocations = []
        allocations_data = data.get("allocations", [])
        if isinstance(allocations_data, str):
            try:
                allocations_data = json.loads(allocations_data)
            except json.JSONDecodeError:
                allocations_data = []

        for alloc in allocations_data:
            if alloc.get("invoice_id") and alloc.get("amount"):
                allocations.append(
                    PaymentAllocationInput(
                        invoice_id=UUID(alloc["invoice_id"]),
                        amount=Decimal(str(alloc["amount"])),
                    )
                )

        return SupplierPaymentInput(
            supplier_id=UUID(data["supplier_id"]),
            payment_date=payment_date,
            payment_method=payment_method,
            currency_code=data.get(
                "currency_code",
                settings.default_functional_currency_code,
            ),
            amount=Decimal(str(data.get("amount", 0))),
            bank_account_id=UUID(data["bank_account_id"]) if data.get("bank_account_id") else None,
            reference=data.get("reference"),
            allocations=allocations,
        )

    @staticmethod
    def delete_payment(
        db: Session,
        organization_id: str,
        payment_id: str,
    ) -> Optional[str]:
        """Delete a payment. Returns error message or None on success."""
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)

        payment = db.get(SupplierPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            return "Payment not found"

        # Only DRAFT payments can be deleted
        if payment.status != APPaymentStatus.DRAFT:
            return f"Cannot delete payment with status '{payment.status.value}'. Only draft payments can be deleted."

        try:
            # Delete allocations first
            db.query(APPaymentAllocation).filter(
                APPaymentAllocation.payment_id == pay_id
            ).delete()
            db.delete(payment)
            db.commit()
            return None
        except Exception as e:
            db.rollback()
            return f"Failed to delete payment: {str(e)}"

    # =========================================================================
    # Purchase Orders
    # =========================================================================

    @staticmethod
    def list_purchase_orders_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        supplier_id: Optional[str],
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        """Build context for purchase orders list page."""
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        # Parse status
        status_value = None
        if status:
            try:
                status_value = POStatus(status)
            except ValueError:
                status_value = None

        from_date = _parse_date(start_date)
        to_date = _parse_date(end_date)

        query = (
            db.query(PurchaseOrder, Supplier)
            .join(Supplier, PurchaseOrder.supplier_id == Supplier.supplier_id)
            .filter(PurchaseOrder.organization_id == org_id)
        )

        if supplier_id:
            query = query.filter(PurchaseOrder.supplier_id == coerce_uuid(supplier_id))
        if status_value:
            query = query.filter(PurchaseOrder.status == status_value)
        if from_date:
            query = query.filter(PurchaseOrder.po_date >= from_date)
        if to_date:
            query = query.filter(PurchaseOrder.po_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    PurchaseOrder.po_number.ilike(search_pattern),
                    Supplier.legal_name.ilike(search_pattern),
                    Supplier.trading_name.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(PurchaseOrder.po_id)).scalar() or 0
        orders = (
            query.order_by(PurchaseOrder.po_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        # Build stats
        draft_count = (
            db.query(func.count(PurchaseOrder.po_id))
            .filter(
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.status == POStatus.DRAFT,
            )
            .scalar() or 0
        )
        pending_count = (
            db.query(func.count(PurchaseOrder.po_id))
            .filter(
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.status == POStatus.PENDING_APPROVAL,
            )
            .scalar() or 0
        )
        approved_total = (
            db.query(func.coalesce(func.sum(PurchaseOrder.total_amount), 0))
            .filter(
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.status == POStatus.APPROVED,
            )
            .scalar() or Decimal("0")
        )
        open_total = (
            db.query(func.coalesce(func.sum(PurchaseOrder.total_amount - PurchaseOrder.amount_received), 0))
            .filter(
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.status.in_([POStatus.APPROVED, POStatus.PARTIALLY_RECEIVED]),
            )
            .scalar() or Decimal("0")
        )

        orders_view = []
        for po, supplier in orders:
            orders_view.append({
                "po_id": po.po_id,
                "po_number": po.po_number,
                "supplier_name": _supplier_display_name(supplier),
                "po_date": _format_date(po.po_date),
                "expected_delivery_date": _format_date(po.expected_delivery_date),
                "total_amount": _format_currency(po.total_amount, po.currency_code),
                "amount_received": _format_currency(po.amount_received, po.currency_code),
                "status": po.status.value,
                "currency_code": po.currency_code,
            })

        suppliers_list = [
            _supplier_option_view(supplier)
            for supplier in supplier_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "orders": orders_view,
            "suppliers_list": suppliers_list,
            "stats": {
                "draft_count": draft_count,
                "pending_count": pending_count,
                "approved_total": _format_currency(approved_total) or "$0.00",
                "open_total": _format_currency(open_total) or "$0.00",
            },
            "search": search,
            "supplier_id": supplier_id,
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "po_statuses": [s.value for s in POStatus],
        }

    @staticmethod
    def purchase_order_detail_context(
        db: Session,
        organization_id: str,
        po_id: str,
    ) -> dict:
        """Build context for purchase order detail page."""
        org_id = coerce_uuid(organization_id)
        po_uuid = coerce_uuid(po_id)

        po = db.get(PurchaseOrder, po_uuid)
        if not po or po.organization_id != org_id:
            return {"order": None, "supplier": None, "lines": []}

        supplier = db.get(Supplier, po.supplier_id)

        lines = (
            db.query(PurchaseOrderLine)
            .filter(PurchaseOrderLine.po_id == po_uuid)
            .order_by(PurchaseOrderLine.line_number)
            .all()
        )

        lines_view = []
        for line in lines:
            lines_view.append({
                "line_id": line.line_id,
                "line_number": line.line_number,
                "description": line.description,
                "quantity_ordered": line.quantity_ordered,
                "quantity_received": line.quantity_received,
                "quantity_invoiced": line.quantity_invoiced,
                "unit_price": _format_currency(line.unit_price, po.currency_code),
                "line_amount": _format_currency(line.line_amount, po.currency_code),
                "tax_amount": _format_currency(line.tax_amount, po.currency_code),
                "item_id": line.item_id,
            })

        order_view = {
            "po_id": po.po_id,
            "po_number": po.po_number,
            "supplier_id": po.supplier_id,
            "supplier_name": _supplier_display_name(supplier) if supplier else "",
            "po_date": _format_date(po.po_date),
            "expected_delivery_date": _format_date(po.expected_delivery_date),
            "currency_code": po.currency_code,
            "subtotal": _format_currency(po.subtotal, po.currency_code),
            "tax_amount": _format_currency(po.tax_amount, po.currency_code),
            "total_amount": _format_currency(po.total_amount, po.currency_code),
            "amount_received": _format_currency(po.amount_received, po.currency_code),
            "amount_invoiced": _format_currency(po.amount_invoiced, po.currency_code),
            "status": po.status.value,
            "terms_and_conditions": po.terms_and_conditions,
            "shipping_address": po.shipping_address,
            "created_at": po.created_at,
            "approved_at": po.approved_at,
        }

        # Get related goods receipts
        goods_receipts = (
            db.query(GoodsReceipt)
            .filter(GoodsReceipt.po_id == po_uuid)
            .order_by(GoodsReceipt.receipt_date.desc())
            .all()
        )
        receipts_view = []
        for gr in goods_receipts:
            line_count = (
                db.query(func.count(GoodsReceiptLine.line_id))
                .filter(GoodsReceiptLine.receipt_id == gr.receipt_id)
                .scalar() or 0
            )
            receipts_view.append({
                "receipt_id": gr.receipt_id,
                "receipt_number": gr.receipt_number,
                "receipt_date": _format_date(gr.receipt_date),
                "status": gr.status.value,
                "line_count": line_count,
                "notes": gr.notes,
            })

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="PURCHASE_ORDER",
            entity_id=po.po_id,
        )
        attachments_view = [
            {
                "attachment_id": str(att.attachment_id),
                "file_name": att.file_name,
                "file_size": att.file_size,
                "file_size_display": _format_file_size(att.file_size),
                "content_type": att.content_type,
                "category": att.category.value,
                "description": att.description,
                "uploaded_at": att.uploaded_at,
                "download_url": f"/ap/attachments/{att.attachment_id}/download",
            }
            for att in attachments
        ]

        return {
            "order": order_view,
            "supplier": _supplier_form_view(supplier) if supplier else None,
            "lines": lines_view,
            "goods_receipts": receipts_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def purchase_order_form_context(
        db: Session,
        organization_id: str,
        po_id: Optional[str] = None,
    ) -> dict:
        """Build context for purchase order form (create/edit)."""
        org_id = coerce_uuid(organization_id)

        suppliers_list = [
            _supplier_option_view(supplier)
            for supplier in supplier_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        expense_accounts = _get_accounts(db, org_id, IFRSCategory.EXPENSES)

        # Get inventory items for selection
        from app.models.ifrs.inv.item import Item
        items = (
            db.query(Item)
            .filter(
                Item.organization_id == org_id,
                Item.is_active.is_(True),
                Item.is_purchaseable.is_(True),
            )
            .order_by(Item.item_code)
            .limit(500)
            .all()
        )
        items_list = [
            {
                "item_id": item.item_id,
                "item_code": item.item_code,
                "item_name": item.item_name,
                "standard_cost": float(item.standard_cost) if item.standard_cost else None,
                "currency_code": item.currency_code,
            }
            for item in items
        ]

        order = None
        lines = []
        if po_id:
            po_uuid = coerce_uuid(po_id)
            po = db.get(PurchaseOrder, po_uuid)
            if po and po.organization_id == org_id:
                order = {
                    "po_id": po.po_id,
                    "po_number": po.po_number,
                    "supplier_id": str(po.supplier_id),
                    "po_date": _format_date(po.po_date),
                    "expected_delivery_date": _format_date(po.expected_delivery_date),
                    "currency_code": po.currency_code,
                    "terms_and_conditions": po.terms_and_conditions,
                    "status": po.status.value,
                }
                po_lines = (
                    db.query(PurchaseOrderLine)
                    .filter(PurchaseOrderLine.po_id == po_uuid)
                    .order_by(PurchaseOrderLine.line_number)
                    .all()
                )
                for line in po_lines:
                    lines.append({
                        "line_id": str(line.line_id),
                        "item_id": str(line.item_id) if line.item_id else "",
                        "description": line.description,
                        "quantity": float(line.quantity_ordered),
                        "unit_price": float(line.unit_price),
                        "tax_amount": float(line.tax_amount) if line.tax_amount else 0,
                        "expense_account_id": str(line.expense_account_id) if line.expense_account_id else "",
                    })

        context = {
            "order": order,
            "lines": lines,
            "suppliers_list": suppliers_list,
            "expense_accounts": expense_accounts,
            "items_list": items_list,
            "cost_centers": _get_cost_centers(db, org_id),
            "projects": _get_projects(db, org_id),
        }
        context.update(get_currency_context(db, organization_id))
        return context

    # =========================================================================
    # Goods Receipts
    # =========================================================================

    @staticmethod
    def list_goods_receipts_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        supplier_id: Optional[str],
        po_id: Optional[str],
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        """Build context for goods receipts list page."""
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        # Parse status
        status_value = None
        if status:
            try:
                status_value = ReceiptStatus(status)
            except ValueError:
                status_value = None

        from_date = _parse_date(start_date)
        to_date = _parse_date(end_date)

        query = (
            db.query(GoodsReceipt, Supplier, PurchaseOrder)
            .join(Supplier, GoodsReceipt.supplier_id == Supplier.supplier_id)
            .join(PurchaseOrder, GoodsReceipt.po_id == PurchaseOrder.po_id)
            .filter(GoodsReceipt.organization_id == org_id)
        )

        if supplier_id:
            query = query.filter(GoodsReceipt.supplier_id == coerce_uuid(supplier_id))
        if po_id:
            query = query.filter(GoodsReceipt.po_id == coerce_uuid(po_id))
        if status_value:
            query = query.filter(GoodsReceipt.status == status_value)
        if from_date:
            query = query.filter(GoodsReceipt.receipt_date >= from_date)
        if to_date:
            query = query.filter(GoodsReceipt.receipt_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    GoodsReceipt.receipt_number.ilike(search_pattern),
                    PurchaseOrder.po_number.ilike(search_pattern),
                    Supplier.legal_name.ilike(search_pattern),
                    Supplier.trading_name.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(GoodsReceipt.receipt_id)).scalar() or 0
        receipts = (
            query.order_by(GoodsReceipt.receipt_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        # Build stats
        received_count = (
            db.query(func.count(GoodsReceipt.receipt_id))
            .filter(
                GoodsReceipt.organization_id == org_id,
                GoodsReceipt.status == ReceiptStatus.RECEIVED,
            )
            .scalar() or 0
        )
        inspecting_count = (
            db.query(func.count(GoodsReceipt.receipt_id))
            .filter(
                GoodsReceipt.organization_id == org_id,
                GoodsReceipt.status == ReceiptStatus.INSPECTING,
            )
            .scalar() or 0
        )
        accepted_count = (
            db.query(func.count(GoodsReceipt.receipt_id))
            .filter(
                GoodsReceipt.organization_id == org_id,
                GoodsReceipt.status == ReceiptStatus.ACCEPTED,
            )
            .scalar() or 0
        )

        receipts_view = []
        for gr, supplier, po in receipts:
            # Count lines
            line_count = (
                db.query(func.count(GoodsReceiptLine.line_id))
                .filter(GoodsReceiptLine.receipt_id == gr.receipt_id)
                .scalar() or 0
            )
            receipts_view.append({
                "receipt_id": gr.receipt_id,
                "receipt_number": gr.receipt_number,
                "supplier_name": _supplier_display_name(supplier),
                "po_number": po.po_number,
                "po_id": po.po_id,
                "receipt_date": _format_date(gr.receipt_date),
                "status": gr.status.value,
                "line_count": line_count,
                "notes": gr.notes,
            })

        suppliers_list = [
            _supplier_option_view(supplier)
            for supplier in supplier_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "receipts": receipts_view,
            "suppliers_list": suppliers_list,
            "stats": {
                "received_count": received_count,
                "inspecting_count": inspecting_count,
                "accepted_count": accepted_count,
            },
            "search": search,
            "supplier_id": supplier_id,
            "po_id": po_id,
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "gr_statuses": [s.value for s in ReceiptStatus],
        }

    @staticmethod
    def goods_receipt_detail_context(
        db: Session,
        organization_id: str,
        receipt_id: str,
    ) -> dict:
        """Build context for goods receipt detail page."""
        org_id = coerce_uuid(organization_id)
        receipt_uuid = coerce_uuid(receipt_id)

        gr = db.get(GoodsReceipt, receipt_uuid)
        if not gr or gr.organization_id != org_id:
            return {"receipt": None, "supplier": None, "order": None, "lines": []}

        supplier = db.get(Supplier, gr.supplier_id)
        po = db.get(PurchaseOrder, gr.po_id)

        lines = (
            db.query(GoodsReceiptLine, PurchaseOrderLine)
            .join(PurchaseOrderLine, GoodsReceiptLine.po_line_id == PurchaseOrderLine.line_id)
            .filter(GoodsReceiptLine.receipt_id == receipt_uuid)
            .order_by(GoodsReceiptLine.line_number)
            .all()
        )

        lines_view = []
        for gr_line, po_line in lines:
            lines_view.append({
                "line_id": gr_line.line_id,
                "line_number": gr_line.line_number,
                "description": po_line.description,
                "quantity_ordered": po_line.quantity_ordered,
                "quantity_received": gr_line.quantity_received,
                "quantity_accepted": gr_line.quantity_accepted,
                "quantity_rejected": gr_line.quantity_rejected,
                "rejection_reason": gr_line.rejection_reason,
                "lot_number": gr_line.lot_number,
                "unit_price": _format_currency(po_line.unit_price, po.currency_code) if po else None,
            })

        receipt_view = {
            "receipt_id": gr.receipt_id,
            "receipt_number": gr.receipt_number,
            "supplier_id": gr.supplier_id,
            "supplier_name": _supplier_display_name(supplier) if supplier else "",
            "po_id": gr.po_id,
            "po_number": po.po_number if po else "",
            "receipt_date": _format_date(gr.receipt_date),
            "status": gr.status.value,
            "notes": gr.notes,
            "warehouse_id": gr.warehouse_id,
            "created_at": gr.created_at,
        }

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="GOODS_RECEIPT",
            entity_id=gr.receipt_id,
        )
        attachments_view = [
            {
                "attachment_id": str(att.attachment_id),
                "file_name": att.file_name,
                "file_size": att.file_size,
                "file_size_display": _format_file_size(att.file_size),
                "content_type": att.content_type,
                "category": att.category.value,
                "description": att.description,
                "uploaded_at": att.uploaded_at,
                "download_url": f"/ap/attachments/{att.attachment_id}/download",
            }
            for att in attachments
        ]

        return {
            "receipt": receipt_view,
            "supplier": _supplier_form_view(supplier) if supplier else None,
            "order": {
                "po_id": po.po_id,
                "po_number": po.po_number,
                "po_date": _format_date(po.po_date),
                "status": po.status.value,
                "total_amount": _format_currency(po.total_amount, po.currency_code),
            } if po else None,
            "lines": lines_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def goods_receipt_form_context(
        db: Session,
        organization_id: str,
        po_id: Optional[str] = None,
    ) -> dict:
        """Build context for goods receipt form (create)."""
        org_id = coerce_uuid(organization_id)

        # Get POs that can receive goods (APPROVED or PARTIALLY_RECEIVED)
        receivable_statuses = [POStatus.APPROVED, POStatus.PARTIALLY_RECEIVED]
        pos = (
            db.query(PurchaseOrder, Supplier)
            .join(Supplier, PurchaseOrder.supplier_id == Supplier.supplier_id)
            .filter(
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.status.in_(receivable_statuses),
            )
            .order_by(PurchaseOrder.po_date.desc())
            .limit(100)
            .all()
        )

        po_list = []
        for po, supplier in pos:
            po_list.append({
                "po_id": str(po.po_id),
                "po_number": po.po_number,
                "supplier_id": str(po.supplier_id),
                "supplier_name": _supplier_display_name(supplier),
                "po_date": _format_date(po.po_date),
                "total_amount": _format_currency(po.total_amount, po.currency_code),
                "currency_code": po.currency_code,
            })

        # If a specific PO is selected, get its lines
        selected_po = None
        po_lines = []
        if po_id:
            po_uuid = coerce_uuid(po_id)
            po = db.get(PurchaseOrder, po_uuid)
            if po and po.organization_id == org_id:
                supplier = db.get(Supplier, po.supplier_id)
                selected_po = {
                    "po_id": str(po.po_id),
                    "po_number": po.po_number,
                    "supplier_id": str(po.supplier_id),
                    "supplier_name": _supplier_display_name(supplier) if supplier else "",
                    "po_date": _format_date(po.po_date),
                    "currency_code": po.currency_code,
                }

                lines = (
                    db.query(PurchaseOrderLine)
                    .filter(PurchaseOrderLine.po_id == po_uuid)
                    .order_by(PurchaseOrderLine.line_number)
                    .all()
                )

                for line in lines:
                    remaining = line.quantity_ordered - line.quantity_received
                    if remaining > 0:
                        po_lines.append({
                            "line_id": str(line.line_id),
                            "line_number": line.line_number,
                            "description": line.description,
                            "quantity_ordered": float(line.quantity_ordered),
                            "quantity_received": float(line.quantity_received),
                            "quantity_remaining": float(remaining),
                            "unit_price": float(line.unit_price),
                        })

        # Get warehouses for selection
        from app.models.ifrs.inv.warehouse import Warehouse
        warehouses = (
            db.query(Warehouse)
            .filter(
                Warehouse.organization_id == org_id,
                Warehouse.is_active.is_(True),
            )
            .order_by(Warehouse.warehouse_code)
            .all()
        )
        warehouse_list = [
            {
                "warehouse_id": str(w.warehouse_id),
                "warehouse_code": w.warehouse_code,
                "warehouse_name": w.warehouse_name,
            }
            for w in warehouses
        ]

        return {
            "po_list": po_list,
            "selected_po": selected_po,
            "po_lines": po_lines,
            "warehouse_list": warehouse_list,
        }


ap_web_service = APWebService()
