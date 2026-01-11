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

from app.models.ifrs.gl.account import Account, AccountType, NormalBalance
from app.models.ifrs.gl.account_balance import AccountBalance, BalanceType
from app.models.ifrs.gl.account_category import AccountCategory, IFRSCategory
from app.models.ifrs.gl.fiscal_period import FiscalPeriod
from app.models.ifrs.gl.fiscal_year import FiscalYear
from app.models.ifrs.gl.journal_entry import JournalEntry, JournalStatus, JournalType
from app.models.ifrs.gl.journal_entry_line import JournalEntryLine
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


def _category_option_view(category: AccountCategory) -> dict:
    return {
        "category_id": category.category_id,
        "category_code": category.category_code,
        "category_name": category.category_name,
        "ifrs_category": category.ifrs_category.value,
        "ifrs_label": _ifrs_label(category.ifrs_category),
    }


def _account_form_view(account: Account) -> dict:
    return {
        "account_id": account.account_id,
        "account_code": account.account_code,
        "account_name": account.account_name,
        "description": account.description,
        "search_terms": account.search_terms,
        "category_id": account.category_id,
        "account_type": account.account_type.value,
        "normal_balance": account.normal_balance.value,
        "is_multi_currency": account.is_multi_currency,
        "default_currency_code": account.default_currency_code,
        "is_active": account.is_active,
        "is_posting_allowed": account.is_posting_allowed,
        "is_budgetable": account.is_budgetable,
        "is_reconciliation_required": account.is_reconciliation_required,
        "subledger_type": account.subledger_type,
        "is_cash_equivalent": account.is_cash_equivalent,
        "is_financial_instrument": account.is_financial_instrument,
    }


def _account_detail_view(account: Account) -> dict:
    category = account.category
    return {
        "account_id": account.account_id,
        "account_code": account.account_code,
        "account_name": account.account_name,
        "description": account.description,
        "category_id": account.category_id,
        "category_name": category.category_name if category else "",
        "category_code": category.category_code if category else "",
        "ifrs_category": _ifrs_label(category.ifrs_category) if category else "",
        "account_type": account.account_type.value,
        "normal_balance": account.normal_balance.value,
        "is_active": account.is_active,
        "is_posting_allowed": account.is_posting_allowed,
        "is_budgetable": account.is_budgetable,
        "is_reconciliation_required": account.is_reconciliation_required,
        "subledger_type": account.subledger_type,
        "is_cash_equivalent": account.is_cash_equivalent,
        "is_financial_instrument": account.is_financial_instrument,
    }


def _journal_entry_view(entry: JournalEntry) -> dict:
    return {
        "journal_entry_id": entry.journal_entry_id,
        "journal_number": entry.journal_number,
        "journal_type": entry.journal_type.value,
        "entry_date": _format_date(entry.entry_date),
        "posting_date": _format_date(entry.posting_date),
        "description": entry.description,
        "reference": entry.reference,
        "currency_code": entry.currency_code,
        "exchange_rate": entry.exchange_rate,
        "total_debit": _format_currency(entry.total_debit, entry.currency_code),
        "total_credit": _format_currency(entry.total_credit, entry.currency_code),
        "status": entry.status.value,
        "source_module": entry.source_module or "MANUAL",
    }


def _journal_line_view(
    line: JournalEntryLine,
    account: Optional[Account],
    currency_code: str,
) -> dict:
    line_currency = line.currency_code or currency_code
    return {
        "line_id": line.line_id,
        "line_number": line.line_number,
        "account_id": line.account_id,
        "account_code": account.account_code if account else "",
        "account_name": account.account_name if account else "",
        "description": line.description,
        "debit_amount": _format_currency(line.debit_amount, line_currency),
        "credit_amount": _format_currency(line.credit_amount, line_currency),
        "currency_code": line_currency,
    }


def _period_option_view(period: FiscalPeriod) -> dict:
    return {
        "period_id": period.fiscal_period_id,
        "period_name": period.period_name,
        "start_date": _format_date(period.start_date),
        "end_date": _format_date(period.end_date),
        "status": period.status.value,
    }


