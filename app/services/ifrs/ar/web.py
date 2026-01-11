"""
AR web view service.

Provides view-focused data for AR web routes.
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

from app.models.ifrs.ar.customer import Customer, CustomerType, RiskCategory
from app.models.ifrs.ar.customer_payment import CustomerPayment, PaymentStatus
from app.models.ifrs.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.ifrs.ar.invoice_line import InvoiceLine
from app.models.ifrs.ar.payment_allocation import PaymentAllocation
from app.models.ifrs.core_org.cost_center import CostCenter
from app.models.ifrs.core_org.project import Project
from app.models.ifrs.gl.account import Account
from app.models.ifrs.gl.account_category import AccountCategory, IFRSCategory
from app.config import settings
from app.services.common import coerce_uuid
from app.services.ifrs.ar.ar_aging import ar_aging_service
from app.services.ifrs.ar.customer import CustomerInput, customer_service
from app.services.ifrs.ar.customer_payment import (
    customer_payment_service,
    CustomerPaymentInput,
    PaymentAllocationInput,
)
from app.models.ifrs.ar.customer_payment import PaymentMethod, PaymentStatus
from app.services.ifrs.ar.invoice import ARInvoiceInput, ARInvoiceLineInput, ar_invoice_service
from app.services.ifrs.common.attachment import attachment_service
from app.services.ifrs.platform.currency_context import get_currency_context
from app.services.ifrs.tax.tax_master import tax_code_service


def _format_file_size(size: int) -> str:
    """Format file size for display."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


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


def _parse_customer_type(value: Optional[str]) -> CustomerType:
    if not value:
        return CustomerType.COMPANY
    try:
        return CustomerType(value)
    except ValueError:
        try:
            return CustomerType(value.upper())
        except ValueError:
            return CustomerType.COMPANY


def _customer_display_name(customer: Customer) -> str:
    return customer.trading_name or customer.legal_name


def _customer_option_view(customer: Customer) -> dict:
    return {
        "customer_id": customer.customer_id,
        "customer_name": _customer_display_name(customer),
        "customer_code": customer.customer_code,
        "currency_code": customer.currency_code,
        "payment_terms_days": customer.credit_terms_days,
    }


def _customer_form_view(customer: Customer) -> dict:
    contact = customer.primary_contact or {}
    return {
        "customer_id": customer.customer_id,
        "customer_code": customer.customer_code,
        "customer_name": _customer_display_name(customer),
        "tax_id": customer.tax_identification_number,
        "currency_code": customer.currency_code,
        "payment_terms_days": customer.credit_terms_days,
        "credit_limit": customer.credit_limit,
        "credit_hold": False,
        "default_revenue_account_id": customer.default_revenue_account_id,
        "default_receivable_account_id": customer.ar_control_account_id,
        "email": contact.get("email"),
        "phone": contact.get("phone"),
        "billing_address": (customer.billing_address or {}).get("address", ""),
        "shipping_address": (customer.shipping_address or {}).get("address", ""),
        "is_active": customer.is_active,
    }


def _customer_list_view(customer: Customer, balance: Decimal) -> dict:
    return {
        "customer_id": customer.customer_id,
        "customer_code": customer.customer_code,
        "customer_name": _customer_display_name(customer),
        "tax_id": customer.tax_identification_number,
        "payment_terms_days": customer.credit_terms_days,
        "credit_limit": _format_currency(
            customer.credit_limit or Decimal("0"),
            customer.currency_code,
        ),
        "balance": _format_currency(balance, customer.currency_code),
        "is_active": customer.is_active,
    }


def _customer_detail_view(customer: Customer, balance: Decimal) -> dict:
    contact = customer.primary_contact or {}
    return {
        "customer_id": customer.customer_id,
        "customer_code": customer.customer_code,
        "customer_name": _customer_display_name(customer),
        "tax_id": customer.tax_identification_number,
        "currency_code": customer.currency_code,
        "payment_terms_days": customer.credit_terms_days,
        "credit_limit": _format_currency(
            customer.credit_limit or Decimal("0"),
            customer.currency_code,
        ),
        "balance": _format_currency(balance, customer.currency_code),
        "default_revenue_account_id": customer.default_revenue_account_id,
        "default_receivable_account_id": customer.ar_control_account_id,
        "email": contact.get("email"),
        "phone": contact.get("phone"),
        "billing_address": (customer.billing_address or {}).get("address", ""),
        "shipping_address": (customer.shipping_address or {}).get("address", ""),
        "is_active": customer.is_active,
    }


def _invoice_line_view(line: InvoiceLine, currency_code: str) -> dict:
    return {
        "line_id": line.line_id,
        "line_number": line.line_number,
        "description": line.description,
        "quantity": line.quantity,
        "unit_price": _format_currency(line.unit_price, currency_code),
        "discount_amount": _format_currency(line.discount_amount, currency_code),
        "tax_amount": _format_currency(line.tax_amount, currency_code),
        "line_amount": _format_currency(line.line_amount, currency_code),
        "revenue_account_id": line.revenue_account_id,
        "item_id": line.item_id,
        "cost_center_id": line.cost_center_id,
        "project_id": line.project_id,
    }


