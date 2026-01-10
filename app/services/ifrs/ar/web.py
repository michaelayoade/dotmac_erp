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
from app.models.ifrs.core_org.cost_center import CostCenter
from app.models.ifrs.core_org.project import Project
from app.models.ifrs.gl.account import Account
from app.models.ifrs.gl.account_category import AccountCategory, IFRSCategory
from app.services.common import coerce_uuid
from app.services.ifrs.ar.ar_aging import ar_aging_service
from app.services.ifrs.ar.customer import CustomerInput, customer_service
from app.services.ifrs.ar.invoice import ARInvoiceInput, ARInvoiceLineInput
from app.services.ifrs.tax.tax_master import tax_code_service


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


def _invoice_status_label(status: InvoiceStatus) -> str:
    if status == InvoiceStatus.PARTIALLY_PAID:
        return "PARTIAL"
    return status.value


def _receipt_status_label(status: PaymentStatus) -> str:
    if status == PaymentStatus.CLEARED:
        return "POSTED"
    if status == PaymentStatus.PENDING:
        return "DRAFT"
    if status in {PaymentStatus.VOID, PaymentStatus.BOUNCED, PaymentStatus.REVERSED}:
        return "VOIDED"
    return status.value


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
            currency_code=form_data.get("currency_code", "USD"),
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
            currency_code=data.get("currency_code", "USD"),
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
                customer = customer_service.get(db, customer_id)
            except Exception:
                customer = None
        customer_view = _customer_form_view(customer) if customer else None

        revenue_accounts = _get_accounts(db, org_id, IFRSCategory.REVENUE)
        receivable_accounts = _get_accounts(db, org_id, IFRSCategory.ASSETS, "AR")

        return {
            "customer": customer_view,
            "revenue_accounts": revenue_accounts,
            "receivable_accounts": receivable_accounts,
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

        return {
            "customers_list": customers_list,
            "revenue_accounts": revenue_accounts,
            "tax_codes": tax_codes,
            "cost_centers": _get_cost_centers(db, org_id),
            "projects": _get_projects(db, org_id),
            "organization_id": organization_id,
            "user_id": "00000000-0000-0000-0000-000000000001",
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


ar_web_service = ARWebService()