def _fiscal_year_option_view(year: FiscalYear) -> dict:
    return {
        "fiscal_year_id": year.fiscal_year_id,
        "year_code": year.year_code,
        "year_name": year.year_name,
        "start_date": _format_date(year.start_date),
        "end_date": _format_date(year.end_date),
        "is_closed": year.is_closed,
        "is_adjustment_year": year.is_adjustment_year,
    }


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
    def account_form_context(
        db: Session,
        organization_id: str,
        account_id: Optional[str] = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        account = None
        if account_id:
            account = db.get(Account, coerce_uuid(account_id))
            if not account or account.organization_id != org_id:
                account = None

        categories = (
            db.query(AccountCategory)
            .filter(
                AccountCategory.organization_id == org_id,
                AccountCategory.is_active.is_(True),
            )
            .order_by(AccountCategory.category_code)
            .all()
        )
        if not categories:
            defaults = [
                ("AST", "Assets", IFRSCategory.ASSETS),
                ("LIA", "Liabilities", IFRSCategory.LIABILITIES),
                ("EQT", "Equity", IFRSCategory.EQUITY),
                ("REV", "Revenue", IFRSCategory.REVENUE),
                ("EXP", "Expenses", IFRSCategory.EXPENSES),
                ("OCI", "Other Comprehensive Income", IFRSCategory.OTHER_COMPREHENSIVE_INCOME),
            ]
            seeded = []
            for index, (code, name, ifrs_category) in enumerate(defaults, start=1):
                seeded.append(
                    AccountCategory(
                        organization_id=org_id,
                        category_code=code,
                        category_name=name,
                        description=f"Default {name} category",
                        ifrs_category=ifrs_category,
                        hierarchy_level=1,
                        display_order=index,
                        is_active=True,
                    )
                )
            db.add_all(seeded)
            db.commit()
            categories = (
                db.query(AccountCategory)
                .filter(
                    AccountCategory.organization_id == org_id,
                    AccountCategory.is_active.is_(True),
                )
                .order_by(AccountCategory.category_code)
                .all()
            )

        return {
            "account": _account_form_view(account) if account else None,
            "account_categories": [_category_option_view(cat) for cat in categories],
            "account_types": [value.value for value in AccountType],
            "normal_balances": [value.value for value in NormalBalance],
            "subledger_types": ["AR", "AP", "INVENTORY", "ASSET", "BANK"],
        }

    @staticmethod
    def account_detail_context(
        db: Session,
        organization_id: str,
        account_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        account = db.get(Account, coerce_uuid(account_id))
        if not account or account.organization_id != org_id:
            return {"account": None}

        return {"account": _account_detail_view(account)}

    @staticmethod
    def create_account(
        db: Session,
        organization_id: str,
        account_code: str,
        account_name: str,
        category_id: str,
        account_type: str,
        normal_balance: str,
        description: str = "",
        search_terms: str = "",
        is_multi_currency: bool = False,
        default_currency_code: str = "USD",
        is_active: bool = True,
        is_posting_allowed: bool = True,
        is_budgetable: bool = False,
        is_reconciliation_required: bool = False,
        subledger_type: Optional[str] = None,
        is_cash_equivalent: bool = False,
        is_financial_instrument: bool = False,
    ) -> tuple[Optional[Account], Optional[str]]:
        """Create a new GL account. Returns (account, error)."""
        org_id = coerce_uuid(organization_id)

        # Validate account type
        try:
            account_type_enum = AccountType(account_type)
        except ValueError:
            return None, f"Invalid account type: {account_type}"

        # Validate normal balance
        try:
            normal_balance_enum = NormalBalance(normal_balance)
        except ValueError:
            return None, f"Invalid normal balance: {normal_balance}"

        # Validate category exists and belongs to organization
        cat_id = coerce_uuid(category_id)
        category = db.get(AccountCategory, cat_id)
        if not category or category.organization_id != org_id:
            return None, "Invalid account category"

        # Check for duplicate account code
        existing = (
            db.query(Account)
            .filter(Account.organization_id == org_id)
            .filter(Account.account_code == account_code)
            .first()
        )
        if existing:
            return None, f"Account code '{account_code}' already exists"

        try:
            account = Account(
                organization_id=org_id,
                account_code=account_code,
                account_name=account_name,
                description=description or None,
                search_terms=search_terms or None,
                category_id=cat_id,
                account_type=account_type_enum,
                normal_balance=normal_balance_enum,
                is_multi_currency=is_multi_currency,
                default_currency_code=default_currency_code,
                is_active=is_active,
                is_posting_allowed=is_posting_allowed,
                is_budgetable=is_budgetable,
                is_reconciliation_required=is_reconciliation_required,
                subledger_type=subledger_type or None,
                is_cash_equivalent=is_cash_equivalent,
                is_financial_instrument=is_financial_instrument,
            )
            db.add(account)
            db.commit()
            db.refresh(account)
            return account, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to create account: {str(e)}"

    @staticmethod
    def update_account(
        db: Session,
        organization_id: str,
        account_id: str,
        account_code: str,
        account_name: str,
        category_id: str,
        account_type: str,
        normal_balance: str,
        description: str = "",
        search_terms: str = "",
        is_multi_currency: bool = False,
        default_currency_code: str = "USD",
        is_active: bool = True,
        is_posting_allowed: bool = True,
        is_budgetable: bool = False,
        is_reconciliation_required: bool = False,
        subledger_type: Optional[str] = None,
        is_cash_equivalent: bool = False,
        is_financial_instrument: bool = False,
    ) -> tuple[Optional[Account], Optional[str]]:
        """Update an existing GL account. Returns (account, error)."""
        org_id = coerce_uuid(organization_id)
        acct_id = coerce_uuid(account_id)

        account = db.get(Account, acct_id)
        if not account or account.organization_id != org_id:
            return None, "Account not found"

        # Validate account type
        try:
            account_type_enum = AccountType(account_type)
        except ValueError:
            return None, f"Invalid account type: {account_type}"

        # Validate normal balance
        try:
            normal_balance_enum = NormalBalance(normal_balance)
        except ValueError:
            return None, f"Invalid normal balance: {normal_balance}"

        # Validate category exists and belongs to organization
        cat_id = coerce_uuid(category_id)
        category = db.get(AccountCategory, cat_id)
        if not category or category.organization_id != org_id:
            return None, "Invalid account category"

        # Check for duplicate account code (excluding current account)
        existing = (
            db.query(Account)
            .filter(Account.organization_id == org_id)
            .filter(Account.account_code == account_code)
            .filter(Account.account_id != acct_id)
            .first()
        )
        if existing:
            return None, f"Account code '{account_code}' already exists"

        try:
            account.account_code = account_code
            account.account_name = account_name
            account.description = description or None
            account.search_terms = search_terms or None
            account.category_id = cat_id
            account.account_type = account_type_enum
            account.normal_balance = normal_balance_enum
            account.is_multi_currency = is_multi_currency
            account.default_currency_code = default_currency_code
            account.is_active = is_active
            account.is_posting_allowed = is_posting_allowed
            account.is_budgetable = is_budgetable
            account.is_reconciliation_required = is_reconciliation_required
            account.subledger_type = subledger_type or None
            account.is_cash_equivalent = is_cash_equivalent
            account.is_financial_instrument = is_financial_instrument

            db.commit()
            db.refresh(account)
            return account, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to update account: {str(e)}"

    @staticmethod
    def delete_account(
        db: Session,
        organization_id: str,
        account_id: str,
    ) -> Optional[str]:
        """Delete a GL account. Returns error message or None on success."""
        org_id = coerce_uuid(organization_id)
        acct_id = coerce_uuid(account_id)

        account = db.get(Account, acct_id)
        if not account or account.organization_id != org_id:
            return "Account not found"

        # Check if account has journal entry lines
        from app.models.ifrs.gl.journal_entry_line import JournalEntryLine
        line_count = (
            db.query(JournalEntryLine)
            .filter(JournalEntryLine.account_id == acct_id)
            .count()
        )
        if line_count > 0:
            return f"Cannot delete account with {line_count} journal entries. Deactivate instead."

        # Check if account has balances
        balance_count = (
            db.query(AccountBalance)
            .filter(AccountBalance.account_id == acct_id)
            .count()
        )
        if balance_count > 0:
            return "Cannot delete account with balance records. Deactivate instead."

        try:
            db.delete(account)
            db.commit()
            return None

        except Exception as e:
            db.rollback()
            return f"Failed to delete account: {str(e)}"

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
    def journal_form_context(
        db: Session,
        organization_id: str,
        entry_id: Optional[str] = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        entry = None
        lines_view: list[dict] = []

        if entry_id:
            entry = db.get(JournalEntry, coerce_uuid(entry_id))
            if not entry or entry.organization_id != org_id:
                entry = None
            else:
                lines = (
                    db.query(JournalEntryLine, Account)
                    .join(Account, JournalEntryLine.account_id == Account.account_id)
                    .filter(JournalEntryLine.journal_entry_id == entry.journal_entry_id)
                    .order_by(JournalEntryLine.line_number)
                    .all()
                )
                lines_view = [
                    _journal_line_view(line, account, entry.currency_code)
                    for line, account in lines
                ]

        accounts = (
            db.query(Account)
            .filter(
                Account.organization_id == org_id,
                Account.is_active.is_(True),
            )
            .order_by(Account.account_code)
            .all()
        )

        periods = (
            db.query(FiscalPeriod)
            .filter(FiscalPeriod.organization_id == org_id)
            .order_by(FiscalPeriod.start_date.desc())
            .all()
        )

        return {
            "entry": _journal_entry_view(entry) if entry else None,
            "lines": lines_view,
            "accounts": [
                {
                    "account_id": account.account_id,
                    "account_code": account.account_code,
                    "account_name": account.account_name,
                }
                for account in accounts
            ],
            "journal_types": [value.value for value in JournalType],
            "fiscal_periods": [_period_option_view(period) for period in periods],
        }

    @staticmethod
    def journal_detail_context(
        db: Session,
        organization_id: str,
        entry_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        entry = db.get(JournalEntry, coerce_uuid(entry_id))
        if not entry or entry.organization_id != org_id:
            return {"entry": None, "lines": []}

        lines = (
            db.query(JournalEntryLine, Account)
            .join(Account, JournalEntryLine.account_id == Account.account_id)
            .filter(JournalEntryLine.journal_entry_id == entry.journal_entry_id)
            .order_by(JournalEntryLine.line_number)
            .all()
        )
        lines_view = [
            _journal_line_view(line, account, entry.currency_code)
            for line, account in lines
        ]

        return {
            "entry": _journal_entry_view(entry),
            "lines": lines_view,
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

        entry_counts: dict[UUID, int] = dict(
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
    def period_form_context(
        db: Session,
        organization_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        years = (
            db.query(FiscalYear)
            .filter(FiscalYear.organization_id == org_id)
            .order_by(FiscalYear.start_date.desc())
            .all()
        )

        return {"fiscal_years": [_fiscal_year_option_view(year) for year in years]}

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


    @staticmethod
    def create_journal(
        db: Session,
        organization_id: str,
        user_id: str,
        journal_type: str,
        fiscal_period_id: str,
        entry_date: str,
        posting_date: str,
        description: str,
        reference: str = "",
        currency_code: str = "USD",
        exchange_rate: str = "1.0",
        lines_json: str = "[]",
    ) -> tuple[Optional[JournalEntry], Optional[str]]:
        """Create a new journal entry with lines. Returns (entry, error)."""
        import json
        from uuid import uuid4

        org_id = coerce_uuid(organization_id)
        uid = coerce_uuid(user_id)

        # Validate journal type
        try:
            journal_type_enum = JournalType(journal_type)
        except ValueError:
            return None, f"Invalid journal type: {journal_type}"

        # Validate fiscal period
        period_id = coerce_uuid(fiscal_period_id)
        period = db.get(FiscalPeriod, period_id)
        if not period or period.organization_id != org_id:
            return None, "Invalid fiscal period"

        # Parse dates
        entry_dt = _parse_date(entry_date)
        posting_dt = _parse_date(posting_date)
        if not entry_dt:
            return None, "Invalid entry date"
        if not posting_dt:
            return None, "Invalid posting date"

        # Parse exchange rate
        try:
            rate = Decimal(exchange_rate)
        except (ValueError, TypeError):
            rate = Decimal("1.0")

        # Parse lines
        try:
            lines_data = json.loads(lines_json) if lines_json else []
        except json.JSONDecodeError:
            return None, "Invalid journal lines format"

        if not lines_data:
            return None, "Journal entry must have at least one line"

        # Validate lines and calculate totals
        total_debit = Decimal("0")
        total_credit = Decimal("0")
        validated_lines = []

        for idx, line_data in enumerate(lines_data):
            account_id = line_data.get("account_id")
            if not account_id:
                return None, f"Line {idx + 1}: Account is required"

            account = db.get(Account, coerce_uuid(account_id))
            if not account or account.organization_id != org_id:
                return None, f"Line {idx + 1}: Invalid account"

            try:
                debit = Decimal(str(line_data.get("debit", "0") or "0"))
                credit = Decimal(str(line_data.get("credit", "0") or "0"))
            except (ValueError, TypeError):
                return None, f"Line {idx + 1}: Invalid amount"

            if debit == 0 and credit == 0:
                return None, f"Line {idx + 1}: Either debit or credit must be non-zero"

            if debit != 0 and credit != 0:
                return None, f"Line {idx + 1}: Cannot have both debit and credit on same line"

            total_debit += debit
            total_credit += credit
            validated_lines.append({
                "account_id": coerce_uuid(account_id),
                "description": line_data.get("description", ""),
                "debit": debit,
                "credit": credit,
            })

        # Check balance
        if total_debit != total_credit:
            return None, f"Journal is out of balance. Debit: {total_debit}, Credit: {total_credit}"

        # Generate journal number
        count = (
            db.query(JournalEntry)
            .filter(JournalEntry.organization_id == org_id)
            .count()
        )
        journal_number = f"JE-{count + 1:06d}"

        try:
            entry = JournalEntry(
                organization_id=org_id,
                journal_number=journal_number,
                journal_type=journal_type_enum,
                entry_date=entry_dt,
                posting_date=posting_dt,
                fiscal_period_id=period_id,
                description=description,
                reference=reference or None,
                currency_code=currency_code,
                exchange_rate=rate,
                total_debit=total_debit,
                total_credit=total_credit,
                total_debit_functional=total_debit * rate,
                total_credit_functional=total_credit * rate,
                status=JournalStatus.DRAFT,
                created_by_user_id=uid,
            )
            db.add(entry)
            db.flush()

            # Create lines
            for idx, line_data in enumerate(validated_lines):
                line = JournalEntryLine(
                    journal_entry_id=entry.journal_entry_id,
                    line_number=idx + 1,
                    account_id=line_data["account_id"],
                    description=line_data["description"] or None,
                    debit_amount=line_data["debit"],
                    credit_amount=line_data["credit"],
                    debit_amount_functional=line_data["debit"] * rate,
                    credit_amount_functional=line_data["credit"] * rate,
                )
                db.add(line)

            db.commit()
            db.refresh(entry)
            return entry, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to create journal entry: {str(e)}"

    @staticmethod
    def update_journal(
        db: Session,
        organization_id: str,
        entry_id: str,
        journal_type: str,
        fiscal_period_id: str,
        entry_date: str,
        posting_date: str,
        description: str,
        reference: str = "",
        currency_code: str = "USD",
        exchange_rate: str = "1.0",
        lines_json: str = "[]",
    ) -> tuple[Optional[JournalEntry], Optional[str]]:
        """Update a journal entry. Only DRAFT entries can be updated. Returns (entry, error)."""
        import json

        org_id = coerce_uuid(organization_id)
        ent_id = coerce_uuid(entry_id)

        entry = db.get(JournalEntry, ent_id)
        if not entry or entry.organization_id != org_id:
            return None, "Journal entry not found"

        if entry.status != JournalStatus.DRAFT:
            return None, f"Cannot edit journal entry with status: {entry.status.value}"

        # Validate journal type
        try:
            journal_type_enum = JournalType(journal_type)
        except ValueError:
            return None, f"Invalid journal type: {journal_type}"

        # Validate fiscal period
        period_id = coerce_uuid(fiscal_period_id)
        period = db.get(FiscalPeriod, period_id)
        if not period or period.organization_id != org_id:
            return None, "Invalid fiscal period"

        # Parse dates
        entry_dt = _parse_date(entry_date)
        posting_dt = _parse_date(posting_date)
        if not entry_dt:
            return None, "Invalid entry date"
        if not posting_dt:
            return None, "Invalid posting date"

        # Parse exchange rate
        try:
            rate = Decimal(exchange_rate)
        except (ValueError, TypeError):
            rate = Decimal("1.0")

        # Parse lines
        try:
            lines_data = json.loads(lines_json) if lines_json else []
        except json.JSONDecodeError:
            return None, "Invalid journal lines format"

        if not lines_data:
            return None, "Journal entry must have at least one line"

        # Validate lines and calculate totals
        total_debit = Decimal("0")
        total_credit = Decimal("0")
        validated_lines = []

        for idx, line_data in enumerate(lines_data):
            account_id = line_data.get("account_id")
            if not account_id:
                return None, f"Line {idx + 1}: Account is required"

            account = db.get(Account, coerce_uuid(account_id))
            if not account or account.organization_id != org_id:
                return None, f"Line {idx + 1}: Invalid account"

            try:
                debit = Decimal(str(line_data.get("debit", "0") or "0"))
                credit = Decimal(str(line_data.get("credit", "0") or "0"))
            except (ValueError, TypeError):
                return None, f"Line {idx + 1}: Invalid amount"

            if debit == 0 and credit == 0:
                return None, f"Line {idx + 1}: Either debit or credit must be non-zero"

            if debit != 0 and credit != 0:
                return None, f"Line {idx + 1}: Cannot have both debit and credit on same line"

            total_debit += debit
            total_credit += credit
            validated_lines.append({
                "account_id": coerce_uuid(account_id),
                "description": line_data.get("description", ""),
                "debit": debit,
                "credit": credit,
            })

        # Check balance
        if total_debit != total_credit:
            return None, f"Journal is out of balance. Debit: {total_debit}, Credit: {total_credit}"

        try:
            # Update header
            entry.journal_type = journal_type_enum
            entry.fiscal_period_id = period_id
            entry.entry_date = entry_dt
            entry.posting_date = posting_dt
            entry.description = description
            entry.reference = reference or None
            entry.currency_code = currency_code
            entry.exchange_rate = rate
            entry.total_debit = total_debit
            entry.total_credit = total_credit
            entry.total_debit_functional = total_debit * rate
            entry.total_credit_functional = total_credit * rate

            # Delete existing lines
            db.query(JournalEntryLine).filter(
                JournalEntryLine.journal_entry_id == ent_id
            ).delete()

            # Create new lines
            for idx, line_data in enumerate(validated_lines):
                line = JournalEntryLine(
                    journal_entry_id=ent_id,
                    line_number=idx + 1,
                    account_id=line_data["account_id"],
                    description=line_data["description"] or None,
                    debit_amount=line_data["debit"],
                    credit_amount=line_data["credit"],
                    debit_amount_functional=line_data["debit"] * rate,
                    credit_amount_functional=line_data["credit"] * rate,
                )
                db.add(line)

            db.commit()
            db.refresh(entry)
            return entry, None

        except Exception as e:
            db.rollback()
            return None, f"Failed to update journal entry: {str(e)}"

    @staticmethod
    def delete_journal(
        db: Session,
        organization_id: str,
        entry_id: str,
    ) -> Optional[str]:
        """Delete a journal entry. Only DRAFT entries can be deleted. Returns error message or None."""
        org_id = coerce_uuid(organization_id)
        ent_id = coerce_uuid(entry_id)

        entry = db.get(JournalEntry, ent_id)
        if not entry or entry.organization_id != org_id:
            return "Journal entry not found"

        if entry.status != JournalStatus.DRAFT:
            return f"Cannot delete journal entry with status: {entry.status.value}. Only DRAFT entries can be deleted."

        try:
            # Lines will be cascade deleted due to relationship
            db.delete(entry)
            db.commit()
            return None

        except Exception as e:
            db.rollback()
            return f"Failed to delete journal entry: {str(e)}"


gl_web_service = GLWebService()