def _invoice_detail_view(invoice: Invoice, customer: Optional[Customer]) -> dict:
    balance = invoice.total_amount - invoice.amount_paid
    today = date.today()
    return {
        "invoice_id": invoice.invoice_id,
        "invoice_number": invoice.invoice_number,
        "invoice_type": invoice.invoice_type.value,
        "customer_id": invoice.customer_id,
        "customer_name": _customer_display_name(customer) if customer else "",
        "invoice_date": _format_date(invoice.invoice_date),
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
            and invoice.status not in {InvoiceStatus.PAID, InvoiceStatus.VOID}
        ),
        "notes": invoice.notes,
        "internal_notes": invoice.internal_notes,
    }


def _receipt_detail_view(payment: CustomerPayment, customer: Optional[Customer]) -> dict:
    return {
        "receipt_id": payment.payment_id,
        "receipt_number": payment.payment_number,
        "customer_id": payment.customer_id,
        "customer_name": _customer_display_name(customer) if customer else "",
        "receipt_date": _format_date(payment.payment_date),
        "payment_method": payment.payment_method.value,
        "reference_number": payment.reference,
        "description": payment.description,
        "amount": _format_currency(payment.amount, payment.currency_code),
        "status": _receipt_status_label(payment.status),
        "currency_code": payment.currency_code,
    }


def _allocation_view(
    allocation: PaymentAllocation,
    invoice: Optional[Invoice],
    currency_code: str,
) -> dict:
    return {
        "allocation_id": allocation.allocation_id,
        "invoice_id": allocation.invoice_id,
        "invoice_number": invoice.invoice_number if invoice else "",
        "allocated_amount": _format_currency(allocation.allocated_amount, currency_code),
        "discount_taken": _format_currency(allocation.discount_taken, currency_code),
        "write_off_amount": _format_currency(allocation.write_off_amount, currency_code),
        "exchange_difference": _format_currency(
            allocation.exchange_difference,
            currency_code,
        ),
        "allocation_date": _format_date(allocation.allocation_date),
    }


def _invoice_status_label(status: InvoiceStatus) -> str:
    if status == InvoiceStatus.PARTIALLY_PAID:
        return "PARTIAL"
    return str(status.value)


def _receipt_status_label(status: PaymentStatus) -> str:
    if status == PaymentStatus.CLEARED:
        return "POSTED"
    if status == PaymentStatus.PENDING:
        return "DRAFT"
    if status in {PaymentStatus.VOID, PaymentStatus.BOUNCED, PaymentStatus.REVERSED}:
        return "VOIDED"
    return str(status.value)


def _parse_invoice_status(value: Optional[str]) -> Optional[InvoiceStatus]:
    if not value:
        return None
    if value == "PARTIAL":
        return InvoiceStatus.PARTIALLY_PAID
    try:
        return InvoiceStatus(value)
    except ValueError:
        return None


def _parse_receipt_status(value: Optional[str]) -> Optional[PaymentStatus]:
    if not value:
        return None
    status_map = {
        "DRAFT": PaymentStatus.PENDING,
        "POSTED": PaymentStatus.CLEARED,
        "VOIDED": PaymentStatus.VOID,
    }
    return status_map.get(value)


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
    this_month: str


