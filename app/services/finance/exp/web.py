"""
Expense Web View Service.

Provides view-focused data for expense web routes.
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.finance.core_org.business_unit import BusinessUnit
from app.models.finance.core_org.cost_center import CostCenter
from app.models.finance.core_org.project import Project, ProjectStatus
from app.models.finance.exp.expense_entry import (
    ExpenseEntry,
    ExpenseStatus,
    PaymentMethod,
)
from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.tax.tax_code import TaxCode
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.finance.exp.expense import expense_service
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.platform.org_context import org_context_service
from app.services.formatters import format_currency as _format_currency
from app.services.formatters import format_date as _format_date
from app.services.recent_activity import get_recent_activity_for_record

logger = logging.getLogger(__name__)


class ExpenseWebService:
    """View service for expense web routes."""

    @staticmethod
    def list_context(
        db: Session,
        organization_id: str,
        status: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 25,
        page: int = 1,
    ) -> dict:
        """Get context for expense list page."""
        from sqlalchemy import or_

        org_id = coerce_uuid(organization_id)

        query = (
            select(ExpenseEntry)
            .options(joinedload(ExpenseEntry.expense_account))
            .where(ExpenseEntry.organization_id == org_id)
        )

        if status:
            query = query.where(ExpenseEntry.status == ExpenseStatus(status))

        if start_date:
            query = query.where(ExpenseEntry.expense_date >= start_date)

        if end_date:
            query = query.where(ExpenseEntry.expense_date <= end_date)

        if search:
            term = f"%{search}%"
            query = query.where(
                or_(
                    ExpenseEntry.expense_number.ilike(term),
                    ExpenseEntry.description.ilike(term),
                    ExpenseEntry.payee.ilike(term),
                )
            )

        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        expenses = (
            db.scalars(
                query.order_by(ExpenseEntry.expense_date.desc())
                .offset(offset)
                .limit(limit)
            )
            .unique()
            .all()
        )

        items = []
        for exp in expenses:
            items.append(
                {
                    "expense_id": str(exp.expense_id),
                    "expense_number": exp.expense_number,
                    "expense_date": _format_date(exp.expense_date),
                    "description": exp.description,
                    "payee": exp.payee or "-",
                    "amount": _format_currency(exp.amount, exp.currency_code),
                    "tax_amount": _format_currency(exp.tax_amount, exp.currency_code),
                    "total_amount": _format_currency(
                        exp.total_amount, exp.currency_code
                    ),
                    "payment_method": exp.payment_method.value,
                    "status": exp.status.value,
                    "expense_account": exp.expense_account.account_name
                    if exp.expense_account
                    else "-",
                }
            )

        # Status counts
        status_counts = db.execute(
            select(ExpenseEntry.status, func.count())
            .where(ExpenseEntry.organization_id == org_id)
            .group_by(ExpenseEntry.status)
        ).all()
        counts = {s.value: c for s, c in status_counts}

        active_filters = build_active_filters(
            params={
                "status": status,
                "start_date": start_date,
                "end_date": end_date,
                "search": search,
            },
            labels={"start_date": "From", "end_date": "To", "search": "Search"},
        )
        return {
            "expenses": items,
            "search": search or "",
            "filter_status": status,
            "filter_start_date": start_date,
            "filter_end_date": end_date,
            "status_counts": counts,
            "statuses": [s.value for s in ExpenseStatus],
            "total": total,
            "offset": offset,
            "limit": limit,
            "page": page,
            "total_count": total,
            "total_pages": max(1, (total + limit - 1) // limit),
            "active_filters": active_filters,
        }

    @staticmethod
    def form_context(
        db: Session,
        organization_id: str,
        expense_id: str | None = None,
    ) -> dict:
        """Get context for expense form (new/edit)."""
        org_id = coerce_uuid(organization_id)

        # Get expense accounts (EXPENSES category)
        expense_accounts = db.scalars(
            select(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .where(
                Account.organization_id == org_id,
                Account.is_active.is_(True),
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
            )
            .order_by(Account.account_code)
        ).all()

        expense_account_options = [
            {
                "account_id": str(a.account_id),
                "account_code": a.account_code,
                "account_name": a.account_name,
            }
            for a in expense_accounts
        ]

        # Get payment accounts (cash, bank - typically ASSETS)
        payment_accounts = db.scalars(
            select(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .where(
                Account.organization_id == org_id,
                Account.is_active.is_(True),
                AccountCategory.ifrs_category == IFRSCategory.ASSETS,
            )
            .order_by(Account.account_code)
        ).all()

        payment_account_options = [
            {
                "account_id": str(a.account_id),
                "account_code": a.account_code,
                "account_name": a.account_name,
            }
            for a in payment_accounts
        ]

        # Get tax codes
        tax_codes = db.scalars(
            select(TaxCode)
            .where(
                TaxCode.organization_id == org_id,
                TaxCode.is_active.is_(True),
            )
            .order_by(TaxCode.tax_code)
        ).all()

        tax_code_options = [
            {
                "tax_code_id": str(t.tax_code_id),
                "tax_code": t.tax_code,
                "description": t.description or "",
                "rate": float(t.tax_rate),
            }
            for t in tax_codes
        ]

        # Get open fiscal periods
        periods = db.scalars(
            select(FiscalPeriod)
            .where(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.status.in_(PeriodStatus.accepts_postings()),
            )
            .order_by(FiscalPeriod.start_date.desc())
        ).all()

        period_options = [
            {
                "period_id": str(p.fiscal_period_id),
                "period_name": p.period_name,
            }
            for p in periods
        ]

        # Get active projects
        projects = db.scalars(
            select(Project)
            .where(
                Project.organization_id == org_id,
                Project.status == ProjectStatus.ACTIVE,
            )
            .order_by(Project.project_code)
        ).all()

        project_options = [
            {
                "project_id": str(p.project_id),
                "project_code": p.project_code,
                "project_name": p.project_name,
            }
            for p in projects
        ]

        # Get cost centers
        cost_centers = db.scalars(
            select(CostCenter)
            .where(
                CostCenter.organization_id == org_id,
                CostCenter.is_active.is_(True),
            )
            .order_by(CostCenter.cost_center_code)
        ).all()

        cost_center_options = [
            {
                "cost_center_id": str(c.cost_center_id),
                "cost_center_code": c.cost_center_code,
                "cost_center_name": c.cost_center_name,
            }
            for c in cost_centers
        ]

        # Get business units
        business_units = db.scalars(
            select(BusinessUnit)
            .where(BusinessUnit.organization_id == org_id)
            .order_by(BusinessUnit.unit_code)
        ).all()

        business_unit_options = [
            {
                "business_unit_id": str(b.business_unit_id),
                "business_unit_code": b.unit_code,
                "business_unit_name": b.unit_name,
            }
            for b in business_units
        ]

        payment_methods = [m.value for m in PaymentMethod]

        context: dict[str, Any] = {
            "expense_accounts": expense_account_options,
            "payment_accounts": payment_account_options,
            "tax_codes": tax_code_options,
            "fiscal_periods": period_options,
            "projects": project_options,
            "cost_centers": cost_center_options,
            "business_units": business_unit_options,
            "payment_methods": payment_methods,
            "today": _format_date(date.today()),
            "expense": None,
        }
        context.update(get_currency_context(db, organization_id))

        # If editing, load expense data
        if expense_id:
            expense = db.get(ExpenseEntry, coerce_uuid(expense_id))
            if expense and expense.organization_id == org_id:
                context["expense"] = {
                    "expense_id": str(expense.expense_id),
                    "expense_number": expense.expense_number,
                    "expense_date": _format_date(expense.expense_date),
                    "description": expense.description,
                    "notes": expense.notes or "",
                    "expense_account_id": str(expense.expense_account_id),
                    "payment_account_id": str(expense.payment_account_id)
                    if expense.payment_account_id
                    else "",
                    "amount": str(expense.amount),
                    "tax_code_id": str(expense.tax_code_id)
                    if expense.tax_code_id
                    else "",
                    "tax_amount": str(expense.tax_amount),
                    "currency_code": expense.currency_code,
                    "payment_method": expense.payment_method.value,
                    "payee": expense.payee or "",
                    "receipt_reference": expense.receipt_reference or "",
                    "project_id": str(expense.project_id) if expense.project_id else "",
                    "cost_center_id": str(expense.cost_center_id)
                    if expense.cost_center_id
                    else "",
                    "business_unit_id": str(expense.business_unit_id)
                    if expense.business_unit_id
                    else "",
                    "status": expense.status.value,
                    "can_edit": expense.status == ExpenseStatus.DRAFT,
                    "can_submit": expense.status == ExpenseStatus.DRAFT,
                    "can_approve": expense.status == ExpenseStatus.SUBMITTED,
                    "can_post": expense.status == ExpenseStatus.APPROVED,
                }

        return context

    @staticmethod
    def detail_context(
        db: Session,
        organization_id: str,
        expense_id: str,
    ) -> dict:
        """Get context for expense detail page."""
        org_id = coerce_uuid(organization_id)
        exp_uuid = coerce_uuid(expense_id)
        expense = (
            db.scalars(
                select(ExpenseEntry)
                .options(
                    joinedload(ExpenseEntry.expense_account),
                    joinedload(ExpenseEntry.payment_account),
                    joinedload(ExpenseEntry.project),
                    joinedload(ExpenseEntry.cost_center),
                    joinedload(ExpenseEntry.business_unit),
                    joinedload(ExpenseEntry.journal_entry),
                )
                .where(
                    ExpenseEntry.expense_id == exp_uuid,
                    ExpenseEntry.organization_id == org_id,
                )
            )
            .unique()
            .first()
        )

        if not expense:
            return {"expense": None}

        return {
            "expense": {
                "expense_id": str(expense.expense_id),
                "expense_number": expense.expense_number,
                "expense_date": _format_date(expense.expense_date),
                "description": expense.description,
                "notes": expense.notes or "",
                "expense_account": expense.expense_account.account_name
                if expense.expense_account
                else "-",
                "expense_account_code": expense.expense_account.account_code
                if expense.expense_account
                else "",
                "payment_account": expense.payment_account.account_name
                if expense.payment_account
                else "-",
                "payment_account_code": expense.payment_account.account_code
                if expense.payment_account
                else "",
                "amount": _format_currency(expense.amount, expense.currency_code),
                "tax_amount": _format_currency(
                    expense.tax_amount, expense.currency_code
                ),
                "total_amount": _format_currency(
                    expense.total_amount, expense.currency_code
                ),
                "currency_code": expense.currency_code,
                "payment_method": expense.payment_method.value,
                "payee": expense.payee or "-",
                "receipt_reference": expense.receipt_reference or "-",
                "project": expense.project.project_name if expense.project else None,
                "project_code": expense.project.project_code
                if expense.project
                else None,
                "cost_center": expense.cost_center.cost_center_name
                if expense.cost_center
                else None,
                "cost_center_code": expense.cost_center.cost_center_code
                if expense.cost_center
                else None,
                "business_unit": expense.business_unit.unit_name
                if expense.business_unit
                else None,
                "business_unit_code": expense.business_unit.unit_code
                if expense.business_unit
                else None,
                "status": expense.status.value,
                "journal_number": expense.journal_entry.journal_number
                if expense.journal_entry
                else None,
                "journal_entry_id": str(expense.journal_entry_id)
                if expense.journal_entry_id
                else None,
                "submitted_at": expense.submitted_at.strftime("%Y-%m-%d %H:%M")
                if expense.submitted_at
                else None,
                "approved_at": expense.approved_at.strftime("%Y-%m-%d %H:%M")
                if expense.approved_at
                else None,
                "posted_at": expense.posted_at.strftime("%Y-%m-%d %H:%M")
                if expense.posted_at
                else None,
                "created_at": expense.created_at.strftime("%Y-%m-%d %H:%M")
                if expense.created_at
                else "",
                "can_edit": expense.status == ExpenseStatus.DRAFT,
                "can_submit": expense.status == ExpenseStatus.DRAFT,
                "can_approve": expense.status == ExpenseStatus.SUBMITTED,
                "can_reject": expense.status
                in [ExpenseStatus.SUBMITTED, ExpenseStatus.APPROVED],
                "can_post": expense.status == ExpenseStatus.APPROVED,
                "can_void": expense.status
                not in [ExpenseStatus.POSTED, ExpenseStatus.VOID],
            },
            "recent_activity": get_recent_activity_for_record(
                db,
                org_id,
                record=expense,
                limit=10,
            ),
        }

    @staticmethod
    def create_expense_from_form(
        db: Session,
        organization_id: UUID,
        user_id: UUID,
        expense_date: str,
        expense_account_id: str,
        amount: str,
        description: str,
        payment_method: str,
        payment_account_id: str | None = None,
        tax_code_id: str | None = None,
        tax_amount: str | None = None,
        currency_code: str | None = None,
        payee: str | None = None,
        receipt_reference: str | None = None,
        notes: str | None = None,
        project_id: str | None = None,
        cost_center_id: str | None = None,
        business_unit_id: str | None = None,
    ) -> ExpenseEntry:
        """Create an expense from form data.

        Handles all type conversions and defaults in the service layer.

        Args:
            db: Database session
            organization_id: Organization UUID
            user_id: User creating the expense
            expense_date: Date string in YYYY-MM-DD format
            expense_account_id: Expense account UUID string
            amount: Amount string
            description: Expense description
            payment_method: PaymentMethod enum value string
            ... other optional parameters

        Returns:
            Created ExpenseEntry

        Raises:
            ValueError: If required fields are invalid
        """
        # Default currency to functional currency if not provided
        if not currency_code:
            currency_code = org_context_service.get_functional_currency(
                db, organization_id
            )

        # Parse date
        parsed_date = datetime.strptime(expense_date, "%Y-%m-%d").date()

        # Parse amount
        parsed_amount = Decimal(amount)
        parsed_tax_amount = Decimal(tax_amount) if tax_amount else Decimal("0")

        # Create expense via the expense service
        expense = expense_service.create(
            db,
            organization_id=str(organization_id),
            expense_date=parsed_date,
            expense_account_id=expense_account_id,
            amount=parsed_amount,
            description=description,
            payment_method=PaymentMethod(payment_method),
            created_by=str(user_id),
            payment_account_id=payment_account_id if payment_account_id else None,
            tax_code_id=tax_code_id if tax_code_id else None,
            tax_amount=parsed_tax_amount,
            currency_code=currency_code,
            payee=payee,
            receipt_reference=receipt_reference,
            notes=notes,
            project_id=project_id if project_id else None,
            cost_center_id=cost_center_id if cost_center_id else None,
            business_unit_id=business_unit_id if business_unit_id else None,
        )

        return expense


expense_web_service = ExpenseWebService()
