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
from app.models.ifrs.core_org.cost_center import CostCenter
from app.models.ifrs.core_org.project import Project
from app.models.ifrs.gl.account import Account
from app.models.ifrs.gl.account_category import AccountCategory, IFRSCategory
from app.services.common import coerce_uuid
from app.services.ifrs.ap.ap_aging import ap_aging_service
from app.services.ifrs.ap.supplier import SupplierInput, supplier_service
from app.services.ifrs.ap.supplier_invoice import InvoiceLineInput, SupplierInvoiceInput


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_date(value: Optional[date]) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _format_currency(amount: Optional[Decimal], currency: str = "USD") -> Optional[str]:
    if amount is None:
        return None
    value = Decimal(str(amount))
    if currency == "USD":
        return f"${value:,.2f}"
    return f"{currency} {value:,.2f}"


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


def _invoice_status_label(status: SupplierInvoiceStatus) -> str:
    if status == SupplierInvoiceStatus.PENDING_APPROVAL:
        return "PENDING"
    if status == SupplierInvoiceStatus.PARTIALLY_PAID:
        return "PARTIAL"
    return status.value


def _payment_status_label(status: APPaymentStatus) -> str:
    if status in {APPaymentStatus.SENT, APPaymentStatus.CLEARED}:
        return "POSTED"
    if status == APPaymentStatus.VOID:
        return "VOIDED"
    return status.value


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
            currency_code=form_data.get("currency_code", "USD"),
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
            currency_code=data.get("currency_code", "USD"),
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
                supplier = supplier_service.get(db, supplier_id)
            except Exception:
                supplier = None
        supplier_view = _supplier_form_view(supplier) if supplier else None

        expense_accounts = _get_accounts(db, org_id, IFRSCategory.EXPENSES)
        payable_accounts = _get_accounts(db, org_id, IFRSCategory.LIABILITIES, "AP")

        return {
            "supplier": supplier_view,
            "expense_accounts": expense_accounts,
            "payable_accounts": payable_accounts,
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

        return {
            "suppliers_list": suppliers_list,
            "expense_accounts": expense_accounts,
            "cost_centers": _get_cost_centers(db, org_id),
            "projects": _get_projects(db, org_id),
            "organization_id": organization_id,
            "user_id": "00000000-0000-0000-0000-000000000001",
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


ap_web_service = APWebService()
