"""
AP web view service.

Provides view-focused data for AP web routes.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, load_only

from app.models.finance.ap.ap_payment_allocation import APPaymentAllocation
from app.models.finance.ap.goods_receipt import GoodsReceipt, ReceiptStatus
from app.models.finance.ap.goods_receipt_line import GoodsReceiptLine
from app.models.finance.ap.payment_batch import APBatchStatus
from app.models.finance.ap.purchase_order import POStatus, PurchaseOrder
from app.models.finance.ap.purchase_order_line import PurchaseOrderLine
from app.models.finance.ap.supplier import Supplier, SupplierType
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.ap.supplier_payment import (
    APPaymentMethod,
    APPaymentStatus,
    SupplierPayment,
)
from app.models.finance.banking.bank_account import BankAccountStatus
from app.models.finance.common.attachment import AttachmentCategory
from app.models.finance.core_org.cost_center import CostCenter
from app.models.finance.core_org.project import Project
from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.notification import EntityType, NotificationType
from app.models.person import Person
from app.services.audit_info import get_audit_service
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.finance.ap.ap_aging import ap_aging_service
from app.services.finance.ap.goods_receipt import goods_receipt_service
from app.services.finance.ap.payment_batch import payment_batch_service
from app.services.finance.ap.purchase_order import purchase_order_service
from app.services.finance.ap.supplier import SupplierInput, supplier_service
from app.services.finance.ap.supplier_invoice import (
    SupplierInvoiceInput,
    supplier_invoice_service,
)
from app.services.finance.ap.supplier_payment import (
    SupplierPaymentInput,
    supplier_payment_service,
)
from app.services.finance.banking.bank_account import bank_account_service
from app.services.finance.common import (
    format_currency,
    format_date,
    format_file_size,
    parse_date,
    parse_enum_safe,
)
from app.services.finance.common.attachment import AttachmentInput, attachment_service
from app.services.finance.common.sorting import apply_sort
from app.services.finance.platform.currency_context import get_currency_context
from app.services.notification import notification_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)

# Keep aliases for backward compatibility with existing code
_parse_date = parse_date
_format_date = format_date
_format_currency = format_currency
_format_file_size = format_file_size


def _parse_supplier_type(value: str | None) -> SupplierType:
    return parse_enum_safe(SupplierType, value, SupplierType.VENDOR)


def _supplier_display_name(supplier: Supplier) -> str:
    return supplier.trading_name or supplier.legal_name


def _calculate_supplier_balance_trends(
    db: Session,
    organization_id: UUID,
    supplier_ids: list[UUID],
    months: int = 6,
) -> dict[UUID, list[float]]:
    """
    Calculate monthly balance trends for suppliers over the last N months.
    Returns a dict mapping supplier_id to a list of balance values.
    """
    if not supplier_ids:
        return {}

    from dateutil.relativedelta import relativedelta

    trends: dict[UUID, list[float]] = {sid: [] for sid in supplier_ids}
    today = date.today()

    # Calculate balance at end of each month for the last N months
    for i in range(months - 1, -1, -1):
        # Get the last day of the month (i months ago)
        if i == 0:
            as_of_date = today
        else:
            month_start = today.replace(day=1) - relativedelta(months=i)
            # Last day of that month
            next_month = month_start + relativedelta(months=1)
            as_of_date = next_month - timedelta(days=1)

        # Query balance as of that date for all suppliers
        # Balance = sum of (total - paid) for invoices created on or before as_of_date
        # that are still open or were open at that time
        balances = db.execute(
            select(
                SupplierInvoice.supplier_id,
                func.coalesce(
                    func.sum(
                        SupplierInvoice.total_amount - SupplierInvoice.amount_paid
                    ),
                    0,
                ).label("balance"),
            )
            .where(
                SupplierInvoice.organization_id == organization_id,
                SupplierInvoice.supplier_id.in_(supplier_ids),
                SupplierInvoice.invoice_date <= as_of_date,
                SupplierInvoice.status.in_(
                    [
                        SupplierInvoiceStatus.POSTED,
                        SupplierInvoiceStatus.PARTIALLY_PAID,
                        SupplierInvoiceStatus.PAID,  # Include paid to see historical
                    ]
                ),
            )
            .group_by(SupplierInvoice.supplier_id)
        ).all()

        balance_map = {row.supplier_id: float(row.balance) for row in balances}

        for sid in supplier_ids:
            trends[sid].append(balance_map.get(sid, 0.0))

    return trends


def _supplier_option_view(supplier: Supplier) -> dict:
    return {
        "supplier_id": supplier.supplier_id,
        "supplier_name": _supplier_display_name(supplier),
        "supplier_code": supplier.supplier_code,
        "currency_code": supplier.currency_code,
        "payment_terms_days": supplier.payment_terms_days,
        # WHT fields for payment form
        "withholding_tax_applicable": getattr(
            supplier, "withholding_tax_applicable", False
        ),
        "withholding_tax_code_id": str(supplier.withholding_tax_code_id)
        if getattr(supplier, "withholding_tax_code_id", None)
        else "",
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


def _supplier_list_view(
    supplier: Supplier,
    balance: Decimal,
    created_by_name: str | None = None,
    balance_trend: list[float] | None = None,
) -> dict:
    contact = supplier.primary_contact or {}
    return {
        "supplier_id": supplier.supplier_id,
        "supplier_code": supplier.supplier_code,
        "supplier_name": _supplier_display_name(supplier),
        "tax_id": supplier.tax_identification_number,
        "contact_email": contact.get("email"),
        "payment_terms_days": supplier.payment_terms_days,
        "balance": _format_currency(balance, supplier.currency_code),
        "balance_trend": balance_trend
        if balance_trend and any(v > 0 for v in balance_trend)
        else None,
        "is_active": supplier.is_active,
        # Audit info
        "created_at": supplier.created_at,
        "created_by_user_id": supplier.created_by_user_id,
        "created_by_name": created_by_name,
        "updated_at": supplier.updated_at,
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
    line_amount_raw = float(line.line_amount) if line.line_amount else 0.0
    return {
        "line_id": line.line_id,
        "line_number": line.line_number,
        "description": line.description,
        "quantity": line.quantity,
        "unit_price": _format_currency(line.unit_price, currency_code),
        "tax_amount": _format_currency(line.tax_amount, currency_code),
        "tax_amount_raw": float(line.tax_amount) if line.tax_amount else 0.0,
        "line_amount_raw": line_amount_raw,
        "line_amount": _format_currency(line.line_amount, currency_code),
        "display_line_amount_raw": line_amount_raw,
        "display_line_amount": _format_currency(line.line_amount, currency_code),
        "expense_account_id": line.expense_account_id,
        "asset_account_id": line.asset_account_id,
        "cost_center_id": line.cost_center_id,
        "project_id": line.project_id,
        # VAT display fields — enriched in invoice_detail_context
        "vat_amount_raw": 0.0,
        "vat_amount": None,
        "vat_label": None,
    }


def _invoice_detail_view(invoice: SupplierInvoice, supplier: Supplier | None) -> dict:
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
        "display_subtotal": _format_currency(invoice.subtotal, invoice.currency_code),
        "display_subtotal_raw": float(invoice.subtotal),
        "tax_amount": _format_currency(invoice.tax_amount, invoice.currency_code),
        "display_tax_amount": _format_currency(
            invoice.tax_amount, invoice.currency_code
        ),
        "display_tax_amount_raw": float(invoice.tax_amount),
        "display_tax_added": _format_currency(
            invoice.tax_amount, invoice.currency_code
        ),
        "display_tax_added_raw": float(invoice.tax_amount),
        "display_tax_included": _format_currency(Decimal("0"), invoice.currency_code),
        "display_tax_included_raw": 0.0,
        "total_amount": _format_currency(invoice.total_amount, invoice.currency_code),
        "amount_paid": _format_currency(invoice.amount_paid, invoice.currency_code),
        "amount_paid_raw": float(invoice.amount_paid) if invoice.amount_paid else 0.0,
        "balance": _format_currency(balance, invoice.currency_code),
        "status": _invoice_status_label(invoice.status),
        "comments": getattr(invoice, "comments", None),
        "is_overdue": (
            invoice.due_date < today
            and invoice.status
            not in {SupplierInvoiceStatus.PAID, SupplierInvoiceStatus.VOID}
        ),
    }


def _payment_detail_view(payment: SupplierPayment, supplier: Supplier | None) -> dict:
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
    invoice: SupplierInvoice | None,
    currency_code: str,
) -> dict:
    return {
        "allocation_id": allocation.allocation_id,
        "invoice_id": allocation.invoice_id,
        "invoice_number": invoice.invoice_number if invoice else "",
        "allocated_amount": _format_currency(
            allocation.allocated_amount, currency_code
        ),
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


def _parse_invoice_status(value: str | None) -> SupplierInvoiceStatus | None:
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


def _parse_payment_status(value: str | None) -> APPaymentStatus | None:
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
    subledger_type: str | None = None,
) -> list[Account]:
    stmt = (
        select(Account)
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .where(
            Account.organization_id == organization_id,
            Account.is_active.is_(True),
            AccountCategory.ifrs_category == ifrs_category,
        )
    )
    if subledger_type:
        stmt = stmt.where(Account.subledger_type == subledger_type)
    return list(db.scalars(stmt.order_by(Account.account_code)).all())


def _get_cost_centers(db: Session, organization_id: UUID) -> list[CostCenter]:
    return list(
        db.scalars(
            select(CostCenter)
            .where(
                CostCenter.organization_id == organization_id,
                CostCenter.is_active.is_(True),
            )
            .order_by(CostCenter.cost_center_code)
        ).all()
    )


def _get_projects(db: Session, organization_id: UUID) -> list[Project]:
    return list(
        db.scalars(
            select(Project)
            .options(
                load_only(
                    Project.project_id,
                    Project.project_code,
                    Project.project_name,
                )
            )
            .where(Project.organization_id == organization_id)
            .order_by(Project.project_code)
        ).all()
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
    def build_supplier_input(
        db: Session, form_data: dict, organization_id: UUID
    ) -> SupplierInput:
        payload = dict(form_data)
        return supplier_service.build_input_from_payload(
            db=db,
            organization_id=organization_id,
            payload=payload,
        )

    @staticmethod
    def build_invoice_input(
        db: Session, data: dict, organization_id: UUID
    ) -> SupplierInvoiceInput:
        payload = dict(data)
        return supplier_invoice_service.build_input_from_payload(
            db=db,
            organization_id=organization_id,
            payload=payload,
        )

    @staticmethod
    def list_suppliers_context(
        db: Session,
        organization_id: str,
        search: str | None,
        status: str | None,
        page: int,
        limit: int = 50,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        is_active = None
        if status == "active":
            is_active = True
        elif status == "inactive":
            is_active = False

        conditions: list[Any] = [Supplier.organization_id == org_id]
        stmt = select(Supplier).where(*conditions)
        if is_active is not None:
            conditions.append(Supplier.is_active == is_active)
            stmt = stmt.where(Supplier.is_active == is_active)
        if search:
            search_pattern = f"%{search}%"
            search_filter = (
                (Supplier.supplier_code.ilike(search_pattern))
                | (Supplier.legal_name.ilike(search_pattern))
                | (Supplier.trading_name.ilike(search_pattern))
                | (Supplier.tax_identification_number.ilike(search_pattern))
            )
            conditions.append(search_filter)
            stmt = stmt.where(search_filter)

        total_count = (
            db.scalar(select(func.count(Supplier.supplier_id)).where(*conditions)) or 0
        )
        supplier_sort_map: dict[str, Any] = {
            "legal_name": Supplier.legal_name,
            "trading_name": Supplier.trading_name,
            "supplier_code": Supplier.supplier_code,
            "status": Supplier.status,
        }
        stmt = apply_sort(
            stmt,
            sort,
            sort_dir,
            supplier_sort_map,
            default=Supplier.legal_name.asc(),
        )
        suppliers = list(db.scalars(stmt.limit(limit).offset(offset)).all())

        open_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]
        balances = db.execute(
            select(
                SupplierInvoice.supplier_id,
                func.coalesce(
                    func.sum(
                        SupplierInvoice.total_amount - SupplierInvoice.amount_paid
                    ),
                    0,
                ).label("balance"),
            )
            .where(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.status.in_(open_statuses),
            )
            .group_by(SupplierInvoice.supplier_id)
        ).all()
        balance_map = {row.supplier_id: row.balance for row in balances}

        # Use shared audit service for user names
        audit_service = get_audit_service(db)
        creator_names = audit_service.get_creator_names(suppliers)

        # Calculate balance trends for sparkline charts
        supplier_ids = [s.supplier_id for s in suppliers]
        balance_trends = _calculate_supplier_balance_trends(db, org_id, supplier_ids)

        suppliers_view = [
            _supplier_list_view(
                supplier,
                balance_map.get(supplier.supplier_id, Decimal("0")),
                creator_names.get(supplier.created_by_user_id),
                balance_trends.get(supplier.supplier_id),
            )
            for supplier in suppliers
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        # Calculate stats for template header cards
        total_suppliers = (
            db.scalar(
                select(func.count(Supplier.supplier_id)).where(
                    Supplier.organization_id == org_id
                )
            )
            or 0
        )
        active_count = (
            db.scalar(
                select(func.count(Supplier.supplier_id)).where(
                    Supplier.organization_id == org_id, Supplier.is_active.is_(True)
                )
            )
            or 0
        )
        total_payables_raw = db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        SupplierInvoice.total_amount - SupplierInvoice.amount_paid
                    ),
                    0,
                )
            ).where(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.status.in_(open_statuses),
            )
        ) or Decimal("0")
        overdue_count = (
            db.scalar(
                select(func.count(SupplierInvoice.invoice_id)).where(
                    SupplierInvoice.organization_id == org_id,
                    SupplierInvoice.status.in_(open_statuses),
                    SupplierInvoice.due_date < date.today(),
                )
            )
            or 0
        )

        active_filters = build_active_filters(
            params={"status": status},
        )
        return {
            "suppliers": suppliers_view,
            "search": search,
            "status": status,
            "sort": sort,
            "sort_dir": sort_dir,
            "page": page,
            "limit": limit,
            "per_page": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            # Stats for header cards
            "total_suppliers": total_suppliers,
            "active_count": active_count,
            "total_payables": _format_currency(total_payables_raw),
            "overdue_count": overdue_count,
            "active_filters": active_filters,
        }

    @staticmethod
    def supplier_form_context(
        db: Session,
        organization_id: str,
        supplier_id: str | None = None,
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

        balance = db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        SupplierInvoice.total_amount - SupplierInvoice.amount_paid
                    ),
                    0,
                )
            ).where(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.supplier_id == supplier.supplier_id,
                SupplierInvoice.status.in_(open_statuses),
            )
        ) or Decimal("0")

        invoices = db.scalars(
            select(SupplierInvoice)
            .where(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.supplier_id == supplier.supplier_id,
                SupplierInvoice.status.in_(open_statuses),
            )
            .order_by(SupplierInvoice.due_date)
            .limit(10)
        ).all()

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
        search: str | None,
        supplier_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        limit: int = 50,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit
        today = date.today()

        status_value = _parse_invoice_status(status)
        from_date = _parse_date(start_date)
        to_date = _parse_date(end_date)

        conditions: list[Any] = [SupplierInvoice.organization_id == org_id]
        if supplier_id:
            conditions.append(SupplierInvoice.supplier_id == coerce_uuid(supplier_id))
        if status_value:
            conditions.append(SupplierInvoice.status == status_value)
        if from_date:
            conditions.append(SupplierInvoice.invoice_date >= from_date)
        if to_date:
            conditions.append(SupplierInvoice.invoice_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            conditions.append(
                or_(
                    SupplierInvoice.invoice_number.ilike(search_pattern),
                    Supplier.legal_name.ilike(search_pattern),
                    Supplier.trading_name.ilike(search_pattern),
                )
            )

        base_stmt = (
            select(SupplierInvoice, Supplier)
            .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
            .where(*conditions)
        )

        total_count = (
            db.scalar(
                select(func.count(SupplierInvoice.invoice_id))
                .select_from(SupplierInvoice)
                .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
                .where(*conditions)
            )
            or 0
        )
        invoice_sort_map: dict[str, Any] = {
            "invoice_date": SupplierInvoice.invoice_date,
            "invoice_number": SupplierInvoice.invoice_number,
            "supplier_name": Supplier.legal_name,
            "total_amount": SupplierInvoice.total_amount,
            "due_date": SupplierInvoice.due_date,
            "status": SupplierInvoice.status,
        }
        stmt = apply_sort(
            base_stmt,
            sort,
            sort_dir,
            invoice_sort_map,
            default=SupplierInvoice.invoice_date.desc(),
        )
        invoices = db.execute(stmt.limit(limit).offset(offset)).all()

        open_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]
        stats_conditions = list(conditions)
        outstanding_conditions = [
            *stats_conditions,
            SupplierInvoice.status.in_(open_statuses),
        ]

        total_outstanding = db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        SupplierInvoice.total_amount - SupplierInvoice.amount_paid
                    ),
                    0,
                )
            ).where(*outstanding_conditions)
        ) or Decimal("0")

        past_due = db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        SupplierInvoice.total_amount - SupplierInvoice.amount_paid
                    ),
                    0,
                )
            ).where(*outstanding_conditions, SupplierInvoice.due_date < today)
        ) or Decimal("0")

        due_this_week_end = today + timedelta(days=7)
        due_this_week = db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        SupplierInvoice.total_amount - SupplierInvoice.amount_paid
                    ),
                    0,
                )
            ).where(
                *outstanding_conditions,
                SupplierInvoice.due_date >= today,
                SupplierInvoice.due_date <= due_this_week_end,
            )
        ) or Decimal("0")

        pending_count = (
            db.scalar(
                select(func.count(SupplierInvoice.invoice_id)).where(
                    *stats_conditions,
                    SupplierInvoice.status == SupplierInvoiceStatus.PENDING_APPROVAL,
                )
            )
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
                        and invoice.status
                        not in {SupplierInvoiceStatus.PAID, SupplierInvoiceStatus.VOID}
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

        active_filters = build_active_filters(
            params={
                "status": status,
                "supplier_id": supplier_id,
                "start_date": start_date,
                "end_date": end_date,
            },
            labels={"start_date": "From", "end_date": "To"},
            options={
                "supplier_id": {
                    str(s["supplier_id"]): s["supplier_name"] for s in suppliers_list
                }
            },
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
            "sort": sort,
            "sort_dir": sort_dir,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "active_filters": active_filters,
        }

    @staticmethod
    def invoice_form_context(
        db: Session,
        organization_id: str,
        supplier_id: str | None = None,
        po_id: str | None = None,
    ) -> dict:
        from app.models.finance.tax.tax_code import TaxCode
        from app.models.fixed_assets.asset_category import AssetCategory
        from app.models.inventory.item import Item

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

        # Get tax codes that apply to purchases
        tax_codes = [
            {
                "tax_code_id": str(tax.tax_code_id),
                "tax_code": tax.tax_code,
                "tax_name": tax.tax_name,
                "tax_rate": float(tax.tax_rate),
                "rate_display": float((tax.tax_rate * 100).quantize(Decimal("0.01")))
                if tax.tax_rate < 1
                else float(tax.tax_rate),
                "is_inclusive": tax.is_inclusive,
                "is_compound": tax.is_compound,
                "is_recoverable": getattr(tax, "is_recoverable", True),
            }
            for tax in db.scalars(
                select(TaxCode).where(
                    TaxCode.organization_id == org_id,
                    TaxCode.is_active.is_(True),
                    TaxCode.applies_to_purchases.is_(True),
                )
            ).all()
        ]

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
                    "supplier_name": _supplier_display_name(supplier)
                    if supplier
                    else "",
                    "currency_code": po.currency_code,
                    "total_amount": float(po.total_amount) if po.total_amount else 0,
                }
                # If supplier not already set, use PO's supplier
                if not selected_supplier and supplier:
                    selected_supplier = _supplier_option_view(supplier)

                # Get PO lines for pre-populating invoice lines
                lines = db.scalars(
                    select(PurchaseOrderLine)
                    .where(PurchaseOrderLine.po_id == po_uuid)
                    .order_by(PurchaseOrderLine.line_number)
                ).all()
                for line in lines:
                    po_lines.append(
                        {
                            "line_id": str(line.line_id),
                            "line_number": line.line_number,
                            "description": line.description,
                            "quantity": float(line.quantity_ordered),
                            "unit_price": float(line.unit_price),
                            "amount": float(line.quantity_ordered * line.unit_price),
                            "expense_account_id": str(line.expense_account_id)
                            if line.expense_account_id
                            else "",
                        }
                    )

        # Get inventory items for AP → INV integration
        items_list = [
            {
                "item_id": str(item.item_id),
                "item_code": item.item_code,
                "item_name": item.item_name,
                "unit_price": float(item.last_purchase_cost)
                if item.last_purchase_cost
                else 0,
                "uom": item.base_uom,
            }
            for item in db.scalars(
                select(Item)
                .where(
                    Item.organization_id == org_id,
                    Item.is_active.is_(True),
                    Item.is_purchaseable.is_(True),
                )
                .order_by(Item.item_code)
                .limit(200)
            ).all()
        ]

        # Get asset accounts for capitalization (AP → FA integration)
        asset_accounts = _get_accounts(db, org_id, IFRSCategory.ASSETS)

        # Get asset categories for capitalization
        asset_categories = [
            {
                "category_id": str(cat.category_id),
                "category_code": cat.category_code,
                "category_name": cat.category_name,
                "threshold": float(cat.capitalization_threshold),
            }
            for cat in db.scalars(
                select(AssetCategory)
                .where(
                    AssetCategory.organization_id == org_id,
                    AssetCategory.is_active.is_(True),
                )
                .order_by(AssetCategory.category_code)
            ).all()
        ]

        context = {
            "suppliers_list": suppliers_list,
            "expense_accounts": expense_accounts,
            "asset_accounts": asset_accounts,
            "items_list": items_list,
            "tax_codes": tax_codes,
            "asset_categories": asset_categories,
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
        lines_view = [_invoice_line_view(line, invoice.currency_code) for line in lines]

        # Enrich lines with VAT/tax labels from line_tax detail
        inclusive_tax_by_line: dict[UUID, Decimal] = {}
        line_ids = [line.line_id for line in lines]
        if line_ids:
            from app.models.finance.ap.supplier_invoice_line_tax import (
                SupplierInvoiceLineTax,
            )
            from app.models.finance.tax.tax_code import TaxCode, TaxType

            vat_taxes = db.execute(
                select(SupplierInvoiceLineTax, TaxCode)
                .join(
                    TaxCode,
                    TaxCode.tax_code_id == SupplierInvoiceLineTax.tax_code_id,
                )
                .where(
                    SupplierInvoiceLineTax.line_id.in_(line_ids),
                    TaxCode.organization_id == org_id,
                    TaxCode.tax_type.in_([TaxType.VAT, TaxType.GST]),
                )
            ).all()

            vat_by_line: dict[UUID, Decimal] = {}
            vat_labels_by_line: dict[UUID, set[str]] = {}
            for line_tax, tax_code in vat_taxes:
                tax_amount = line_tax.tax_amount or Decimal("0")
                tax_rate = line_tax.tax_rate
                if not isinstance(tax_rate, Decimal):
                    try:
                        tax_rate = Decimal(str(tax_rate))
                    except Exception:
                        tax_rate = Decimal("0")

                vat_by_line[line_tax.line_id] = (
                    vat_by_line.get(line_tax.line_id, Decimal("0")) + tax_amount
                )
                if line_tax.is_inclusive:
                    inclusive_tax_by_line[line_tax.line_id] = (
                        inclusive_tax_by_line.get(line_tax.line_id, Decimal("0"))
                        + tax_amount
                    )
                rate_label = (
                    f"{(tax_rate * 100).quantize(Decimal('0.01'))}%"
                    if tax_rate < 1
                    else f"{tax_rate}%"
                )
                incl_suffix = " Incl." if line_tax.is_inclusive else ""
                vat_labels_by_line.setdefault(line_tax.line_id, set()).add(
                    f"{tax_code.tax_code} {rate_label}{incl_suffix}"
                )

            for idx, line in enumerate(lines):
                inclusive_tax = inclusive_tax_by_line.get(line.line_id, Decimal("0"))
                if inclusive_tax > 0:
                    display_amount = (line.line_amount or Decimal("0")) + inclusive_tax
                    lines_view[idx]["display_line_amount_raw"] = float(display_amount)
                    lines_view[idx]["display_line_amount"] = _format_currency(
                        display_amount, invoice.currency_code
                    )

                vat_amount = vat_by_line.get(line.line_id, Decimal("0"))
                if vat_amount > 0:
                    lines_view[idx]["vat_amount_raw"] = float(vat_amount)
                    lines_view[idx]["vat_amount"] = _format_currency(
                        vat_amount, invoice.currency_code
                    )
                    labels = vat_labels_by_line.get(line.line_id, set())
                    lines_view[idx]["vat_label"] = (
                        ", ".join(sorted(labels)) if labels else None
                    )

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

        invoice_view = _invoice_detail_view(invoice, supplier)
        inclusive_vat_total = sum(inclusive_tax_by_line.values(), Decimal("0"))
        if inclusive_vat_total > 0:
            display_subtotal = (invoice.subtotal or Decimal("0")) + inclusive_vat_total
            display_tax_added = (
                invoice.tax_amount or Decimal("0")
            ) - inclusive_vat_total
            if display_tax_added < 0:
                display_tax_added = Decimal("0")
            invoice_view["display_subtotal_raw"] = float(display_subtotal)
            invoice_view["display_subtotal"] = _format_currency(
                display_subtotal, invoice.currency_code
            )
            invoice_view["display_tax_amount_raw"] = float(display_tax_added)
            invoice_view["display_tax_amount"] = _format_currency(
                display_tax_added, invoice.currency_code
            )
            invoice_view["display_tax_added_raw"] = float(display_tax_added)
            invoice_view["display_tax_added"] = _format_currency(
                display_tax_added, invoice.currency_code
            )
            invoice_view["display_tax_included_raw"] = float(inclusive_vat_total)
            invoice_view["display_tax_included"] = _format_currency(
                inclusive_vat_total, invoice.currency_code
            )

        return {
            "invoice": invoice_view,
            "supplier": _supplier_form_view(supplier) if supplier else None,
            "lines": lines_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def list_payments_context(
        db: Session,
        organization_id: str,
        search: str | None,
        supplier_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        limit: int = 50,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = _parse_payment_status(status)
        from_date = _parse_date(start_date)
        to_date = _parse_date(end_date)

        conditions: list[Any] = [SupplierPayment.organization_id == org_id]
        if supplier_id:
            conditions.append(SupplierPayment.supplier_id == coerce_uuid(supplier_id))
        if status_value:
            conditions.append(SupplierPayment.status == status_value)
        if from_date:
            conditions.append(SupplierPayment.payment_date >= from_date)
        if to_date:
            conditions.append(SupplierPayment.payment_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            conditions.append(
                or_(
                    SupplierPayment.payment_number.ilike(search_pattern),
                    SupplierPayment.reference.ilike(search_pattern),
                )
            )

        base_stmt = (
            select(SupplierPayment, Supplier)
            .join(Supplier, SupplierPayment.supplier_id == Supplier.supplier_id)
            .where(*conditions)
        )

        total_count = (
            db.scalar(
                select(func.count(SupplierPayment.payment_id))
                .select_from(SupplierPayment)
                .join(Supplier, SupplierPayment.supplier_id == Supplier.supplier_id)
                .where(*conditions)
            )
            or 0
        )

        column_map = {
            "payment_date": SupplierPayment.payment_date,
            "payment_number": SupplierPayment.payment_number,
            "amount": SupplierPayment.amount,
            "status": SupplierPayment.status,
        }
        stmt = apply_sort(
            base_stmt,
            sort,
            sort_dir,
            column_map,
            default=SupplierPayment.payment_date.desc(),
        )

        payments = db.execute(stmt.limit(limit).offset(offset)).all()

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

        active_filters = build_active_filters(
            params={
                "status": status,
                "supplier_id": supplier_id,
                "start_date": start_date,
                "end_date": end_date,
            },
            labels={"start_date": "From", "end_date": "To"},
            options={
                "supplier_id": {
                    str(s["supplier_id"]): s["supplier_name"] for s in suppliers_list
                }
            },
        )
        return {
            "payments": payments_view,
            "suppliers_list": suppliers_list,
            "search": search,
            "supplier_id": supplier_id,
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
            "sort": sort,
            "sort_dir": sort_dir,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "active_filters": active_filters,
        }

    @staticmethod
    def payment_form_context(
        db: Session,
        organization_id: str,
        invoice_id: str | None = None,
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
            select(SupplierInvoice, Supplier)
            .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
            .where(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.status.in_(open_statuses),
            )
        )

        selected_invoice_id = coerce_uuid(invoice_id) if invoice_id else None
        if selected_invoice_id:
            query = query.where(SupplierInvoice.invoice_id == selected_invoice_id)

        rows = db.execute(query.order_by(SupplierInvoice.due_date)).all()

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
                "balance_raw": float(balance),  # For JS calculations
                "currency_code": invoice.currency_code,
            }
            open_invoices.append(view)
            if selected_invoice_id and invoice.invoice_id == selected_invoice_id:
                selected_invoice = view

        # Get WHT codes for payments
        from app.models.finance.tax.tax_code import TaxCode, TaxType

        wht_codes = db.scalars(
            select(TaxCode)
            .where(
                TaxCode.organization_id == org_id,
                TaxCode.tax_type == TaxType.WITHHOLDING,
                TaxCode.is_active.is_(True),
                TaxCode.applies_to_purchases.is_(True),
            )
            .order_by(TaxCode.tax_code)
        ).all()
        wht_codes_list = [
            {
                "id": str(code.tax_code_id),
                "code": code.tax_code,
                "name": code.tax_name,
                "rate": float(code.tax_rate)
                * 100,  # Convert decimal to percentage for display
            }
            for code in wht_codes
        ]

        # Get bank accounts
        from app.models.finance.gl.account import Account, IFRSCategory

        bank_accounts = db.scalars(
            select(Account)
            .where(
                Account.organization_id == org_id,
                Account.ifrs_category == IFRSCategory.ASSETS,
                Account.is_active.is_(True),
            )
            .order_by(Account.account_name)
        ).all()
        bank_accounts_list = [
            {
                "id": str(acct.account_id),
                "code": acct.account_code,
                "name": acct.account_name,
            }
            for acct in bank_accounts
        ]

        context = {
            "suppliers_list": suppliers_list,
            "invoice_id": invoice_id,
            "invoice": selected_invoice,
            "open_invoices": open_invoices,
            "wht_codes": wht_codes_list,
            "bank_accounts": bank_accounts_list,
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
            invoices = db.scalars(
                select(SupplierInvoice).where(
                    SupplierInvoice.invoice_id.in_(invoice_ids)
                )
            ).all()
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
        as_of_date: str | None,
        supplier_id: str | None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        ref_date = _parse_date(as_of_date)

        if supplier_id:
            summary = ap_aging_service.calculate_supplier_aging(
                db, org_id, coerce_uuid(supplier_id), ref_date
            )
            aging_data = [summary]
        else:
            aging_data = ap_aging_service.get_aging_by_supplier(db, org_id, ref_date)

        suppliers_list = [
            _supplier_option_view(supplier)
            for supplier in supplier_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        # Build template-ready context from raw aging data
        currency = aging_data[0].currency_code if aging_data else "NGN"

        def fmt(v):
            return _format_currency(v, currency)

        # Aggregate totals across all suppliers
        total_current = sum(r.current for r in aging_data)
        total_30 = sum(r.days_31_60 for r in aging_data)
        total_60 = sum(r.days_61_90 for r in aging_data)
        total_90 = sum(r.over_90 for r in aging_data)
        grand_total = total_current + total_30 + total_60 + total_90
        total_invoices = sum(r.invoice_count for r in aging_data)

        def _pct(part: Decimal, whole: Decimal) -> float:
            return round(float(part / whole * 100), 1) if whole else 0.0

        # DPO approximation using bucket midpoints
        if grand_total:
            dpo = int(
                (total_current * 15 + total_30 * 45 + total_60 * 75 + total_90 * 120)
                / grand_total
            )
        else:
            dpo = 0

        aging_summary = (
            {
                "total": fmt(grand_total),
                "invoice_count": total_invoices,
                "current": fmt(total_current),
                "current_pct": _pct(total_current, grand_total),
                "days_30": fmt(total_30),
                "days_30_pct": _pct(total_30, grand_total),
                "days_60": fmt(total_60),
                "days_60_pct": _pct(total_60, grand_total),
                "days_90": fmt(total_90),
                "days_90_pct": _pct(total_90, grand_total),
                "days_90_raw": float(total_90),
                "dpo": dpo,
            }
            if aging_data
            else None
        )

        # Per-supplier rows for the table
        supplier_aging = []
        for r in aging_data:
            row_total = r.current + r.days_31_60 + r.days_61_90 + r.over_90
            supplier_aging.append(
                {
                    "supplier_id": r.supplier_id,
                    "supplier_name": r.supplier_name,
                    "supplier_code": r.supplier_code,
                    "current": fmt(r.current),
                    "current_raw": float(r.current),
                    "days_30": fmt(r.days_31_60),
                    "days_30_raw": float(r.days_31_60),
                    "days_60": fmt(r.days_61_90),
                    "days_60_raw": float(r.days_61_90),
                    "days_90": fmt(r.over_90),
                    "days_90_raw": float(r.over_90),
                    "total": fmt(row_total),
                    "current_pct": _pct(r.current, row_total),
                    "days_30_pct": _pct(r.days_31_60, row_total),
                    "days_60_pct": _pct(r.days_61_90, row_total),
                    "days_90_pct": _pct(r.over_90, row_total),
                }
            )

        # Chart data JSON for the <script> tag
        aging_chart_data = (
            json.dumps(
                {
                    "buckets": {
                        "current": float(total_current),
                        "days_30": float(total_30),
                        "days_60": float(total_60),
                        "days_90": float(total_90),
                    },
                    "currency": currency,
                }
            )
            if aging_data
            else "{}"
        )

        return {
            "aging_summary": aging_summary,
            "supplier_aging": supplier_aging,
            "suppliers": suppliers_list,
            "selected_supplier_id": supplier_id,
            "as_of_date": as_of_date or _format_date(ref_date or date.today()),
            "aging_chart_data": aging_chart_data,
        }

    @staticmethod
    def delete_supplier(
        db: Session,
        organization_id: str,
        supplier_id: str,
    ) -> str | None:
        """Delete a supplier. Returns error message or None on success."""
        org_id = coerce_uuid(organization_id)
        sup_id = coerce_uuid(supplier_id)

        try:
            supplier_service.delete_supplier(db, org_id, sup_id)
            return None
        except HTTPException as exc:
            return exc.detail
        except Exception as e:
            return f"Failed to delete supplier: {str(e)}"

    @staticmethod
    def delete_invoice(
        db: Session,
        organization_id: str,
        invoice_id: str,
    ) -> str | None:
        """Delete an invoice. Returns error message or None on success."""
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        try:
            supplier_invoice_service.delete_invoice(db, org_id, inv_id)
            return None
        except HTTPException as exc:
            return exc.detail
        except Exception as e:
            return f"Failed to delete invoice: {str(e)}"

    @staticmethod
    def build_payment_input(
        db: Session, data: dict, organization_id: UUID
    ) -> SupplierPaymentInput:
        """Build SupplierPaymentInput from form data."""
        payload = dict(data)
        return supplier_payment_service.build_input_from_payload(
            db=db,
            organization_id=organization_id,
            payload=payload,
        )

    @staticmethod
    def delete_payment(
        db: Session,
        organization_id: str,
        payment_id: str,
    ) -> str | None:
        """Delete a payment. Returns error message or None on success."""
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)

        try:
            supplier_payment_service.delete_payment(db, org_id, pay_id)
            return None
        except HTTPException as exc:
            return exc.detail
        except Exception as e:
            return f"Failed to delete payment: {str(e)}"

    # =========================================================================
    # Purchase Orders
    # =========================================================================

    @staticmethod
    def list_purchase_orders_context(
        db: Session,
        organization_id: str,
        search: str | None,
        supplier_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
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

        filters = [PurchaseOrder.organization_id == org_id]
        if supplier_id:
            filters.append(PurchaseOrder.supplier_id == coerce_uuid(supplier_id))
        if status_value:
            filters.append(PurchaseOrder.status == status_value)
        if from_date:
            filters.append(PurchaseOrder.po_date >= from_date)
        if to_date:
            filters.append(PurchaseOrder.po_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            filters.append(
                or_(
                    PurchaseOrder.po_number.ilike(search_pattern),
                    Supplier.legal_name.ilike(search_pattern),
                    Supplier.trading_name.ilike(search_pattern),
                )
            )

        total_count = (
            db.scalar(
                select(func.count(PurchaseOrder.po_id))
                .select_from(PurchaseOrder)
                .join(Supplier, PurchaseOrder.supplier_id == Supplier.supplier_id)
                .where(*filters)
            )
            or 0
        )
        orders = db.execute(
            select(PurchaseOrder, Supplier)
            .join(Supplier, PurchaseOrder.supplier_id == Supplier.supplier_id)
            .where(*filters)
            .order_by(PurchaseOrder.po_date.desc())
            .limit(limit)
            .offset(offset)
        ).all()

        # Build stats
        draft_count = (
            db.scalar(
                select(func.count(PurchaseOrder.po_id)).where(
                    PurchaseOrder.organization_id == org_id,
                    PurchaseOrder.status == POStatus.DRAFT,
                )
            )
            or 0
        )
        pending_count = (
            db.scalar(
                select(func.count(PurchaseOrder.po_id)).where(
                    PurchaseOrder.organization_id == org_id,
                    PurchaseOrder.status == POStatus.PENDING_APPROVAL,
                )
            )
            or 0
        )
        approved_total = db.scalar(
            select(func.coalesce(func.sum(PurchaseOrder.total_amount), 0)).where(
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.status == POStatus.APPROVED,
            )
        ) or Decimal("0")
        open_total = db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        PurchaseOrder.total_amount - PurchaseOrder.amount_received
                    ),
                    0,
                )
            ).where(
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.status.in_(
                    [POStatus.APPROVED, POStatus.PARTIALLY_RECEIVED]
                ),
            )
        ) or Decimal("0")

        orders_view = []
        for po, supplier in orders:
            orders_view.append(
                {
                    "po_id": po.po_id,
                    "po_number": po.po_number,
                    "supplier_name": _supplier_display_name(supplier),
                    "po_date": _format_date(po.po_date),
                    "expected_delivery_date": _format_date(po.expected_delivery_date),
                    "total_amount": _format_currency(po.total_amount, po.currency_code),
                    "amount_received": _format_currency(
                        po.amount_received, po.currency_code
                    ),
                    "status": po.status.value,
                    "currency_code": po.currency_code,
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

        active_filters = build_active_filters(
            params={
                "status": status,
                "supplier_id": supplier_id,
                "start_date": start_date,
                "end_date": end_date,
            },
            labels={"start_date": "From", "end_date": "To"},
        )
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
            "active_filters": active_filters,
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

        lines = db.scalars(
            select(PurchaseOrderLine)
            .where(PurchaseOrderLine.po_id == po_uuid)
            .order_by(PurchaseOrderLine.line_number)
        ).all()

        lines_view = []
        for line in lines:
            lines_view.append(
                {
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
                }
            )

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
        goods_receipts = db.scalars(
            select(GoodsReceipt)
            .where(GoodsReceipt.po_id == po_uuid)
            .order_by(GoodsReceipt.receipt_date.desc())
        ).all()
        receipts_view = []
        for gr in goods_receipts:
            line_count = (
                db.scalar(
                    select(func.count(GoodsReceiptLine.line_id)).where(
                        GoodsReceiptLine.receipt_id == gr.receipt_id
                    )
                )
                or 0
            )
            receipts_view.append(
                {
                    "receipt_id": gr.receipt_id,
                    "receipt_number": gr.receipt_number,
                    "receipt_date": _format_date(gr.receipt_date),
                    "status": gr.status.value,
                    "line_count": line_count,
                    "notes": gr.notes,
                }
            )

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
        po_id: str | None = None,
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
        from app.models.inventory.item import Item

        items = db.scalars(
            select(Item)
            .where(
                Item.organization_id == org_id,
                Item.is_active.is_(True),
                Item.is_purchaseable.is_(True),
            )
            .order_by(Item.item_code)
            .limit(500)
        ).all()
        items_list = [
            {
                "item_id": item.item_id,
                "item_code": item.item_code,
                "item_name": item.item_name,
                "standard_cost": float(item.standard_cost)
                if item.standard_cost
                else None,
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
                po_lines = db.scalars(
                    select(PurchaseOrderLine)
                    .where(PurchaseOrderLine.po_id == po_uuid)
                    .order_by(PurchaseOrderLine.line_number)
                ).all()
                for line in po_lines:
                    lines.append(
                        {
                            "line_id": str(line.line_id),
                            "item_id": str(line.item_id) if line.item_id else "",
                            "description": line.description,
                            "quantity": float(line.quantity_ordered),
                            "unit_price": float(line.unit_price),
                            "tax_amount": float(line.tax_amount)
                            if line.tax_amount
                            else 0,
                            "expense_account_id": str(line.expense_account_id)
                            if line.expense_account_id
                            else "",
                        }
                    )

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
        search: str | None,
        supplier_id: str | None,
        po_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
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

        filters = [GoodsReceipt.organization_id == org_id]
        if supplier_id:
            filters.append(GoodsReceipt.supplier_id == coerce_uuid(supplier_id))
        if po_id:
            filters.append(GoodsReceipt.po_id == coerce_uuid(po_id))
        if status_value:
            filters.append(GoodsReceipt.status == status_value)
        if from_date:
            filters.append(GoodsReceipt.receipt_date >= from_date)
        if to_date:
            filters.append(GoodsReceipt.receipt_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            filters.append(
                or_(
                    GoodsReceipt.receipt_number.ilike(search_pattern),
                    PurchaseOrder.po_number.ilike(search_pattern),
                    Supplier.legal_name.ilike(search_pattern),
                    Supplier.trading_name.ilike(search_pattern),
                )
            )

        total_count = (
            db.scalar(
                select(func.count(GoodsReceipt.receipt_id))
                .select_from(GoodsReceipt)
                .join(Supplier, GoodsReceipt.supplier_id == Supplier.supplier_id)
                .join(PurchaseOrder, GoodsReceipt.po_id == PurchaseOrder.po_id)
                .where(*filters)
            )
            or 0
        )
        receipts = db.execute(
            select(GoodsReceipt, Supplier, PurchaseOrder)
            .join(Supplier, GoodsReceipt.supplier_id == Supplier.supplier_id)
            .join(PurchaseOrder, GoodsReceipt.po_id == PurchaseOrder.po_id)
            .where(*filters)
            .order_by(GoodsReceipt.receipt_date.desc())
            .limit(limit)
            .offset(offset)
        ).all()

        # Build stats
        received_count = (
            db.scalar(
                select(func.count(GoodsReceipt.receipt_id)).where(
                    GoodsReceipt.organization_id == org_id,
                    GoodsReceipt.status == ReceiptStatus.RECEIVED,
                )
            )
            or 0
        )
        inspecting_count = (
            db.scalar(
                select(func.count(GoodsReceipt.receipt_id)).where(
                    GoodsReceipt.organization_id == org_id,
                    GoodsReceipt.status == ReceiptStatus.INSPECTING,
                )
            )
            or 0
        )
        accepted_count = (
            db.scalar(
                select(func.count(GoodsReceipt.receipt_id)).where(
                    GoodsReceipt.organization_id == org_id,
                    GoodsReceipt.status == ReceiptStatus.ACCEPTED,
                )
            )
            or 0
        )

        receipts_view = []
        for gr, supplier, po in receipts:
            # Count lines
            line_count = (
                db.scalar(
                    select(func.count(GoodsReceiptLine.line_id)).where(
                        GoodsReceiptLine.receipt_id == gr.receipt_id
                    )
                )
                or 0
            )
            receipts_view.append(
                {
                    "receipt_id": gr.receipt_id,
                    "receipt_number": gr.receipt_number,
                    "supplier_name": _supplier_display_name(supplier),
                    "po_number": po.po_number,
                    "po_id": po.po_id,
                    "receipt_date": _format_date(gr.receipt_date),
                    "status": gr.status.value,
                    "line_count": line_count,
                    "notes": gr.notes,
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

        active_filters = build_active_filters(
            params={
                "status": status,
                "supplier_id": supplier_id,
                "start_date": start_date,
                "end_date": end_date,
            },
            labels={"start_date": "From", "end_date": "To"},
        )
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
            "active_filters": active_filters,
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

        lines = db.execute(
            select(GoodsReceiptLine, PurchaseOrderLine)
            .join(
                PurchaseOrderLine,
                GoodsReceiptLine.po_line_id == PurchaseOrderLine.line_id,
            )
            .where(GoodsReceiptLine.receipt_id == receipt_uuid)
            .order_by(GoodsReceiptLine.line_number)
        ).all()

        lines_view = []
        for gr_line, po_line in lines:
            lines_view.append(
                {
                    "line_id": gr_line.line_id,
                    "line_number": gr_line.line_number,
                    "description": po_line.description,
                    "quantity_ordered": po_line.quantity_ordered,
                    "quantity_received": gr_line.quantity_received,
                    "quantity_accepted": gr_line.quantity_accepted,
                    "quantity_rejected": gr_line.quantity_rejected,
                    "rejection_reason": gr_line.rejection_reason,
                    "lot_number": gr_line.lot_number,
                    "unit_price": _format_currency(po_line.unit_price, po.currency_code)
                    if po
                    else None,
                }
            )

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
            }
            if po
            else None,
            "lines": lines_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def goods_receipt_form_context(
        db: Session,
        organization_id: str,
        po_id: str | None = None,
    ) -> dict:
        """Build context for goods receipt form (create)."""
        org_id = coerce_uuid(organization_id)

        # Get POs that can receive goods (APPROVED or PARTIALLY_RECEIVED)
        receivable_statuses = [POStatus.APPROVED, POStatus.PARTIALLY_RECEIVED]
        pos = db.execute(
            select(PurchaseOrder, Supplier)
            .join(Supplier, PurchaseOrder.supplier_id == Supplier.supplier_id)
            .where(
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.status.in_(receivable_statuses),
            )
            .order_by(PurchaseOrder.po_date.desc())
            .limit(100)
        ).all()

        po_list = []
        for po, supplier in pos:
            po_list.append(
                {
                    "po_id": str(po.po_id),
                    "po_number": po.po_number,
                    "supplier_id": str(po.supplier_id),
                    "supplier_name": _supplier_display_name(supplier),
                    "po_date": _format_date(po.po_date),
                    "total_amount": _format_currency(po.total_amount, po.currency_code),
                    "currency_code": po.currency_code,
                }
            )

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
                    "supplier_name": _supplier_display_name(supplier)
                    if supplier
                    else "",
                    "po_date": _format_date(po.po_date),
                    "currency_code": po.currency_code,
                }

                lines = db.scalars(
                    select(PurchaseOrderLine)
                    .where(PurchaseOrderLine.po_id == po_uuid)
                    .order_by(PurchaseOrderLine.line_number)
                ).all()

                for line in lines:
                    remaining = line.quantity_ordered - line.quantity_received
                    if remaining > 0:
                        po_lines.append(
                            {
                                "line_id": str(line.line_id),
                                "line_number": line.line_number,
                                "description": line.description,
                                "quantity_ordered": float(line.quantity_ordered),
                                "quantity_received": float(line.quantity_received),
                                "quantity_remaining": float(remaining),
                                "unit_price": float(line.unit_price),
                            }
                        )

        # Get warehouses for selection
        from app.models.inventory.warehouse import Warehouse

        warehouses = db.scalars(
            select(Warehouse)
            .where(
                Warehouse.organization_id == org_id,
                Warehouse.is_active.is_(True),
            )
            .order_by(Warehouse.warehouse_code)
        ).all()
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

    def list_suppliers_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None,
        status: str | None,
        page: int,
        limit: int,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Suppliers", "ap")
        context.update(
            self.list_suppliers_context(
                db,
                str(auth.organization_id),
                search=search,
                status=status,
                page=page,
                limit=limit,
                sort=sort,
                sort_dir=sort_dir,
            )
        )
        return templates.TemplateResponse(request, "finance/ap/suppliers.html", context)

    def supplier_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Supplier", "ap")
        context.update(self.supplier_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "finance/ap/supplier_form.html", context
        )

    def supplier_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        supplier_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Supplier Details", "ap")
        context.update(
            self.supplier_detail_context(
                db,
                str(auth.organization_id),
                supplier_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/supplier_detail.html", context
        )

    def supplier_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        supplier_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Edit Supplier", "ap")
        context.update(
            self.supplier_form_context(db, str(auth.organization_id), supplier_id)
        )
        return templates.TemplateResponse(
            request, "finance/ap/supplier_form.html", context
        )

    async def create_supplier_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        form_data = await request.form()

        try:
            input_data = self.build_supplier_input(
                db, dict(form_data), auth.organization_id
            )

            supplier_service.create_supplier(
                db=db,
                organization_id=auth.organization_id,
                input=input_data,
            )

            return RedirectResponse(
                url="/finance/ap/suppliers?success=Supplier+created+successfully",
                status_code=303,
            )

        except Exception as e:
            context = base_context(request, auth, "New Supplier", "ap")
            context.update(self.supplier_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = dict(form_data)
            return templates.TemplateResponse(
                request, "finance/ap/supplier_form.html", context
            )

    async def update_supplier_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        supplier_id: str,
    ) -> HTMLResponse | RedirectResponse:
        form_data = await request.form()

        try:
            input_data = self.build_supplier_input(
                db, dict(form_data), auth.organization_id
            )

            supplier_service.update_supplier(
                db=db,
                organization_id=auth.organization_id,
                supplier_id=UUID(supplier_id),
                input=input_data,
            )

            return RedirectResponse(
                url="/finance/ap/suppliers?success=Supplier+updated+successfully",
                status_code=303,
            )

        except Exception as e:
            context = base_context(request, auth, "Edit Supplier", "ap")
            context.update(
                self.supplier_form_context(db, str(auth.organization_id), supplier_id)
            )
            context["error"] = str(e)
            context["form_data"] = dict(form_data)
            return templates.TemplateResponse(
                request, "finance/ap/supplier_form.html", context
            )

    def delete_supplier_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        supplier_id: str,
    ) -> HTMLResponse | RedirectResponse:
        error = self.delete_supplier(db, str(auth.organization_id), supplier_id)

        if error:
            context = base_context(request, auth, "Supplier Details", "ap")
            context.update(
                self.supplier_detail_context(
                    db,
                    str(auth.organization_id),
                    supplier_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(
                request, "finance/ap/supplier_detail.html", context
            )

        return RedirectResponse(
            url="/finance/ap/suppliers?success=Record+deleted+successfully",
            status_code=303,
        )

    def list_invoices_response(
        self,
        request: Request,
        auth: WebAuthContext,
        search: str | None,
        supplier_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        db: Session,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> HTMLResponse:
        context = base_context(request, auth, "AP Invoices", "ap")
        context.update(
            self.list_invoices_context(
                db,
                str(auth.organization_id),
                search=search,
                supplier_id=supplier_id,
                status=status,
                start_date=start_date,
                end_date=end_date,
                page=page,
                sort=sort,
                sort_dir=sort_dir,
            )
        )
        return templates.TemplateResponse(request, "finance/ap/invoices.html", context)

    def invoice_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        supplier_id: str | None,
        po_id: str | None,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New AP Invoice", "ap")
        context.update(
            self.invoice_form_context(
                db,
                str(auth.organization_id),
                supplier_id=supplier_id,
                po_id=po_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/invoice_form.html", context
        )

    async def create_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | JSONResponse | RedirectResponse | dict:
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            input_data = self.build_invoice_input(db, data, auth.organization_id)

            invoice = supplier_invoice_service.create_invoice(
                db=db,
                organization_id=auth.organization_id,
                input=input_data,
                created_by_user_id=auth.person_id,
            )

            if "application/json" in content_type:
                return {"success": True, "invoice_id": str(invoice.invoice_id)}

            return RedirectResponse(
                url="/finance/ap/invoices?success=Invoice+created+successfully",
                status_code=303,
            )

        except Exception as e:
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": str(e)},
                )

            context = base_context(request, auth, "New AP Invoice", "ap")
            context.update(self.invoice_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ap/invoice_form.html", context
            )

    def invoice_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "AP Invoice Details", "ap")
        context.update(
            self.invoice_detail_context(
                db,
                str(auth.organization_id),
                invoice_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/invoice_detail.html", context
        )

    def delete_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> HTMLResponse | RedirectResponse:
        error = self.delete_invoice(db, str(auth.organization_id), invoice_id)

        if error:
            context = base_context(request, auth, "AP Invoice Details", "ap")
            context.update(
                self.invoice_detail_context(
                    db,
                    str(auth.organization_id),
                    invoice_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(
                request, "finance/ap/invoice_detail.html", context
            )

        return RedirectResponse(
            url="/finance/ap/invoices?success=Record+deleted+successfully",
            status_code=303,
        )

    def invoice_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Return the edit invoice form with existing invoice data."""
        org_id = coerce_uuid(auth.organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            return RedirectResponse(
                url="/finance/ap/invoices?success=Record+updated+successfully",
                status_code=303,
            )

        if invoice.status != SupplierInvoiceStatus.DRAFT:
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?error=Only+draft+invoices+can+be+edited",
                status_code=303,
            )

        context = base_context(request, auth, "Edit AP Invoice", "ap")
        context.update(self.invoice_form_context(db, str(auth.organization_id)))

        # Add existing invoice data
        db.get(Supplier, invoice.supplier_id)
        lines = db.scalars(
            select(SupplierInvoiceLine)
            .where(SupplierInvoiceLine.invoice_id == inv_id)
            .order_by(SupplierInvoiceLine.line_number)
        ).all()

        context["invoice"] = {
            "invoice_id": invoice.invoice_id,
            "invoice_number": invoice.invoice_number,
            "supplier_id": invoice.supplier_id,
            "invoice_date": invoice.invoice_date,
            "due_date": invoice.due_date,
            "currency_code": invoice.currency_code,
            "vendor_invoice_number": invoice.vendor_invoice_number,
            "description": invoice.description,
            "notes": invoice.notes,
            "internal_notes": invoice.internal_notes,
            "lines": [
                {
                    "line_id": line.line_id,
                    "expense_account_id": line.expense_account_id,
                    "description": line.description,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "tax_code_id": line.tax_code_id,
                    "tax_amount": line.tax_amount,
                    "cost_center_id": line.cost_center_id,
                    "project_id": line.project_id,
                }
                for line in lines
            ],
        }

        return templates.TemplateResponse(
            request, "finance/ap/invoice_form.html", context
        )

    async def update_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> HTMLResponse | JSONResponse | RedirectResponse:
        """Handle invoice update form submission."""
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            input_data = self.build_invoice_input(db, data, auth.organization_id)

            invoice = supplier_invoice_service.update_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(invoice_id),
                input=input_data,
                updated_by_user_id=auth.user_id,
            )

            if "application/json" in content_type:
                return JSONResponse(
                    content={"success": True, "invoice_id": str(invoice.invoice_id)}
                )

            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice.invoice_id}?success=Invoice+updated+successfully",
                status_code=303,
            )

        except Exception as e:
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": str(e)},
                )

            context = base_context(request, auth, "Edit AP Invoice", "ap")
            context.update(self.invoice_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ap/invoice_form.html", context
            )

    def submit_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> RedirectResponse:
        """Submit invoice for approval."""
        try:
            supplier_invoice_service.submit_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(invoice_id),
                submitted_by_user_id=auth.user_id,
            )
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?success=Invoice+submitted+for+approval",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?error={str(e)}",
                status_code=303,
            )

    def approve_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> RedirectResponse:
        """Approve a submitted invoice."""
        try:
            supplier_invoice_service.approve_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(invoice_id),
                approved_by_user_id=auth.user_id,
            )
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?success=Invoice+approved",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?error={str(e)}",
                status_code=303,
            )

    def post_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> RedirectResponse:
        """Post invoice to general ledger."""
        try:
            supplier_invoice_service.post_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(invoice_id),
                posted_by_user_id=auth.user_id,
            )
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?success=Invoice+posted+to+ledger",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?error={str(e)}",
                status_code=303,
            )

    def void_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> RedirectResponse:
        """Void an invoice."""
        try:
            supplier_invoice_service.void_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(invoice_id),
                voided_by_user_id=auth.user_id,
                reason="Voided via web interface",
            )
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?success=Invoice+voided",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?error={str(e)}",
                status_code=303,
            )

    async def add_invoice_comment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> RedirectResponse:
        """Append an internal comment to an AP invoice."""
        org_id = auth.organization_id
        user_id = auth.person_id or auth.user_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        try:
            invoice = supplier_invoice_service.get(
                db, invoice_id, organization_id=org_id
            )
        except Exception:
            return RedirectResponse(
                url="/finance/ap/invoices?error=Invoice+not+found",
                status_code=303,
            )

        form = await request.form()
        comment_text = str(form.get("comment", "")).strip()
        if not comment_text:
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?error=Comment+is+required#comments",
                status_code=303,
            )

        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        entry = f"[{timestamp}] {user_id}: {comment_text}"
        invoice.comments = f"{invoice.comments}\n{entry}" if invoice.comments else entry

        mentioned_person_ids: set[UUID] = set()

        # Support @email mentions.
        mention_pattern = re.compile(
            r"@([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
        )
        mentioned_emails = {m.lower() for m in mention_pattern.findall(comment_text)}
        if mentioned_emails:
            email_matches = db.scalars(
                select(Person).where(
                    Person.organization_id == org_id,
                    func.lower(Person.email).in_(mentioned_emails),
                )
            ).all()
            mentioned_person_ids.update(person.id for person in email_matches)

        # Support @Display Name mentions (for inline tagging in comment text).
        if "@" in comment_text:
            normalized_comment = " ".join(comment_text.lower().split())
            org_people = db.scalars(
                select(Person).where(Person.organization_id == org_id)
            ).all()
            for person in org_people:
                candidate_names = {
                    (person.display_name or "").strip(),
                    f"{person.first_name} {person.last_name}".strip(),
                }
                for candidate in candidate_names:
                    if not candidate:
                        continue
                    candidate_norm = " ".join(candidate.lower().split())
                    needle = f"@{candidate_norm}"
                    pos = normalized_comment.find(needle)
                    while pos != -1:
                        end_pos = pos + len(needle)
                        if (
                            end_pos == len(normalized_comment)
                            or not normalized_comment[end_pos].isalnum()
                        ):
                            mentioned_person_ids.add(person.id)
                            break
                        pos = normalized_comment.find(needle, pos + 1)

        if mentioned_person_ids:
            actor_id = coerce_uuid(user_id)
            actor = db.get(Person, actor_id)
            actor_name = actor.name if actor else "A teammate"
            mentioned_people = db.scalars(
                select(Person).where(Person.id.in_(mentioned_person_ids))
            ).all()
            for person in mentioned_people:
                if person.id == actor_id:
                    continue
                notification_service.create(
                    db=db,
                    organization_id=coerce_uuid(org_id),
                    recipient_id=person.id,
                    entity_type=EntityType.INVOICE,
                    entity_id=coerce_uuid(invoice.invoice_id),
                    notification_type=NotificationType.MENTION,
                    title=f"Mentioned on AP invoice {invoice.invoice_number}",
                    message=(
                        f"{actor_name} mentioned you in a comment on invoice "
                        f"{invoice.invoice_number}."
                    ),
                    action_url=f"/finance/ap/invoices/{invoice_id}#comments",
                    actor_id=actor_id,
                )
        db.commit()

        return RedirectResponse(
            url=f"/finance/ap/invoices/{invoice_id}?success=Comment+added#comments",
            status_code=303,
        )

    def list_payments_response(
        self,
        request: Request,
        auth: WebAuthContext,
        search: str | None,
        supplier_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        db: Session,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> HTMLResponse:
        context = base_context(request, auth, "AP Payments", "ap")
        context.update(
            self.list_payments_context(
                db,
                str(auth.organization_id),
                search=search,
                supplier_id=supplier_id,
                status=status,
                start_date=start_date,
                end_date=end_date,
                page=page,
                sort=sort,
                sort_dir=sort_dir,
            )
        )
        return templates.TemplateResponse(request, "finance/ap/payments.html", context)

    def payment_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New AP Payment", "ap")
        context.update(
            self.payment_form_context(
                db,
                str(auth.organization_id),
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/payment_form.html", context
        )

    def payment_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        payment_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "AP Payment Details", "ap")
        context.update(
            self.payment_detail_context(
                db,
                str(auth.organization_id),
                payment_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/payment_detail.html", context
        )

    async def create_payment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | JSONResponse | RedirectResponse | dict:
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            input_data = self.build_payment_input(db, data, auth.organization_id)

            payment = supplier_payment_service.create_payment(
                db=db,
                organization_id=auth.organization_id,
                input=input_data,
                created_by_user_id=auth.person_id,
            )

            if "application/json" in content_type:
                return {"success": True, "payment_id": str(payment.payment_id)}

            return RedirectResponse(
                url="/finance/ap/payments?success=Payment+created+successfully",
                status_code=303,
            )

        except Exception as e:
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": str(e)},
                )

            context = base_context(request, auth, "New AP Payment", "ap")
            context.update(self.payment_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ap/payment_form.html", context
            )

    def delete_payment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        payment_id: str,
    ) -> HTMLResponse | RedirectResponse:
        error = self.delete_payment(db, str(auth.organization_id), payment_id)

        if error:
            context = base_context(request, auth, "AP Payment Details", "ap")
            context.update(
                self.payment_detail_context(
                    db,
                    str(auth.organization_id),
                    payment_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(
                request, "finance/ap/payment_detail.html", context
            )

        return RedirectResponse(
            url="/finance/ap/payments?success=Record+deleted+successfully",
            status_code=303,
        )

    def payment_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        payment_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Return the edit payment form with existing payment data."""
        org_id = coerce_uuid(auth.organization_id)
        pay_id = coerce_uuid(payment_id)

        payment = db.get(SupplierPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            return RedirectResponse(
                url="/finance/ap/payments?success=Record+updated+successfully",
                status_code=303,
            )

        if payment.status != APPaymentStatus.DRAFT:
            return RedirectResponse(
                url=f"/finance/ap/payments/{payment_id}?error=Only+draft+payments+can+be+edited",
                status_code=303,
            )

        context = base_context(request, auth, "Edit AP Payment", "ap")
        context.update(self.payment_form_context(db, str(auth.organization_id)))

        # Add existing payment data
        context["payment"] = {
            "payment_id": payment.payment_id,
            "payment_number": payment.payment_number,
            "supplier_id": payment.supplier_id,
            "payment_date": payment.payment_date,
            "payment_method": payment.payment_method.value
            if payment.payment_method
            else "",
            "currency_code": payment.currency_code,
            "amount": payment.amount,
            "reference": payment.reference,
            "description": payment.description,
            "bank_account_id": payment.bank_account_id,
        }

        return templates.TemplateResponse(
            request, "finance/ap/payment_form.html", context
        )

    async def update_payment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        payment_id: str,
    ) -> HTMLResponse | JSONResponse | RedirectResponse:
        """Handle payment update form submission."""
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            # Update payment - for now redirect back with error since full update isn't implemented
            return RedirectResponse(
                url=f"/finance/ap/payments/{payment_id}?error=Payment+update+not+yet+implemented",
                status_code=303,
            )

        except Exception as e:
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": str(e)},
                )

            context = base_context(request, auth, "Edit AP Payment", "ap")
            context.update(self.payment_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ap/payment_form.html", context
            )

    def approve_payment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        payment_id: str,
    ) -> RedirectResponse:
        """Approve payment."""
        try:
            supplier_payment_service.approve_payment(
                db=db,
                organization_id=auth.organization_id,
                payment_id=coerce_uuid(payment_id),
                approved_by_user_id=auth.user_id,
            )
            return RedirectResponse(
                url=f"/finance/ap/payments/{payment_id}?success=Payment+approved",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ap/payments/{payment_id}?error={str(e)}",
                status_code=303,
            )

    def post_payment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        payment_id: str,
    ) -> RedirectResponse:
        """Post payment to general ledger."""
        try:
            supplier_payment_service.post_payment(
                db=db,
                organization_id=auth.organization_id,
                payment_id=coerce_uuid(payment_id),
                posted_by_user_id=auth.user_id,
            )
            return RedirectResponse(
                url=f"/finance/ap/payments/{payment_id}?success=Payment+posted+to+ledger",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ap/payments/{payment_id}?error={str(e)}",
                status_code=303,
            )

    def void_payment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        payment_id: str,
    ) -> RedirectResponse:
        """Void a payment."""
        try:
            supplier_payment_service.void_payment(
                db=db,
                organization_id=auth.organization_id,
                payment_id=coerce_uuid(payment_id),
                voided_by_user_id=auth.user_id,
                reason="Voided via web interface",
            )
            return RedirectResponse(
                url=f"/finance/ap/payments/{payment_id}?success=Payment+voided",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ap/payments/{payment_id}?error={str(e)}",
                status_code=303,
            )

    def list_payment_batches_response(
        self,
        request: Request,
        auth: WebAuthContext,
        status: str | None,
        page: int,
        db: Session,
    ) -> HTMLResponse:
        status_value = None
        if status:
            try:
                status_value = APBatchStatus(status)
            except ValueError:
                status_value = None

        limit = 50
        offset = (page - 1) * limit
        batches = payment_batch_service.list(
            db=db,
            organization_id=str(auth.organization_id),
            status=status_value,
            limit=limit,
            offset=offset,
        )

        context = base_context(request, auth, "Payment Batches", "ap")
        context.update(
            {
                "batches": batches,
                "status": status or "",
                "page": page,
            }
        )
        return templates.TemplateResponse(
            request, "finance/ap/payment_batches.html", context
        )

    def payment_batch_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        bank_accounts = bank_account_service.list(
            db=db,
            organization_id=auth.organization_id,
            status=BankAccountStatus.active,
            limit=200,
        )
        invoices = db.execute(
            select(SupplierInvoice, Supplier)
            .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
            .where(SupplierInvoice.organization_id == auth.organization_id)
            .order_by(SupplierInvoice.invoice_date.desc())
            .limit(50)
        ).all()
        invoices_view = [
            {
                "invoice_id": invoice.invoice_id,
                "invoice_number": invoice.invoice_number,
                "supplier_name": supplier.trading_name or supplier.legal_name,
                "due_date": invoice.due_date,
                "amount": invoice.total_amount,
                "currency_code": invoice.currency_code,
            }
            for invoice, supplier in invoices
        ]

        context = base_context(request, auth, "New Payment Batch", "ap")
        context.update(
            {
                "bank_accounts": bank_accounts,
                "invoices": invoices_view,
                "payment_methods": [method.value for method in APPaymentMethod],
            }
        )
        context.update(get_currency_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "finance/ap/payment_batch_form.html", context
        )

    def list_purchase_orders_response(
        self,
        request: Request,
        auth: WebAuthContext,
        search: str | None,
        supplier_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Purchase Orders", "ap", db=db)
        context.update(
            self.list_purchase_orders_context(
                db,
                str(auth.organization_id),
                search=search,
                supplier_id=supplier_id,
                status=status,
                start_date=start_date,
                end_date=end_date,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/purchase_orders.html", context
        )

    def purchase_order_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Purchase Order", "ap")
        context.update(self.purchase_order_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "finance/ap/purchase_order_form.html", context
        )

    def purchase_order_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        po_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Purchase Order Details", "ap")
        context.update(
            self.purchase_order_detail_context(
                db,
                str(auth.organization_id),
                po_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/purchase_order_detail.html", context
        )

    def purchase_order_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        po_id: str,
    ) -> HTMLResponse | RedirectResponse:
        context = base_context(request, auth, "Edit Purchase Order", "ap")
        context.update(
            self.purchase_order_form_context(db, str(auth.organization_id), po_id)
        )
        if not context.get("order"):
            return RedirectResponse(
                url="/finance/ap/purchase-orders?success=Record+updated+successfully",
                status_code=303,
            )
        return templates.TemplateResponse(
            request, "finance/ap/purchase_order_form.html", context
        )

    async def create_purchase_order_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | JSONResponse | RedirectResponse | dict:
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            input_data = purchase_order_service.build_input_from_payload(
                db=db,
                organization_id=auth.organization_id,
                payload=dict(data),
            )

            po = purchase_order_service.create_po(
                db=db,
                organization_id=auth.organization_id,
                input=input_data,
                created_by_user_id=auth.person_id,
            )

            if "application/json" in content_type:
                return {"success": True, "po_id": str(po.po_id)}

            return RedirectResponse(
                url=f"/ap/purchase-orders/{po.po_id}?saved=1",
                status_code=303,
            )

        except Exception as e:
            if "application/json" in content_type:
                return JSONResponse(status_code=400, content={"detail": str(e)})

            context = base_context(request, auth, "New Purchase Order", "ap")
            context.update(
                self.purchase_order_form_context(db, str(auth.organization_id))
            )
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ap/purchase_order_form.html", context
            )

    def submit_purchase_order_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        po_id: str,
    ) -> HTMLResponse | RedirectResponse:
        try:
            purchase_order_service.submit_for_approval(
                db=db,
                organization_id=auth.organization_id,
                po_id=UUID(po_id),
            )
            return RedirectResponse(
                url=f"/ap/purchase-orders/{po_id}?success=Submitted+for+approval",
                status_code=303,
            )
        except Exception as e:
            context = base_context(request, auth, "Purchase Order Details", "ap")
            context.update(
                self.purchase_order_detail_context(
                    db,
                    str(auth.organization_id),
                    po_id,
                )
            )
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "finance/ap/purchase_order_detail.html", context
            )

    def approve_purchase_order_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        po_id: str,
    ) -> HTMLResponse | RedirectResponse:
        try:
            purchase_order_service.approve_po(
                db=db,
                organization_id=auth.organization_id,
                po_id=UUID(po_id),
                approved_by_user_id=auth.person_id,
            )
            return RedirectResponse(
                url=f"/ap/purchase-orders/{po_id}?success=Purchase+order+approved",
                status_code=303,
            )
        except Exception as e:
            context = base_context(request, auth, "Purchase Order Details", "ap")
            context.update(
                self.purchase_order_detail_context(
                    db,
                    str(auth.organization_id),
                    po_id,
                )
            )
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "finance/ap/purchase_order_detail.html", context
            )

    def cancel_purchase_order_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        po_id: str,
    ) -> HTMLResponse | RedirectResponse:
        try:
            purchase_order_service.cancel_po(
                db=db,
                organization_id=auth.organization_id,
                po_id=UUID(po_id),
            )
            return RedirectResponse(
                url=f"/ap/purchase-orders/{po_id}?success=Purchase+order+cancelled",
                status_code=303,
            )
        except Exception as e:
            context = base_context(request, auth, "Purchase Order Details", "ap")
            context.update(
                self.purchase_order_detail_context(
                    db,
                    str(auth.organization_id),
                    po_id,
                )
            )
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "finance/ap/purchase_order_detail.html", context
            )

    def list_goods_receipts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        search: str | None,
        supplier_id: str | None,
        po_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Goods Receipts", "ap")
        context.update(
            self.list_goods_receipts_context(
                db,
                str(auth.organization_id),
                search=search,
                supplier_id=supplier_id,
                po_id=po_id,
                status=status,
                start_date=start_date,
                end_date=end_date,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/goods_receipts.html", context
        )

    def goods_receipt_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        po_id: str | None,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Goods Receipt", "ap")
        context.update(
            self.goods_receipt_form_context(db, str(auth.organization_id), po_id)
        )
        return templates.TemplateResponse(
            request, "finance/ap/goods_receipt_form.html", context
        )

    def goods_receipt_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        receipt_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Goods Receipt Details", "ap")
        context.update(
            self.goods_receipt_detail_context(
                db,
                str(auth.organization_id),
                receipt_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/goods_receipt_detail.html", context
        )

    async def create_goods_receipt_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | JSONResponse | RedirectResponse | dict:
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            input_data = goods_receipt_service.build_input_from_payload(
                db=db,
                organization_id=auth.organization_id,
                payload=dict(data),
            )

            receipt = goods_receipt_service.create_receipt(
                db=db,
                organization_id=auth.organization_id,
                input=input_data,
                received_by_user_id=auth.person_id,
            )

            if "application/json" in content_type:
                return {"success": True, "receipt_id": str(receipt.receipt_id)}

            return RedirectResponse(
                url=f"/ap/goods-receipts/{receipt.receipt_id}?saved=1",
                status_code=303,
            )

        except Exception as e:
            if "application/json" in content_type:
                return JSONResponse(status_code=400, content={"detail": str(e)})

            context = base_context(request, auth, "New Goods Receipt", "ap")
            context.update(
                self.goods_receipt_form_context(
                    db,
                    str(auth.organization_id),
                    data.get("po_id"),
                )
            )
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ap/goods_receipt_form.html", context
            )

    def start_inspection_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        receipt_id: str,
    ) -> HTMLResponse | RedirectResponse:
        try:
            goods_receipt_service.start_inspection(
                db=db,
                organization_id=auth.organization_id,
                receipt_id=UUID(receipt_id),
            )
            return RedirectResponse(
                url=f"/ap/goods-receipts/{receipt_id}?success=Inspection+started",
                status_code=303,
            )
        except Exception as e:
            context = base_context(request, auth, "Goods Receipt Details", "ap")
            context.update(
                self.goods_receipt_detail_context(
                    db,
                    str(auth.organization_id),
                    receipt_id,
                )
            )
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "finance/ap/goods_receipt_detail.html", context
            )

    def accept_all_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        receipt_id: str,
    ) -> HTMLResponse | RedirectResponse:
        try:
            goods_receipt_service.accept_all(
                db=db,
                organization_id=auth.organization_id,
                receipt_id=UUID(receipt_id),
            )
            return RedirectResponse(
                url=f"/ap/goods-receipts/{receipt_id}?success=All+items+accepted",
                status_code=303,
            )
        except Exception as e:
            context = base_context(request, auth, "Goods Receipt Details", "ap")
            context.update(
                self.goods_receipt_detail_context(
                    db,
                    str(auth.organization_id),
                    receipt_id,
                )
            )
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "finance/ap/goods_receipt_detail.html", context
            )

    def aging_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        as_of_date: str | None,
        supplier_id: str | None,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "AP Aging Report", "ap")
        context.update(
            self.aging_context(
                db,
                str(auth.organization_id),
                as_of_date=as_of_date,
                supplier_id=supplier_id,
            )
        )
        return templates.TemplateResponse(request, "finance/ap/aging.html", context)

    async def upload_invoice_attachment_response(
        self,
        invoice_id: str,
        file: UploadFile,
        description: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        try:
            invoice = supplier_invoice_service.get(db, invoice_id)
            if not invoice or invoice.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/ap/invoices/{invoice_id}?error=Invoice+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="SUPPLIER_INVOICE",
                entity_id=invoice_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                category=AttachmentCategory.INVOICE,
                description=description,
            )

            attachment_service.save_file(
                db=db,
                organization_id=auth.organization_id,
                input=input_data,
                file_content=file.file,
                uploaded_by=auth.person_id,
            )

            return RedirectResponse(
                url=f"/ap/invoices/{invoice_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/ap/invoices/{invoice_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            return RedirectResponse(
                url=f"/ap/invoices/{invoice_id}?error=Upload+failed",
                status_code=303,
            )

    async def upload_po_attachment_response(
        self,
        po_id: str,
        file: UploadFile,
        description: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        try:
            po = purchase_order_service.get(db, po_id)
            if not po or po.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/ap/purchase-orders/{po_id}?error=Purchase+order+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="PURCHASE_ORDER",
                entity_id=po_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                category=AttachmentCategory.PURCHASE_ORDER,
                description=description,
            )

            attachment_service.save_file(
                db=db,
                organization_id=auth.organization_id,
                input=input_data,
                file_content=file.file,
                uploaded_by=auth.person_id,
            )

            return RedirectResponse(
                url=f"/ap/purchase-orders/{po_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/ap/purchase-orders/{po_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            return RedirectResponse(
                url=f"/ap/purchase-orders/{po_id}?error=Upload+failed",
                status_code=303,
            )

    async def upload_goods_receipt_attachment_response(
        self,
        receipt_id: str,
        file: UploadFile,
        description: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        try:
            receipt = goods_receipt_service.get(db, receipt_id)
            if not receipt or receipt.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/ap/goods-receipts/{receipt_id}?error=Goods+receipt+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="GOODS_RECEIPT",
                entity_id=receipt_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                category=AttachmentCategory.GOODS_RECEIPT,
                description=description,
            )

            attachment_service.save_file(
                db=db,
                organization_id=auth.organization_id,
                input=input_data,
                file_content=file.file,
                uploaded_by=auth.person_id,
            )

            return RedirectResponse(
                url=f"/ap/goods-receipts/{receipt_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/ap/goods-receipts/{receipt_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            return RedirectResponse(
                url=f"/ap/goods-receipts/{receipt_id}?error=Upload+failed",
                status_code=303,
            )

    async def upload_payment_attachment_response(
        self,
        payment_id: str,
        file: UploadFile,
        description: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        try:
            payment = supplier_payment_service.get(db, payment_id)
            if not payment or payment.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/ap/payments/{payment_id}?error=Payment+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="SUPPLIER_PAYMENT",
                entity_id=payment_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                category=AttachmentCategory.PAYMENT,
                description=description,
            )

            attachment_service.save_file(
                db=db,
                organization_id=auth.organization_id,
                input=input_data,
                file_content=file.file,
                uploaded_by=auth.person_id,
            )

            return RedirectResponse(
                url=f"/ap/payments/{payment_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/ap/payments/{payment_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            return RedirectResponse(
                url=f"/ap/payments/{payment_id}?error=Upload+failed",
                status_code=303,
            )

    async def upload_supplier_attachment_response(
        self,
        supplier_id: str,
        file: UploadFile,
        description: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        try:
            supplier = supplier_service.get(db, auth.organization_id, supplier_id)
            if not supplier or supplier.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/ap/suppliers/{supplier_id}?error=Supplier+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="SUPPLIER",
                entity_id=supplier_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                category=AttachmentCategory.SUPPLIER,
                description=description,
            )

            attachment_service.save_file(
                db=db,
                organization_id=auth.organization_id,
                input=input_data,
                file_content=file.file,
                uploaded_by=auth.person_id,
            )

            return RedirectResponse(
                url=f"/ap/suppliers/{supplier_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/ap/suppliers/{supplier_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            return RedirectResponse(
                url=f"/ap/suppliers/{supplier_id}?error=Upload+failed",
                status_code=303,
            )

    def download_attachment_response(
        self,
        attachment_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> FileResponse | RedirectResponse:
        attachment = attachment_service.get(db, auth.organization_id, attachment_id)

        if not attachment or attachment.organization_id != auth.organization_id:
            return RedirectResponse(
                url="/finance/ap/invoices?error=Attachment+not+found", status_code=303
            )

        # Files are stored in S3; stream through authenticated /files endpoint.
        return RedirectResponse(
            url=f"/files/attachments/{attachment_id}",
            status_code=302,
        )

    def delete_attachment_response(
        self,
        attachment_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        attachment = attachment_service.get(db, auth.organization_id, attachment_id)

        if not attachment or attachment.organization_id != auth.organization_id:
            return RedirectResponse(
                url="/finance/ap/invoices?error=Attachment+not+found", status_code=303
            )

        entity_type = attachment.entity_type
        entity_id = attachment.entity_id

        attachment_service.delete(db, attachment_id, auth.organization_id)

        redirect_map = {
            "SUPPLIER_INVOICE": f"/ap/invoices/{entity_id}",
            "PURCHASE_ORDER": f"/ap/purchase-orders/{entity_id}",
            "GOODS_RECEIPT": f"/ap/goods-receipts/{entity_id}",
            "SUPPLIER_PAYMENT": f"/ap/payments/{entity_id}",
            "SUPPLIER": f"/ap/suppliers/{entity_id}",
        }

        redirect_url = redirect_map.get(entity_type, "/ap/invoices")
        return RedirectResponse(
            url=f"{redirect_url}?success=Attachment+deleted",
            status_code=303,
        )

    @staticmethod
    def supplier_typeahead(
        db: Session,
        organization_id: str,
        query: str,
        limit: int = 8,
    ) -> dict:
        """Search active suppliers for typeahead/autocomplete."""
        from app.services.finance.ap.web.supplier_web import supplier_web_service

        return supplier_web_service.supplier_typeahead(
            db=db,
            organization_id=organization_id,
            query=query,
            limit=limit,
        )

    @staticmethod
    def people_search(
        db: Session,
        organization_id: str,
        query: str,
        limit: int = 25,
    ) -> dict:
        """Search people by name/email for comment @mentions."""
        from app.services.finance.ap.web.supplier_web import supplier_web_service

        return supplier_web_service.people_search(
            db=db,
            organization_id=organization_id,
            query=query,
            limit=limit,
        )


ap_web_service = APWebService()