class ARWebService:
    """View service for AR web routes."""

    @staticmethod
    def build_customer_input(form_data: dict) -> CustomerInput:
        credit_limit = form_data.get("credit_limit")
        return CustomerInput(
            customer_code=form_data.get("customer_code", ""),
            customer_type=_parse_customer_type(form_data.get("customer_type")),
            legal_name=form_data.get("customer_name", ""),
            trading_name=form_data.get("customer_name"),
            tax_identification_number=form_data.get("tax_id"),
            currency_code=form_data.get(
                "currency_code",
                settings.default_functional_currency_code,
            ),
            credit_terms_days=int(form_data.get("payment_terms_days", 30)),
            credit_limit=Decimal(credit_limit) if credit_limit else None,
            risk_category=RiskCategory.MEDIUM,
            ar_control_account_id=(
                UUID(form_data["default_receivable_account_id"])
                if form_data.get("default_receivable_account_id")
                else UUID("00000000-0000-0000-0000-000000000001")
            ),
            default_revenue_account_id=(
                UUID(form_data["default_revenue_account_id"])
                if form_data.get("default_revenue_account_id")
                else None
            ),
            billing_address={
                "address": form_data.get("billing_address", ""),
            }
            if form_data.get("billing_address")
            else None,
            shipping_address={
                "address": form_data.get("shipping_address", ""),
            }
            if form_data.get("shipping_address")
            else None,
            primary_contact={
                "email": form_data.get("email", ""),
                "phone": form_data.get("phone", ""),
            }
            if form_data.get("email") or form_data.get("phone")
            else None,
        )

    @staticmethod
    def build_invoice_input(data: dict) -> ARInvoiceInput:
        lines_data = data.get("lines", [])
        if isinstance(lines_data, str):
            lines_data = json.loads(lines_data)

        lines = []
        for line in lines_data:
            if line.get("revenue_account_id") and line.get("description"):
                lines.append(
                    ARInvoiceLineInput(
                        description=line.get("description", ""),
                        quantity=Decimal(str(line.get("quantity", 1))),
                        unit_price=Decimal(str(line.get("unit_price", 0))),
                        revenue_account_id=UUID(line["revenue_account_id"])
                        if line.get("revenue_account_id")
                        else None,
                        tax_code_id=UUID(line["tax_code_id"]) if line.get("tax_code_id") else None,
                        tax_amount=Decimal(str(line.get("tax_amount", 0))),
                        cost_center_id=UUID(line["cost_center_id"])
                        if line.get("cost_center_id")
                        else None,
                        project_id=UUID(line["project_id"]) if line.get("project_id") else None,
                    )
                )

        invoice_date = _parse_date(data.get("invoice_date")) or date.today()
        due_date = _parse_date(data.get("due_date")) or invoice_date

        return ARInvoiceInput(
            customer_id=UUID(data["customer_id"]),
            invoice_type=InvoiceType.STANDARD,
            invoice_date=invoice_date,
            due_date=due_date,
            currency_code=data.get(
                "currency_code",
                settings.default_functional_currency_code,
            ),
            notes=data.get("terms"),
            internal_notes=data.get("notes"),
            lines=lines,
        )

    @staticmethod
    def list_customers_context(
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

        query = db.query(Customer).filter(Customer.organization_id == org_id)
        if is_active is not None:
            query = query.filter(Customer.is_active == is_active)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (Customer.customer_code.ilike(search_pattern))
                | (Customer.legal_name.ilike(search_pattern))
                | (Customer.trading_name.ilike(search_pattern))
                | (Customer.tax_identification_number.ilike(search_pattern))
            )

        total_count = query.with_entities(func.count(Customer.customer_id)).scalar() or 0
        customers = (
            query.order_by(Customer.legal_name)
            .limit(limit)
            .offset(offset)
            .all()
        )

        open_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]
        balances = (
            db.query(
                Invoice.customer_id,
                func.coalesce(
                    func.sum(Invoice.total_amount - Invoice.amount_paid), 0
                ).label("balance"),
            )
            .filter(
                Invoice.organization_id == org_id,
                Invoice.status.in_(open_statuses),
            )
            .group_by(Invoice.customer_id)
            .all()
        )
        balance_map = {row.customer_id: row.balance for row in balances}

        customers_view = [
            _customer_list_view(
                customer,
                balance_map.get(customer.customer_id, Decimal("0")),
            )
            for customer in customers
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "customers": customers_view,
            "search": search,
            "status": status,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def customer_form_context(
        db: Session,
        organization_id: str,
        customer_id: Optional[str] = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        customer = None
        if customer_id:
            try:
                customer = customer_service.get(db, org_id, customer_id)
            except Exception:
                customer = None
        customer_view = _customer_form_view(customer) if customer else None

        revenue_accounts = _get_accounts(db, org_id, IFRSCategory.REVENUE)
        receivable_accounts = _get_accounts(db, org_id, IFRSCategory.ASSETS, "AR")

        context = {
            "customer": customer_view,
            "revenue_accounts": revenue_accounts,
            "receivable_accounts": receivable_accounts,
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def customer_detail_context(
        db: Session,
        organization_id: str,
        customer_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        customer = None
        try:
            customer = customer_service.get(db, org_id, customer_id)
        except Exception:
            customer = None

        if not customer or customer.organization_id != org_id:
            return {"customer": None, "open_invoices": []}

        open_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]

        balance = (
            db.query(
                func.coalesce(
                    func.sum(Invoice.total_amount - Invoice.amount_paid),
                    0,
                )
            )
            .filter(
                Invoice.organization_id == org_id,
                Invoice.customer_id == customer.customer_id,
                Invoice.status.in_(open_statuses),
            )
            .scalar()
            or Decimal("0")
        )

        invoices = (
            db.query(Invoice)
            .filter(
                Invoice.organization_id == org_id,
                Invoice.customer_id == customer.customer_id,
                Invoice.status.in_(open_statuses),
            )
            .order_by(Invoice.due_date)
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
                        not in {InvoiceStatus.PAID, InvoiceStatus.VOID}
                    ),
                }
            )

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="CUSTOMER",
            entity_id=customer.customer_id,
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
            "customer": _customer_detail_view(customer, balance),
            "open_invoices": open_invoices,
            "attachments": attachments_view,
        }

    @staticmethod
    def list_invoices_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        customer_id: Optional[str],
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
            db.query(Invoice, Customer)
            .join(Customer, Invoice.customer_id == Customer.customer_id)
            .filter(Invoice.organization_id == org_id)
        )

        if customer_id:
            query = query.filter(Invoice.customer_id == coerce_uuid(customer_id))
        if status_value:
            query = query.filter(Invoice.status == status_value)
        if from_date:
            query = query.filter(Invoice.invoice_date >= from_date)
        if to_date:
            query = query.filter(Invoice.invoice_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    Invoice.invoice_number.ilike(search_pattern),
                    Customer.legal_name.ilike(search_pattern),
                    Customer.trading_name.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(Invoice.invoice_id)).scalar() or 0
        invoices = (
            query.order_by(Invoice.invoice_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        open_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]
        stats_base = query.with_entities(Invoice)
        outstanding_filter = stats_base.filter(Invoice.status.in_(open_statuses))

        total_outstanding = (
            outstanding_filter.with_entities(
                func.coalesce(
                    func.sum(Invoice.total_amount - Invoice.amount_paid), 0
                )
            ).scalar()
            or Decimal("0")
        )

        past_due = (
            outstanding_filter.filter(Invoice.due_date < today)
            .with_entities(
                func.coalesce(
                    func.sum(Invoice.total_amount - Invoice.amount_paid), 0
                )
            )
            .scalar()
            or Decimal("0")
        )

        due_this_week_end = today + timedelta(days=7)
        due_this_week = (
            outstanding_filter.filter(
                Invoice.due_date >= today,
                Invoice.due_date <= due_this_week_end,
            )
            .with_entities(
                func.coalesce(
                    func.sum(Invoice.total_amount - Invoice.amount_paid), 0
                )
            )
            .scalar()
            or Decimal("0")
        )

        month_start = date(today.year, today.month, 1)
        if today.month == 12:
            month_end = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(today.year, today.month + 1, 1) - timedelta(days=1)

        this_month = (
            outstanding_filter.filter(
                Invoice.due_date >= month_start,
                Invoice.due_date <= month_end,
            )
            .with_entities(
                func.coalesce(
                    func.sum(Invoice.total_amount - Invoice.amount_paid), 0
                )
            )
            .scalar()
            or Decimal("0")
        )

        invoices_view = []
        for invoice, customer in invoices:
            balance = invoice.total_amount - invoice.amount_paid
            invoices_view.append(
                {
                    "invoice_id": invoice.invoice_id,
                    "invoice_number": invoice.invoice_number,
                    "customer_name": _customer_display_name(customer),
                    "invoice_date": _format_date(invoice.invoice_date),
                    "due_date": _format_date(invoice.due_date),
                    "total_amount": _format_currency(
                        invoice.total_amount, invoice.currency_code
                    ),
                    "balance": _format_currency(balance, invoice.currency_code),
                    "status": _invoice_status_label(invoice.status),
                    "is_overdue": (
                        invoice.due_date < today
                        and invoice.status not in {InvoiceStatus.PAID, InvoiceStatus.VOID}
                    ),
                }
            )

        customers_list = [
            _customer_option_view(customer)
            for customer in customer_service.list(
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
            this_month=_format_currency(this_month) or "$0.00",
        )

        return {
            "invoices": invoices_view,
            "customers_list": customers_list,
            "stats": stats.__dict__,
            "search": search,
            "customer_id": customer_id,
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
        customers_list = [
            _customer_option_view(customer)
            for customer in customer_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        revenue_accounts = _get_accounts(db, org_id, IFRSCategory.REVENUE)

        tax_codes = [
            {
                "tax_code_id": tax.tax_code_id,
                "tax_code": tax.tax_code,
                "rate": (tax.tax_rate * 100).quantize(Decimal("0.01")),
            }
            for tax in tax_code_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                applies_to_sales=True,
                limit=200,
            )
        ]

        context = {
            "customers_list": customers_list,
            "revenue_accounts": revenue_accounts,
            "tax_codes": tax_codes,
            "cost_centers": _get_cost_centers(db, org_id),
            "projects": _get_projects(db, org_id),
            "organization_id": organization_id,
            "user_id": "00000000-0000-0000-0000-000000000001",
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
            invoice = ar_invoice_service.get(db, org_id, invoice_id)
        except Exception:
            invoice = None

        if not invoice or invoice.organization_id != org_id:
            return {"invoice": None, "customer": None, "lines": []}

        customer = None
        try:
            customer = customer_service.get(db, org_id, str(invoice.customer_id))
        except Exception:
            customer = None

        lines = ar_invoice_service.get_invoice_lines(
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
            entity_type="CUSTOMER_INVOICE",
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
                "download_url": f"/ar/attachments/{att.attachment_id}/download",
            }
            for att in attachments
        ]

        return {
            "invoice": _invoice_detail_view(invoice, customer),
            "customer": _customer_form_view(customer) if customer else None,
            "lines": lines_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def list_receipts_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        customer_id: Optional[str],
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = _parse_receipt_status(status)
        from_date = _parse_date(start_date)
        to_date = _parse_date(end_date)

        query = (
            db.query(CustomerPayment, Customer)
            .join(Customer, CustomerPayment.customer_id == Customer.customer_id)
            .filter(CustomerPayment.organization_id == org_id)
        )

        if customer_id:
            query = query.filter(CustomerPayment.customer_id == coerce_uuid(customer_id))
        if status_value:
            query = query.filter(CustomerPayment.status == status_value)
        if from_date:
            query = query.filter(CustomerPayment.payment_date >= from_date)
        if to_date:
            query = query.filter(CustomerPayment.payment_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    CustomerPayment.payment_number.ilike(search_pattern),
                    CustomerPayment.reference.ilike(search_pattern),
                    CustomerPayment.description.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(CustomerPayment.payment_id)).scalar() or 0
        receipts = (
            query.order_by(CustomerPayment.payment_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        receipts_view = []
        for payment, customer in receipts:
            receipts_view.append(
                {
                    "receipt_id": payment.payment_id,
                    "receipt_number": payment.payment_number,
                    "customer_name": _customer_display_name(customer),
                    "receipt_date": _format_date(payment.payment_date),
                    "payment_method": payment.payment_method.value,
                    "reference_number": payment.reference,
                    "amount": _format_currency(payment.amount, payment.currency_code),
                    "status": _receipt_status_label(payment.status),
                }
            )

        customers_list = [
            _customer_option_view(customer)
            for customer in customer_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "receipts": receipts_view,
            "customers_list": customers_list,
            "search": search,
            "customer_id": customer_id,
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
    def receipt_form_context(
        db: Session,
        organization_id: str,
        invoice_id: Optional[str] = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        customers_list = [
            _customer_option_view(customer)
            for customer in customer_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        open_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]

        query = (
            db.query(Invoice, Customer)
            .join(Customer, Invoice.customer_id == Customer.customer_id)
            .filter(
                Invoice.organization_id == org_id,
                Invoice.status.in_(open_statuses),
            )
        )

        if invoice_id:
            query = query.filter(Invoice.invoice_id == coerce_uuid(invoice_id))

        rows = query.order_by(Invoice.due_date).all()

        open_invoices = []
        selected_invoice = None
        for invoice, customer in rows:
            balance = invoice.total_amount - invoice.amount_paid
            view = {
                "invoice_id": invoice.invoice_id,
                "invoice_number": invoice.invoice_number,
                "customer_id": invoice.customer_id,
                "customer_name": _customer_display_name(customer),
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

        return {
            "customers_list": customers_list,
            "invoice_id": invoice_id,
            "invoice": selected_invoice,
            "open_invoices": open_invoices,
        }

    @staticmethod
    def receipt_detail_context(
        db: Session,
        organization_id: str,
        receipt_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        receipt = None
        try:
            receipt = customer_payment_service.get(db, receipt_id)
        except Exception:
            receipt = None

        if not receipt or receipt.organization_id != org_id:
            return {"receipt": None, "customer": None, "allocations": []}

        customer = None
        try:
            customer = customer_service.get(db, org_id, str(receipt.customer_id))
        except Exception:
            customer = None

        allocations = customer_payment_service.get_payment_allocations(
            db,
            organization_id=org_id,
            payment_id=receipt.payment_id,
        )

        invoice_map: dict[UUID, Invoice] = {}
        if allocations:
            invoice_ids = [allocation.invoice_id for allocation in allocations]
            invoices = (
                db.query(Invoice)
                .filter(Invoice.invoice_id.in_(invoice_ids))
                .all()
            )
            invoice_map = {invoice.invoice_id: invoice for invoice in invoices}

        allocations_view = [
            _allocation_view(
                allocation,
                invoice_map.get(allocation.invoice_id),
                receipt.currency_code,
            )
            for allocation in allocations
        ]

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="CUSTOMER_PAYMENT",
            entity_id=receipt.payment_id,
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
                "download_url": f"/ar/attachments/{att.attachment_id}/download",
            }
            for att in attachments
        ]

        return {
            "receipt": _receipt_detail_view(receipt, customer),
            "customer": _customer_form_view(customer) if customer else None,
            "allocations": allocations_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def aging_context(
        db: Session,
        organization_id: str,
        as_of_date: Optional[str],
        customer_id: Optional[str],
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        ref_date = _parse_date(as_of_date)

        if customer_id:
            summary = ar_aging_service.calculate_customer_aging(
                db, org_id, coerce_uuid(customer_id), ref_date
            )
            aging_data = [summary]
        else:
            aging_data = ar_aging_service.get_aging_by_customer(
                db, org_id, ref_date
            )

        customers_list = [
            _customer_option_view(customer)
            for customer in customer_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        return {
            "aging_data": aging_data,
            "customers_list": customers_list,
            "as_of_date": as_of_date,
            "customer_id": customer_id,
        }


    @staticmethod
    def delete_customer(
        db: Session,
        organization_id: str,
        customer_id: str,
    ) -> Optional[str]:
        """Delete a customer. Returns error message or None on success."""
        org_id = coerce_uuid(organization_id)
        cust_id = coerce_uuid(customer_id)

        customer = db.get(Customer, cust_id)
        if not customer or customer.organization_id != org_id:
            return "Customer not found"

        # Check for existing invoices
        invoice_count = (
            db.query(func.count(Invoice.invoice_id))
            .filter(
                Invoice.organization_id == org_id,
                Invoice.customer_id == cust_id,
            )
            .scalar()
            or 0
        )

        if invoice_count > 0:
            return f"Cannot delete customer with {invoice_count} invoice(s). Deactivate instead."

        # Check for existing payments
        payment_count = (
            db.query(func.count(CustomerPayment.payment_id))
            .filter(
                CustomerPayment.organization_id == org_id,
                CustomerPayment.customer_id == cust_id,
            )
            .scalar()
            or 0
        )

        if payment_count > 0:
            return f"Cannot delete customer with {payment_count} receipt(s). Deactivate instead."

        try:
            db.delete(customer)
            db.commit()
            return None
        except Exception as e:
            db.rollback()
            return f"Failed to delete customer: {str(e)}"

    @staticmethod
    def delete_invoice(
        db: Session,
        organization_id: str,
        invoice_id: str,
    ) -> Optional[str]:
        """Delete an invoice. Returns error message or None on success."""
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            return "Invoice not found"

        # Only DRAFT invoices can be deleted
        if invoice.status != InvoiceStatus.DRAFT:
            return f"Cannot delete invoice with status '{invoice.status.value}'. Only DRAFT invoices can be deleted."

        # Check for existing payment allocations
        allocation_count = (
            db.query(func.count(PaymentAllocation.allocation_id))
            .filter(PaymentAllocation.invoice_id == inv_id)
            .scalar()
            or 0
        )

        if allocation_count > 0:
            return f"Cannot delete invoice with {allocation_count} payment allocation(s)."

        try:
            # Delete invoice lines first
            db.query(InvoiceLine).filter(
                InvoiceLine.invoice_id == inv_id
            ).delete()
            db.delete(invoice)
            db.commit()
            return None
        except Exception as e:
            db.rollback()
            return f"Failed to delete invoice: {str(e)}"

    @staticmethod
    def build_receipt_input(data: dict) -> CustomerPaymentInput:
        """Build CustomerPaymentInput from form data."""
        payment_date = _parse_date(data.get("payment_date")) or date.today()

        # Parse payment method
        method_str = data.get("payment_method", "BANK_TRANSFER")
        try:
            payment_method = PaymentMethod(method_str)
        except ValueError:
            payment_method = PaymentMethod.BANK_TRANSFER

        # Parse allocations if provided
        allocations = []
        allocations_data = data.get("allocations", [])
        if isinstance(allocations_data, str):
            import json
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

        return CustomerPaymentInput(
            customer_id=UUID(data["customer_id"]),
            payment_date=payment_date,
            payment_method=payment_method,
            currency_code=data.get(
                "currency_code",
                settings.default_functional_currency_code,
            ),
            amount=Decimal(str(data.get("amount", 0))),
            bank_account_id=UUID(data["bank_account_id"]) if data.get("bank_account_id") else None,
            reference=data.get("reference"),
            description=data.get("description"),
            allocations=allocations,
        )

    @staticmethod
    def delete_receipt(
        db: Session,
        organization_id: str,
        receipt_id: str,
    ) -> Optional[str]:
        """Delete a receipt. Returns error message or None on success."""
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(receipt_id)

        payment = db.get(CustomerPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            return "Receipt not found"

        # Only PENDING (DRAFT) receipts can be deleted
        if payment.status != PaymentStatus.PENDING:
            return f"Cannot delete receipt with status '{payment.status.value}'. Only draft receipts can be deleted."

        try:
            # Delete allocations first
            db.query(PaymentAllocation).filter(
                PaymentAllocation.payment_id == pay_id
            ).delete()
            db.delete(payment)
            db.commit()
            return None
        except Exception as e:
            db.rollback()
            return f"Failed to delete receipt: {str(e)}"


    # =========================================================================
    # Credit Notes
    # =========================================================================

    @staticmethod
    def list_credit_notes_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        customer_id: Optional[str],
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        """List credit notes (invoices with type CREDIT_NOTE)."""
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = _parse_invoice_status(status)
        from_date = _parse_date(start_date)
        to_date = _parse_date(end_date)

        query = (
            db.query(Invoice, Customer)
            .join(Customer, Invoice.customer_id == Customer.customer_id)
            .filter(
                Invoice.organization_id == org_id,
                Invoice.invoice_type == InvoiceType.CREDIT_NOTE,
            )
        )

        if customer_id:
            query = query.filter(Invoice.customer_id == coerce_uuid(customer_id))
        if status_value:
            query = query.filter(Invoice.status == status_value)
        if from_date:
            query = query.filter(Invoice.invoice_date >= from_date)
        if to_date:
            query = query.filter(Invoice.invoice_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    Invoice.invoice_number.ilike(search_pattern),
                    Customer.legal_name.ilike(search_pattern),
                    Customer.trading_name.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(Invoice.invoice_id)).scalar() or 0
        credit_notes = (
            query.order_by(Invoice.invoice_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        # Calculate stats
        stats_query = (
            db.query(Invoice)
            .filter(
                Invoice.organization_id == org_id,
                Invoice.invoice_type == InvoiceType.CREDIT_NOTE,
            )
        )

        total_credit_notes = (
            stats_query.with_entities(
                func.coalesce(func.sum(Invoice.total_amount), 0)
            ).scalar()
            or Decimal("0")
        )

        draft_total = (
            stats_query.filter(Invoice.status == InvoiceStatus.DRAFT)
            .with_entities(func.coalesce(func.sum(Invoice.total_amount), 0))
            .scalar()
            or Decimal("0")
        )

        posted_total = (
            stats_query.filter(Invoice.status == InvoiceStatus.POSTED)
            .with_entities(func.coalesce(func.sum(Invoice.total_amount), 0))
            .scalar()
            or Decimal("0")
        )

        applied_total = (
            stats_query.filter(Invoice.status == InvoiceStatus.PAID)
            .with_entities(func.coalesce(func.sum(Invoice.total_amount), 0))
            .scalar()
            or Decimal("0")
        )

        credit_notes_view = []
        for credit_note, customer in credit_notes:
            credit_notes_view.append(
                {
                    "credit_note_id": credit_note.invoice_id,
                    "credit_note_number": credit_note.invoice_number,
                    "customer_name": _customer_display_name(customer),
                    "credit_note_date": _format_date(credit_note.invoice_date),
                    "total_amount": _format_currency(
                        credit_note.total_amount, credit_note.currency_code
                    ),
                    "amount_applied": _format_currency(
                        credit_note.amount_paid, credit_note.currency_code
                    ),
                    "balance": _format_currency(
                        credit_note.total_amount - credit_note.amount_paid,
                        credit_note.currency_code,
                    ),
                    "status": _invoice_status_label(credit_note.status),
                }
            )

        customers_list = [
            _customer_option_view(customer)
            for customer in customer_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "credit_notes": credit_notes_view,
            "customers_list": customers_list,
            "stats": {
                "total_credit_notes": _format_currency(total_credit_notes) or "$0.00",
                "draft_total": _format_currency(draft_total) or "$0.00",
                "posted_total": _format_currency(posted_total) or "$0.00",
                "applied_total": _format_currency(applied_total) or "$0.00",
            },
            "search": search,
            "customer_id": customer_id,
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
    def credit_note_form_context(
        db: Session,
        organization_id: str,
        invoice_id: Optional[str] = None,
    ) -> dict:
        """Context for credit note form (optionally linked to an invoice)."""
        org_id = coerce_uuid(organization_id)
        customers_list = [
            _customer_option_view(customer)
            for customer in customer_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        revenue_accounts = _get_accounts(db, org_id, IFRSCategory.REVENUE)

        tax_codes = [
            {
                "tax_code_id": tax.tax_code_id,
                "tax_code": tax.tax_code,
                "rate": (tax.tax_rate * 100).quantize(Decimal("0.01")),
            }
            for tax in tax_code_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                applies_to_sales=True,
                limit=200,
            )
        ]

        # Get open invoices for reference
        open_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]

        open_invoices = []
        selected_invoice = None
        invoices_query = (
            db.query(Invoice, Customer)
            .join(Customer, Invoice.customer_id == Customer.customer_id)
            .filter(
                Invoice.organization_id == org_id,
                Invoice.invoice_type == InvoiceType.STANDARD,
                Invoice.status.in_(open_statuses),
            )
            .order_by(Invoice.due_date)
        )

        for invoice, customer in invoices_query.all():
            balance = invoice.total_amount - invoice.amount_paid
            view = {
                "invoice_id": invoice.invoice_id,
                "invoice_number": invoice.invoice_number,
                "customer_id": invoice.customer_id,
                "customer_name": _customer_display_name(customer),
                "invoice_date": _format_date(invoice.invoice_date),
                "total_amount": _format_currency(invoice.total_amount, invoice.currency_code),
                "balance": _format_currency(balance, invoice.currency_code),
                "currency_code": invoice.currency_code,
            }
            open_invoices.append(view)
            if invoice_id and str(invoice.invoice_id) == invoice_id:
                selected_invoice = view

        context = {
            "customers_list": customers_list,
            "revenue_accounts": revenue_accounts,
            "tax_codes": tax_codes,
            "cost_centers": _get_cost_centers(db, org_id),
            "projects": _get_projects(db, org_id),
            "open_invoices": open_invoices,
            "invoice_id": invoice_id,
            "selected_invoice": selected_invoice,
            "organization_id": organization_id,
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def credit_note_detail_context(
        db: Session,
        organization_id: str,
        credit_note_id: str,
    ) -> dict:
        """Detail context for a credit note."""
        org_id = coerce_uuid(organization_id)
        credit_note = None
        try:
            credit_note = ar_invoice_service.get(db, org_id, credit_note_id)
        except Exception:
            credit_note = None

        if not credit_note or credit_note.organization_id != org_id:
            return {"credit_note": None, "customer": None, "lines": []}

        if credit_note.invoice_type != InvoiceType.CREDIT_NOTE:
            return {"credit_note": None, "customer": None, "lines": []}

        customer = None
        try:
            customer = customer_service.get(db, org_id, str(credit_note.customer_id))
        except Exception:
            customer = None

        lines = ar_invoice_service.get_invoice_lines(
            db,
            organization_id=org_id,
            invoice_id=credit_note.invoice_id,
        )
        lines_view = [
            _invoice_line_view(line, credit_note.currency_code) for line in lines
        ]

        balance = credit_note.total_amount - credit_note.amount_paid
        credit_note_view = {
            "credit_note_id": credit_note.invoice_id,
            "credit_note_number": credit_note.invoice_number,
            "customer_id": credit_note.customer_id,
            "customer_name": _customer_display_name(customer) if customer else "",
            "credit_note_date": _format_date(credit_note.invoice_date),
            "currency_code": credit_note.currency_code,
            "subtotal": _format_currency(credit_note.subtotal, credit_note.currency_code),
            "tax_amount": _format_currency(credit_note.tax_amount, credit_note.currency_code),
            "total_amount": _format_currency(credit_note.total_amount, credit_note.currency_code),
            "amount_applied": _format_currency(credit_note.amount_paid, credit_note.currency_code),
            "balance": _format_currency(balance, credit_note.currency_code),
            "status": _invoice_status_label(credit_note.status),
            "notes": credit_note.notes,
            "internal_notes": credit_note.internal_notes,
        }

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="CREDIT_NOTE",
            entity_id=credit_note.invoice_id,
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
                "download_url": f"/ar/attachments/{att.attachment_id}/download",
            }
            for att in attachments
        ]

        return {
            "credit_note": credit_note_view,
            "customer": _customer_form_view(customer) if customer else None,
            "lines": lines_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def build_credit_note_input(data: dict) -> ARInvoiceInput:
        """Build ARInvoiceInput from form data for credit note."""
        lines_data = data.get("lines", [])
        if isinstance(lines_data, str):
            lines_data = json.loads(lines_data)

        lines = []
        for line in lines_data:
            if line.get("revenue_account_id") and line.get("description"):
                lines.append(
                    ARInvoiceLineInput(
                        description=line.get("description", ""),
                        quantity=Decimal(str(line.get("quantity", 1))),
                        unit_price=Decimal(str(line.get("unit_price", 0))),
                        revenue_account_id=UUID(line["revenue_account_id"])
                        if line.get("revenue_account_id")
                        else None,
                        tax_code_id=UUID(line["tax_code_id"]) if line.get("tax_code_id") else None,
                        tax_amount=Decimal(str(line.get("tax_amount", 0))),
                        cost_center_id=UUID(line["cost_center_id"])
                        if line.get("cost_center_id")
                        else None,
                        project_id=UUID(line["project_id"]) if line.get("project_id") else None,
                    )
                )

        credit_note_date = _parse_date(data.get("credit_note_date")) or date.today()

        return ARInvoiceInput(
            customer_id=UUID(data["customer_id"]),
            invoice_type=InvoiceType.CREDIT_NOTE,
            invoice_date=credit_note_date,
            due_date=credit_note_date,  # Credit notes don't have due dates
            currency_code=data.get(
                "currency_code",
                settings.default_functional_currency_code,
            ),
            notes=data.get("reason"),
            internal_notes=data.get("notes"),
            lines=lines,
        )

    @staticmethod
    def delete_credit_note(
        db: Session,
        organization_id: str,
        credit_note_id: str,
    ) -> Optional[str]:
        """Delete a credit note. Returns error message or None on success."""
        org_id = coerce_uuid(organization_id)
        cn_id = coerce_uuid(credit_note_id)

        credit_note = db.get(Invoice, cn_id)
        if not credit_note or credit_note.organization_id != org_id:
            return "Credit note not found"

        if credit_note.invoice_type != InvoiceType.CREDIT_NOTE:
            return "Document is not a credit note"

        # Only DRAFT credit notes can be deleted
        if credit_note.status != InvoiceStatus.DRAFT:
            return f"Cannot delete credit note with status '{credit_note.status.value}'. Only DRAFT credit notes can be deleted."

        # Check for payment allocations
        allocation_count = (
            db.query(func.count(PaymentAllocation.allocation_id))
            .filter(PaymentAllocation.invoice_id == cn_id)
            .scalar()
            or 0
        )

        if allocation_count > 0:
            return f"Cannot delete credit note with {allocation_count} allocation(s)."

        try:
            # Delete lines first
            db.query(InvoiceLine).filter(
                InvoiceLine.invoice_id == cn_id
            ).delete()
            db.delete(credit_note)
            db.commit()
            return None
        except Exception as e:
            db.rollback()
            return f"Failed to delete credit note: {str(e)}"


ar_web_service = ARWebService()
