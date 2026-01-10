"""
GL web view service.

Provides view-focused data for GL web routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ifrs.gl.account import Account
from app.models.ifrs.gl.account_balance import AccountBalance, BalanceType
from app.models.ifrs.gl.account_category import AccountCategory, IFRSCategory
from app.models.ifrs.gl.fiscal_period import FiscalPeriod
from app.models.ifrs.gl.fiscal_year import FiscalYear
from app.models.ifrs.gl.journal_entry import JournalEntry, JournalStatus
from app.services.common import coerce_uuid


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


def _ifrs_label(category: IFRSCategory) -> str:
    label_map = {
        IFRSCategory.ASSETS: "ASSET",
        IFRSCategory.LIABILITIES: "LIABILITY",
        IFRSCategory.EQUITY: "EQUITY",
        IFRSCategory.REVENUE: "REVENUE",
        IFRSCategory.EXPENSES: "EXPENSE",
        IFRSCategory.OTHER_COMPREHENSIVE_INCOME: "OCI",
    }
    return label_map.get(category, category.value)


def _parse_category(value: Optional[str]) -> Optional[IFRSCategory]:
    if not value:
        return None
    mapping = {
        "ASSET": IFRSCategory.ASSETS,
        "LIABILITY": IFRSCategory.LIABILITIES,
        "EQUITY": IFRSCategory.EQUITY,
        "REVENUE": IFRSCategory.REVENUE,
        "EXPENSE": IFRSCategory.EXPENSES,
    }
    return mapping.get(value)


def _parse_status(value: Optional[str]) -> Optional[JournalStatus]:
    if not value:
        return None
    try:
        return JournalStatus(value)
    except ValueError:
        return None


@dataclass
class TrialBalanceTotals:
    total_debit: str
    total_credit: str


class GLWebService:
    """View service for GL web routes."""

    @staticmethod
    def list_accounts_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        category: Optional[str],
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

        category_value = _parse_category(category)

        query = (
            db.query(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .filter(Account.organization_id == org_id)
        )

        if is_active is not None:
            query = query.filter(Account.is_active == is_active)
        if category_value:
            query = query.filter(AccountCategory.ifrs_category == category_value)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (Account.account_code.ilike(search_pattern))
                | (Account.account_name.ilike(search_pattern))
                | (Account.search_terms.ilike(search_pattern))
            )

        total_count = query.with_entities(func.count(Account.account_id)).scalar() or 0
        accounts = (
            query.order_by(Account.account_code)
            .limit(limit)
            .offset(offset)
            .all()
        )

        accounts_view = []
        for account in accounts:
            category_label = _ifrs_label(account.category.ifrs_category)
            accounts_view.append(
                {
                    "account_id": account.account_id,
                    "account_code": account.account_code,
                    "account_name": account.account_name,
                    "description": account.description,
                    "category": category_label,
                    "normal_balance": account.normal_balance.value,
                    "balance": "$0.00",
                    "is_active": account.is_active,
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "accounts": accounts_view,
            "search": search,
            "category": category,
            "status": status,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def list_journals_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = _parse_status(status)
        from_date = _parse_date(start_date)
        to_date = _parse_date(end_date)

        query = db.query(JournalEntry).filter(JournalEntry.organization_id == org_id)

        if status_value:
            query = query.filter(JournalEntry.status == status_value)
        if from_date:
            query = query.filter(JournalEntry.posting_date >= from_date)
        if to_date:
            query = query.filter(JournalEntry.posting_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (JournalEntry.journal_number.ilike(search_pattern))
                | (JournalEntry.description.ilike(search_pattern))
                | (JournalEntry.reference.ilike(search_pattern))
            )

        total_count = query.with_entities(func.count(JournalEntry.journal_entry_id)).scalar() or 0
        entries = (
            query.order_by(JournalEntry.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        entries_view = []
        for entry in entries:
            entries_view.append(
                {
                    "journal_entry_id": entry.journal_entry_id,
                    "entry_number": entry.journal_number,
                    "entry_date": _format_date(entry.entry_date),
                    "description": entry.description,
                    "source_module": entry.source_module or "MANUAL",
                    "total_debit": _format_currency(entry.total_debit, entry.currency_code),
                    "total_credit": _format_currency(entry.total_credit, entry.currency_code),
                    "status": entry.status.value,
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "entries": entries_view,
            "search": search,
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
    def periods_context(
        db: Session,
        organization_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        today = date.today()

        years = (
            db.query(FiscalYear)
            .filter(FiscalYear.organization_id == org_id)
            .order_by(FiscalYear.start_date.desc())
            .all()
        )

        periods = (
            db.query(FiscalPeriod)
            .filter(FiscalPeriod.organization_id == org_id)
            .order_by(FiscalPeriod.period_number)
            .all()
        )
        periods_by_year: dict[UUID, list[FiscalPeriod]] = {}
        for period in periods:
            periods_by_year.setdefault(period.fiscal_year_id, []).append(period)

        entry_counts = dict(
            db.query(
                JournalEntry.fiscal_period_id,
                func.count(JournalEntry.journal_entry_id),
            )
            .filter(JournalEntry.organization_id == org_id)
            .group_by(JournalEntry.fiscal_period_id)
            .all()
        )

        years_view = []
        for year in years:
            year_periods = []
            for period in periods_by_year.get(year.fiscal_year_id, []):
                year_periods.append(
                    {
                        "period_id": period.fiscal_period_id,
                        "period_name": period.period_name,
                        "start_date": _format_date(period.start_date),
                        "end_date": _format_date(period.end_date),
                        "status": period.status.value,
                        "is_current": period.start_date <= today <= period.end_date,
                        "entry_count": entry_counts.get(period.fiscal_period_id, 0),
                    }
                )

            years_view.append(
                {
                    "year_id": year.fiscal_year_id,
                    "year_name": year.year_name,
                    "start_date": _format_date(year.start_date),
                    "end_date": _format_date(year.end_date),
                    "status": "OPEN" if not year.is_closed else "CLOSED",
                    "periods": year_periods,
                }
            )

        return {"fiscal_years": years_view}

    @staticmethod
    def trial_balance_context(
        db: Session,
        organization_id: str,
        as_of_date: Optional[str],
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        ref_date = _parse_date(as_of_date) or date.today()

        period = (
            db.query(FiscalPeriod)
            .filter(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= ref_date,
                FiscalPeriod.end_date >= ref_date,
            )
            .order_by(FiscalPeriod.start_date.desc())
            .first()
        )

        if not period:
            period = (
                db.query(FiscalPeriod)
                .filter(FiscalPeriod.organization_id == org_id)
                .order_by(FiscalPeriod.end_date.desc())
                .first()
            )

        balances = []
        total_debit = Decimal("0")
        total_credit = Decimal("0")

        if period:
            rows = (
                db.query(AccountBalance, Account, AccountCategory)
                .join(Account, AccountBalance.account_id == Account.account_id)
                .join(AccountCategory, Account.category_id == AccountCategory.category_id)
                .filter(
                    AccountBalance.organization_id == org_id,
                    AccountBalance.fiscal_period_id == period.fiscal_period_id,
                    AccountBalance.balance_type == BalanceType.ACTUAL,
                )
                .order_by(Account.account_code)
                .all()
            )

            for balance, account, category in rows:
                debit = balance.closing_debit or Decimal("0")
                credit = balance.closing_credit or Decimal("0")
                total_debit += debit
                total_credit += credit
                balances.append(
                    {
                        "account_code": account.account_code,
                        "account_name": account.account_name,
                        "category": _ifrs_label(category.ifrs_category),
                        "debit": _format_currency(debit, balance.currency_code),
                        "credit": _format_currency(credit, balance.currency_code),
                    }
                )

        totals = TrialBalanceTotals(
            total_debit=_format_currency(total_debit) or "$0.00",
            total_credit=_format_currency(total_credit) or "$0.00",
        )

        return {
            "balances": balances,
            "as_of_date": as_of_date or _format_date(ref_date),
            "total_debit": totals.total_debit,
            "total_credit": totals.total_credit,
        }


gl_web_service = GLWebService()
