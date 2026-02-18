"""
DashboardService - IFRS Dashboard data aggregation.

Provides aggregated statistics and data for the IFRS dashboard.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, case, extract, func, or_, select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.gl.account import Account
from app.models.finance.gl.account_balance import AccountBalance
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


def _safe_decimal(value, default: Decimal = Decimal("0")) -> Decimal:
    """Safely convert a value to Decimal."""
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _year_bounds(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def _apply_year_filter(query, column, year: int | None):
    if year is None:
        return query
    start_date, end_date = _year_bounds(year)
    if hasattr(query, "where"):
        return query.where(column >= start_date, column <= end_date)
    return query.filter(column >= start_date, column <= end_date)


def _cogs_match_clause():
    keywords = [
        "%cost of goods%",
        "%cost of sales%",
        "%cogs%",
        "%purchase%",
        "%purchases%",
    ]
    return or_(
        *[AccountCategory.category_name.ilike(pattern) for pattern in keywords],
        *[AccountCategory.category_code.ilike(pattern) for pattern in keywords],
        *[Account.account_name.ilike(pattern) for pattern in keywords],
    )


@dataclass
class DashboardStats:
    """Dashboard statistics."""

    total_revenue: Decimal
    total_expenses: Decimal
    net_income: Decimal
    cogs_spend: Decimal
    opex_spend: Decimal
    ar_control_balance: Decimal
    ap_control_balance: Decimal
    cash_inflow: Decimal
    cash_outflow: Decimal
    net_cash_flow: Decimal
    open_ar_invoices: int = 0
    open_ap_invoices: int = 0
    pending_ar_amount: Decimal = Decimal("0")
    pending_ap_amount: Decimal = Decimal("0")
    # Aging buckets
    aging_current: Decimal = Decimal("0")
    aging_30: Decimal = Decimal("0")
    aging_60: Decimal = Decimal("0")
    aging_90: Decimal = Decimal("0")
    aging_current_pct: float = 0.0
    aging_30_pct: float = 0.0
    aging_60_pct: float = 0.0
    aging_90_pct: float = 0.0
    # Trend indicators
    revenue_trend: float | None = None
    income_trend: float | None = None


@dataclass
class JournalEntrySummary:
    """Journal entry summary for dashboard."""

    entry_number: str
    entry_date: str
    description: str
    total_debit: Decimal
    status: str


@dataclass
class FiscalPeriodSummary:
    """Fiscal period summary for dashboard."""

    period_name: str
    start_date: str
    end_date: str
    status: str


class DashboardService:
    """
    Service for dashboard data aggregation.

    Aggregates data from AR, AP, GL, and other modules for dashboard display.
    """

    @staticmethod
    def get_available_years(db: Session, organization_id: UUID) -> list[int]:
        org_id = coerce_uuid(organization_id)
        sources = [
            (Invoice, Invoice.invoice_date),
            (SupplierInvoice, SupplierInvoice.invoice_date),
            (JournalEntry, JournalEntry.entry_date),
            (FiscalPeriod, FiscalPeriod.start_date),
        ]
        years: set[int] = set()
        for model, column in sources:
            org_col = cast(Any, model).organization_id
            rows = db.execute(
                select(extract("year", column))
                .where(org_col == org_id, column.isnot(None))
                .distinct()
            ).all()
            for (year_value,) in rows:
                if year_value is not None:
                    years.add(int(year_value))
        return sorted(years, reverse=True)

    @staticmethod
    def get_cogs_opex_spend(
        db: Session,
        organization_id: UUID,
        year: int | None = None,
    ) -> tuple[Decimal, Decimal]:
        org_id = coerce_uuid(organization_id)
        cogs_match = _cogs_match_clause()

        spend_stmt = (
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    cogs_match,
                                    AccountCategory.ifrs_category
                                    == IFRSCategory.EXPENSES,
                                ),
                                PostedLedgerLine.debit_amount
                                - PostedLedgerLine.credit_amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("cogs_spend"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    ~cogs_match,
                                    AccountCategory.ifrs_category
                                    == IFRSCategory.EXPENSES,
                                ),
                                PostedLedgerLine.debit_amount
                                - PostedLedgerLine.credit_amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("opex_spend"),
            )
            .join(Account, Account.account_id == PostedLedgerLine.account_id)
            .join(AccountCategory, AccountCategory.category_id == Account.category_id)
            .where(PostedLedgerLine.organization_id == org_id)
        )
        spend_stmt = _apply_year_filter(spend_stmt, PostedLedgerLine.posting_date, year)
        cogs_spend, opex_spend = db.execute(spend_stmt).one()
        return _safe_decimal(cogs_spend), _safe_decimal(opex_spend)

    @staticmethod
    def get_gl_revenue_expenses(
        db: Session,
        organization_id: UUID,
        year: int | None = None,
    ) -> tuple[Decimal, Decimal]:
        org_id = coerce_uuid(organization_id)
        revenue_expense_stmt = (
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                AccountCategory.ifrs_category == IFRSCategory.REVENUE,
                                PostedLedgerLine.credit_amount
                                - PostedLedgerLine.debit_amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("revenue_total"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                                PostedLedgerLine.debit_amount
                                - PostedLedgerLine.credit_amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("expense_total"),
            )
            .join(Account, Account.account_id == PostedLedgerLine.account_id)
            .join(AccountCategory, AccountCategory.category_id == Account.category_id)
            .where(
                PostedLedgerLine.organization_id == org_id,
                AccountCategory.organization_id == org_id,
                Account.is_active.is_(True),
            )
        )
        revenue_expense_stmt = _apply_year_filter(
            revenue_expense_stmt, PostedLedgerLine.posting_date, year
        )
        revenue_total, expense_total = db.execute(revenue_expense_stmt).one()
        return _safe_decimal(revenue_total), _safe_decimal(expense_total)

    @staticmethod
    def get_gl_control_balances(
        db: Session,
        organization_id: UUID,
        year: int | None = None,
    ) -> tuple[Decimal, Decimal]:
        org_id = coerce_uuid(organization_id)

        ar_stmt = (
            select(
                func.coalesce(
                    func.sum(
                        PostedLedgerLine.debit_amount - PostedLedgerLine.credit_amount
                    ),
                    0,
                )
            )
            .join(Account, Account.account_id == PostedLedgerLine.account_id)
            .where(
                PostedLedgerLine.organization_id == org_id,
                Account.subledger_type == "AR",
                Account.is_active.is_(True),
            )
        )
        ar_stmt = _apply_year_filter(ar_stmt, PostedLedgerLine.posting_date, year)
        ar_balance = _safe_decimal(db.scalar(ar_stmt))

        ap_stmt = (
            select(
                func.coalesce(
                    func.sum(
                        PostedLedgerLine.credit_amount - PostedLedgerLine.debit_amount
                    ),
                    0,
                )
            )
            .join(Account, Account.account_id == PostedLedgerLine.account_id)
            .where(
                PostedLedgerLine.organization_id == org_id,
                Account.subledger_type == "AP",
                Account.is_active.is_(True),
            )
        )
        ap_stmt = _apply_year_filter(ap_stmt, PostedLedgerLine.posting_date, year)
        ap_balance = _safe_decimal(db.scalar(ap_stmt))

        return ar_balance, ap_balance

    @staticmethod
    def get_cash_flow_summary(
        db: Session,
        organization_id: UUID,
        year: int | None = None,
        days: int = 30,
    ) -> tuple[Decimal, Decimal, Decimal]:
        org_id = coerce_uuid(organization_id)
        today = date.today()
        start_date = today - timedelta(days=days)

        cash_stmt = (
            select(
                func.coalesce(func.sum(PostedLedgerLine.debit_amount), 0).label(
                    "inflow"
                ),
                func.coalesce(func.sum(PostedLedgerLine.credit_amount), 0).label(
                    "outflow"
                ),
            )
            .join(Account, Account.account_id == PostedLedgerLine.account_id)
            .where(
                PostedLedgerLine.organization_id == org_id,
                PostedLedgerLine.posting_date >= start_date,
                PostedLedgerLine.posting_date <= today,
                Account.is_cash_equivalent.is_(True),
                Account.is_active.is_(True),
            )
        )
        if year is not None:
            cash_stmt = _apply_year_filter(
                cash_stmt, PostedLedgerLine.posting_date, year
            )
        inflow, outflow = db.execute(cash_stmt).one()
        inflow = _safe_decimal(inflow)
        outflow = _safe_decimal(outflow)
        return inflow, outflow, inflow - outflow

    @staticmethod
    def get_subledger_reconciliation(
        db: Session,
        organization_id: UUID,
        year: int | None = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)

        gl_ar_balance, gl_ap_balance = DashboardService.get_gl_control_balances(
            db, org_id, year=year
        )

        open_ar_statuses = [
            InvoiceStatus.DRAFT.value,
            InvoiceStatus.SUBMITTED.value,
            InvoiceStatus.APPROVED.value,
            InvoiceStatus.PARTIALLY_PAID.value,
        ]
        ar_subledger_stmt = select(
            func.coalesce(
                func.sum(Invoice.total_amount - Invoice.amount_paid),
                0,
            )
        ).where(
            Invoice.organization_id == org_id,
            Invoice.status.in_(open_ar_statuses),
        )
        ar_subledger_stmt = _apply_year_filter(
            ar_subledger_stmt, Invoice.invoice_date, year
        )
        ar_subledger_balance = _safe_decimal(db.scalar(ar_subledger_stmt))

        open_ap_statuses = [
            SupplierInvoiceStatus.DRAFT.value,
            SupplierInvoiceStatus.SUBMITTED.value,
            SupplierInvoiceStatus.APPROVED.value,
            SupplierInvoiceStatus.PARTIALLY_PAID.value,
        ]
        ap_subledger_stmt = select(
            func.coalesce(
                func.sum(SupplierInvoice.total_amount - SupplierInvoice.amount_paid),
                0,
            )
        ).where(
            SupplierInvoice.organization_id == org_id,
            SupplierInvoice.status.in_(open_ap_statuses),
        )
        ap_subledger_stmt = _apply_year_filter(
            ap_subledger_stmt, SupplierInvoice.invoice_date, year
        )
        ap_subledger_balance = _safe_decimal(db.scalar(ap_subledger_stmt))

        tolerance = Decimal("0.01")
        ar_diff = (gl_ar_balance - ar_subledger_balance).copy_abs()
        ap_diff = (gl_ap_balance - ap_subledger_balance).copy_abs()

        return {
            "gl_ar_balance": gl_ar_balance,
            "gl_ap_balance": gl_ap_balance,
            "subledger_ar_balance": ar_subledger_balance,
            "subledger_ap_balance": ap_subledger_balance,
            "ar_diff": ar_diff,
            "ap_diff": ap_diff,
            "ar_ok": ar_diff <= tolerance,
            "ap_ok": ap_diff <= tolerance,
        }

    @staticmethod
    def get_stats(
        db: Session,
        organization_id: UUID,
        year: int | None = None,
    ) -> DashboardStats:
        """
        Get dashboard statistics.

        Args:
            db: Database session
            organization_id: Organization scope

        Returns:
            DashboardStats with revenue, expenses, and invoice counts
        """
        org_id = coerce_uuid(organization_id)

        revenue, expenses = DashboardService.get_gl_revenue_expenses(
            db, org_id, year=year
        )

        cogs_spend, opex_spend = DashboardService.get_cogs_opex_spend(
            db, org_id, year=year
        )
        ar_control_balance, ap_control_balance = (
            DashboardService.get_gl_control_balances(db, org_id, year=year)
        )
        cash_inflow, cash_outflow, net_cash_flow = (
            DashboardService.get_cash_flow_summary(db, org_id, year=year)
        )

        # Convert to Decimal safely
        ar_control = _safe_decimal(ar_control_balance)
        ap_control = _safe_decimal(ap_control_balance)
        inflow = _safe_decimal(cash_inflow)
        outflow = _safe_decimal(cash_outflow)
        net_cash = _safe_decimal(net_cash_flow)

        # Get aging data
        aging = DashboardService.get_ar_aging(db, org_id, year=year)

        # Get trend data
        trends = DashboardService.get_revenue_expense_trend(db, org_id, year=year)

        return DashboardStats(
            total_revenue=revenue,
            total_expenses=expenses,
            net_income=revenue - expenses,
            cogs_spend=cogs_spend,
            opex_spend=opex_spend,
            ar_control_balance=ar_control,
            ap_control_balance=ap_control,
            cash_inflow=inflow,
            cash_outflow=outflow,
            net_cash_flow=net_cash,
            # Aging data
            aging_current=aging["aging_current"],
            aging_30=aging["aging_30"],
            aging_60=aging["aging_60"],
            aging_90=aging["aging_90"],
            aging_current_pct=aging["aging_current_pct"],
            aging_30_pct=aging["aging_30_pct"],
            aging_60_pct=aging["aging_60_pct"],
            aging_90_pct=aging["aging_90_pct"],
            # Trend data
            revenue_trend=trends["revenue_trend"],
            income_trend=trends["income_trend"],
        )

    @staticmethod
    def get_recent_journals(
        db: Session,
        organization_id: UUID,
        limit: int = 10,
        year: int | None = None,
    ) -> list[JournalEntrySummary]:
        """
        Get recent journal entries.

        Args:
            db: Database session
            organization_id: Organization scope
            limit: Maximum number of entries to return

        Returns:
            List of JournalEntrySummary objects
        """
        org_id = coerce_uuid(organization_id)

        journals_stmt = select(JournalEntry).where(
            JournalEntry.organization_id == org_id,
            JournalEntry.status == JournalStatus.POSTED,
        )
        journals_stmt = _apply_year_filter(
            journals_stmt, JournalEntry.posting_date, year
        )
        journals_stmt = journals_stmt.order_by(JournalEntry.posting_date.desc())
        journals = db.scalars(journals_stmt.limit(limit)).all()

        result = []
        for journal in journals:
            try:
                # Calculate total debit from lines
                total_debit = Decimal("0")
                if journal.lines:
                    total_debit = sum(
                        (_safe_decimal(line.debit_amount) for line in journal.lines),
                        Decimal("0"),
                    )

                result.append(
                    JournalEntrySummary(
                        entry_number=journal.journal_number or "",
                        entry_date=journal.posting_date.strftime("%Y-%m-%d")
                        if journal.posting_date
                        else "",
                        description=journal.description or "",
                        total_debit=total_debit,
                        status=journal.status.value if journal.status else "DRAFT",
                    )
                )
            except (AttributeError, TypeError) as e:
                logger.warning(f"Error processing journal entry: {e}")
                continue

        return result

    @staticmethod
    def get_fiscal_periods(
        db: Session,
        organization_id: UUID,
        limit: int = 8,
        year: int | None = None,
    ) -> list[FiscalPeriodSummary]:
        """
        Get fiscal periods.

        Args:
            db: Database session
            organization_id: Organization scope
            limit: Maximum number of periods to return

        Returns:
            List of FiscalPeriodSummary objects
        """
        org_id = coerce_uuid(organization_id)

        periods_stmt = select(FiscalPeriod).where(
            FiscalPeriod.organization_id == org_id
        )
        periods_stmt = _apply_year_filter(periods_stmt, FiscalPeriod.start_date, year)
        periods = db.scalars(
            periods_stmt.order_by(FiscalPeriod.start_date.desc()).limit(limit)
        ).all()

        result = []
        for period in periods:
            result.append(
                FiscalPeriodSummary(
                    period_name=period.period_name,
                    start_date=period.start_date.strftime("%Y-%m-%d")
                    if period.start_date
                    else "",
                    end_date=period.end_date.strftime("%Y-%m-%d")
                    if period.end_date
                    else "",
                    status=period.status.value if period.status else "FUTURE",
                )
            )

        return result

    @staticmethod
    def get_monthly_revenue_expenses(
        db: Session,
        organization_id: UUID,
        months: int = 12,
        year: int | None = None,
    ) -> list[dict]:
        """
        Get monthly revenue and expense totals for trend chart.

        Args:
            db: Database session
            organization_id: Organization scope
            months: Number of months to include

        Returns:
            List of dicts with month, revenue, expenses keys
        """
        org_id = coerce_uuid(organization_id)
        today = date.today()
        if year is None:
            start_date = today.replace(day=1) - timedelta(days=(months - 1) * 30)
            start_date = start_date.replace(day=1)
            end_date = today
        else:
            start_date, end_date = _year_bounds(year)

        monthly_rows = db.execute(
            select(
                extract("year", PostedLedgerLine.posting_date).label("year"),
                extract("month", PostedLedgerLine.posting_date).label("month"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                AccountCategory.ifrs_category == IFRSCategory.REVENUE,
                                PostedLedgerLine.credit_amount
                                - PostedLedgerLine.debit_amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("revenue_total"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                                PostedLedgerLine.debit_amount
                                - PostedLedgerLine.credit_amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("expense_total"),
            )
            .join(Account, Account.account_id == PostedLedgerLine.account_id)
            .join(AccountCategory, AccountCategory.category_id == Account.category_id)
            .where(
                PostedLedgerLine.organization_id == org_id,
                PostedLedgerLine.posting_date >= start_date,
                PostedLedgerLine.posting_date <= end_date,
                AccountCategory.organization_id == org_id,
                Account.is_active.is_(True),
            )
            .group_by(
                extract("year", PostedLedgerLine.posting_date),
                extract("month", PostedLedgerLine.posting_date),
            )
        ).all()

        monthly_dict = {
            (int(r.year), int(r.month)): {
                "revenue": _safe_decimal(r.revenue_total),
                "expenses": _safe_decimal(r.expense_total),
            }
            for r in monthly_rows
        }

        result = []
        current = start_date
        while current <= end_date:
            key = (current.year, current.month)
            totals = monthly_dict.get(
                key, {"revenue": Decimal("0"), "expenses": Decimal("0")}
            )
            result.append(
                {
                    "month": current.strftime("%b %Y"),
                    "month_short": current.strftime("%b"),
                    "revenue": float(totals["revenue"]),
                    "expenses": float(totals["expenses"]),
                }
            )
            # Move to next month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

        return result

    @staticmethod
    def get_account_balances_by_ifrs_category(
        db: Session,
        organization_id: UUID,
        year: int | None = None,
    ) -> list[dict]:
        """
        Get account balances aggregated by IFRS category for donut chart.

        Args:
            db: Database session
            organization_id: Organization scope

        Returns:
            List of dicts with category name and total balance
        """
        org_id = coerce_uuid(organization_id)

        # Join accounts with categories to get IFRS classification
        balances_stmt = (
            select(
                AccountCategory.ifrs_category,
                func.coalesce(func.sum(AccountBalance.net_balance), 0).label("total"),
            )
            .join(Account, Account.category_id == AccountCategory.category_id)
            .join(
                AccountBalance,
                and_(
                    AccountBalance.account_id == Account.account_id,
                    AccountBalance.organization_id == org_id,
                ),
                isouter=True,
            )
        )
        if year is not None:
            start_date, end_date = _year_bounds(year)
            balances_stmt = balances_stmt.join(
                FiscalPeriod,
                FiscalPeriod.fiscal_period_id == AccountBalance.fiscal_period_id,
            ).where(
                FiscalPeriod.start_date >= start_date,
                FiscalPeriod.start_date <= end_date,
            )

        results = db.execute(
            balances_stmt.where(
                AccountCategory.organization_id == org_id,
                Account.is_active.is_(True),
            ).group_by(AccountCategory.ifrs_category)
        ).all()
        if not results:
            ledger_stmt = (
                select(
                    AccountCategory.ifrs_category,
                    func.coalesce(
                        func.sum(
                            PostedLedgerLine.debit_amount
                            - PostedLedgerLine.credit_amount
                        ),
                        0,
                    ).label("total"),
                )
                .join(Account, Account.account_id == PostedLedgerLine.account_id)
                .join(
                    AccountCategory, AccountCategory.category_id == Account.category_id
                )
                .where(
                    PostedLedgerLine.organization_id == org_id,
                    AccountCategory.organization_id == org_id,
                    Account.is_active.is_(True),
                )
            )
            ledger_stmt = _apply_year_filter(
                ledger_stmt, PostedLedgerLine.posting_date, year
            )
            results = db.execute(
                ledger_stmt.group_by(AccountCategory.ifrs_category)
            ).all()
        if not results:
            results = []

        # Map to friendly names and colors
        category_config = {
            IFRSCategory.ASSETS: {"name": "Assets", "color": "#0d9488"},
            IFRSCategory.LIABILITIES: {"name": "Liabilities", "color": "#f97316"},
            IFRSCategory.EQUITY: {"name": "Equity", "color": "#8b5cf6"},
            IFRSCategory.REVENUE: {"name": "Revenue", "color": "#10b981"},
            IFRSCategory.EXPENSES: {"name": "Expenses", "color": "#ef4444"},
            IFRSCategory.OTHER_COMPREHENSIVE_INCOME: {
                "name": "OCI",
                "color": "#d97706",
            },
        }

        return [
            {
                "category": category_config.get(r.ifrs_category, {}).get(
                    "name", str(r.ifrs_category)
                ),
                "balance": abs(float(_safe_decimal(r.total))),
                "value": abs(float(_safe_decimal(r.total))),
                "color": category_config.get(r.ifrs_category, {}).get(
                    "color", "#64748b"
                ),
            }
            for r in results
            if _safe_decimal(r.total) != 0
        ]

    @staticmethod
    def get_top_customers(
        db: Session,
        organization_id: UUID,
        limit: int = 5,
        year: int | None = None,
    ) -> list[dict]:
        """
        Get top customers by posted GL revenue.

        Args:
            db: Database session
            organization_id: Organization scope
            limit: Maximum number of customers

        Returns:
            List of dicts with customer name and total amount
        """
        org_id = coerce_uuid(organization_id)

        customers_stmt = (
            select(
                Customer.legal_name,
                Customer.trading_name,
                func.coalesce(
                    func.sum(
                        PostedLedgerLine.credit_amount - PostedLedgerLine.debit_amount
                    ),
                    0,
                ).label("total"),
            )
            .join(Invoice, Invoice.invoice_id == PostedLedgerLine.source_document_id)
            .join(Customer, Customer.customer_id == Invoice.customer_id)
            .join(Account, Account.account_id == PostedLedgerLine.account_id)
            .join(AccountCategory, AccountCategory.category_id == Account.category_id)
            .where(
                Customer.organization_id == org_id,
                PostedLedgerLine.organization_id == org_id,
                PostedLedgerLine.source_document_type == "INVOICE",
                AccountCategory.ifrs_category == IFRSCategory.REVENUE,
            )
        )
        customers_stmt = _apply_year_filter(
            customers_stmt, PostedLedgerLine.posting_date, year
        )

        results = db.execute(
            customers_stmt.group_by(
                Customer.customer_id, Customer.legal_name, Customer.trading_name
            )
            .order_by(
                func.sum(PostedLedgerLine.credit_amount - PostedLedgerLine.debit_amount)
                .desc()
                .nullslast()
            )
            .limit(limit)
        ).all()

        return [
            {
                "name": r.trading_name or r.legal_name,
                "value": float(_safe_decimal(r.total)),
                "revenue": float(_safe_decimal(r.total)),  # Alias for template
            }
            for r in results
            if _safe_decimal(r.total) > 0
        ]

    @staticmethod
    def get_top_suppliers(
        db: Session,
        organization_id: UUID,
        limit: int = 5,
        year: int | None = None,
    ) -> list[dict]:
        """
        Get top suppliers by COGS/purchases spend.

        Args:
            db: Database session
            organization_id: Organization scope
            limit: Maximum number of suppliers

        Returns:
            List of dicts with supplier name and total amount
        """
        org_id = coerce_uuid(organization_id)

        cogs_match = _cogs_match_clause()
        suppliers_stmt = (
            select(
                Supplier.legal_name,
                Supplier.trading_name,
                func.coalesce(
                    func.sum(
                        PostedLedgerLine.debit_amount - PostedLedgerLine.credit_amount
                    ),
                    0,
                ).label("total"),
            )
            .join(
                SupplierInvoice,
                SupplierInvoice.invoice_id == PostedLedgerLine.source_document_id,
            )
            .join(Supplier, Supplier.supplier_id == SupplierInvoice.supplier_id)
            .join(Account, Account.account_id == PostedLedgerLine.account_id)
            .join(AccountCategory, AccountCategory.category_id == Account.category_id)
            .where(
                Supplier.organization_id == org_id,
                PostedLedgerLine.organization_id == org_id,
                PostedLedgerLine.source_document_type == "SUPPLIER_INVOICE",
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                cogs_match,
            )
        )
        suppliers_stmt = _apply_year_filter(
            suppliers_stmt, PostedLedgerLine.posting_date, year
        )

        results = db.execute(
            suppliers_stmt.group_by(
                Supplier.supplier_id, Supplier.legal_name, Supplier.trading_name
            )
            .order_by(
                func.sum(PostedLedgerLine.debit_amount - PostedLedgerLine.credit_amount)
                .desc()
                .nullslast()
            )
            .limit(limit)
        ).all()

        return [
            {
                "name": r.trading_name or r.legal_name,
                "value": float(_safe_decimal(r.total)),
                "spend": float(_safe_decimal(r.total)),  # Alias for template
            }
            for r in results
            if _safe_decimal(r.total) > 0
        ]

    @staticmethod
    def get_monthly_cash_flow(
        db: Session,
        organization_id: UUID,
        months: int = 6,
        year: int | None = None,
    ) -> list[dict]:
        """
        Get monthly cash flow data (inflows vs outflows).

        Args:
            db: Database session
            organization_id: Organization scope
            months: Number of months

        Returns:
            List of dicts with month, inflow, outflow keys
        """
        org_id = coerce_uuid(organization_id)
        today = date.today()
        if year is None:
            start_date = today.replace(day=1) - timedelta(days=(months - 1) * 30)
            start_date = start_date.replace(day=1)
            end_date = today
        else:
            start_date, end_date = _year_bounds(year)

        cash_rows = db.execute(
            select(
                extract("year", PostedLedgerLine.posting_date).label("year"),
                extract("month", PostedLedgerLine.posting_date).label("month"),
                func.coalesce(func.sum(PostedLedgerLine.debit_amount), 0).label(
                    "inflow"
                ),
                func.coalesce(func.sum(PostedLedgerLine.credit_amount), 0).label(
                    "outflow"
                ),
            )
            .join(Account, Account.account_id == PostedLedgerLine.account_id)
            .where(
                PostedLedgerLine.organization_id == org_id,
                PostedLedgerLine.posting_date >= start_date,
                PostedLedgerLine.posting_date <= end_date,
                Account.is_cash_equivalent.is_(True),
                Account.is_active.is_(True),
            )
            .group_by(
                extract("year", PostedLedgerLine.posting_date),
                extract("month", PostedLedgerLine.posting_date),
            )
        ).all()

        cash_dict = {
            (int(r.year), int(r.month)): {
                "inflow": _safe_decimal(r.inflow),
                "outflow": _safe_decimal(r.outflow),
            }
            for r in cash_rows
        }

        result = []
        current = start_date
        while current <= end_date:
            key = (current.year, current.month)
            totals = cash_dict.get(
                key, {"inflow": Decimal("0"), "outflow": Decimal("0")}
            )
            inflow = float(totals["inflow"])
            outflow = float(totals["outflow"])
            result.append(
                {
                    "month": current.strftime("%b"),
                    "inflow": inflow,
                    "outflow": outflow,
                    "net": inflow - outflow,
                }
            )
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

        return result

    @staticmethod
    def get_ar_aging(
        db: Session,
        organization_id: UUID,
        year: int | None = None,
    ) -> dict:
        """
        Calculate AR aging buckets for open invoices.

        Args:
            db: Database session
            organization_id: Organization scope
            year: Optional year filter

        Returns:
            Dict with aging amounts and percentages for current, 1-30, 31-60, 60+ days
        """
        org_id = coerce_uuid(organization_id)
        today = date.today()

        # Query open AR invoices
        open_statuses = [
            InvoiceStatus.DRAFT.value,
            InvoiceStatus.SUBMITTED.value,
            InvoiceStatus.APPROVED.value,
            InvoiceStatus.PARTIALLY_PAID.value,
        ]

        invoices_stmt = select(
            Invoice.due_date,
            (Invoice.total_amount - Invoice.amount_paid).label("outstanding"),
        ).where(
            Invoice.organization_id == org_id,
            Invoice.status.in_(open_statuses),
        )
        invoices_stmt = _apply_year_filter(invoices_stmt, Invoice.invoice_date, year)
        invoices = db.execute(invoices_stmt).all()

        # Initialize buckets
        current = Decimal("0")
        days_1_30 = Decimal("0")
        days_31_60 = Decimal("0")
        days_60_plus = Decimal("0")

        for inv in invoices:
            outstanding = _safe_decimal(inv.outstanding)
            if outstanding <= 0:
                continue

            if inv.due_date is None:
                # No due date, treat as current
                current += outstanding
                continue

            days_overdue = (today - inv.due_date).days

            if days_overdue <= 0:
                current += outstanding
            elif days_overdue <= 30:
                days_1_30 += outstanding
            elif days_overdue <= 60:
                days_31_60 += outstanding
            else:
                days_60_plus += outstanding

        total = current + days_1_30 + days_31_60 + days_60_plus

        # Calculate percentages (avoid division by zero)
        if total > 0:
            current_pct = float((current / total) * 100)
            days_1_30_pct = float((days_1_30 / total) * 100)
            days_31_60_pct = float((days_31_60 / total) * 100)
            days_60_plus_pct = float((days_60_plus / total) * 100)
        else:
            current_pct = days_1_30_pct = days_31_60_pct = days_60_plus_pct = 0.0

        return {
            "aging_current": current,
            "aging_30": days_1_30,
            "aging_60": days_31_60,
            "aging_90": days_60_plus,
            "aging_current_pct": round(current_pct, 1),
            "aging_30_pct": round(days_1_30_pct, 1),
            "aging_60_pct": round(days_31_60_pct, 1),
            "aging_90_pct": round(days_60_plus_pct, 1),
            "aging_total": total,
        }

    @staticmethod
    def get_revenue_expense_trend(
        db: Session,
        organization_id: UUID,
        year: int | None = None,
    ) -> dict:
        """
        Calculate period-over-period trend for revenue and net income.

        Compares current period (last 30 days or current year) to previous period.

        Args:
            db: Database session
            organization_id: Organization scope
            year: Optional year filter

        Returns:
            Dict with revenue_trend and income_trend percentages
        """
        org_id = coerce_uuid(organization_id)
        today = date.today()

        if year is not None:
            # Compare selected year to previous year
            current_start, current_end = _year_bounds(year)
            prev_start, prev_end = _year_bounds(year - 1)
        else:
            # Compare last 30 days to previous 30 days
            current_end = today
            current_start = today - timedelta(days=30)
            prev_end = current_start - timedelta(days=1)
            prev_start = prev_end - timedelta(days=30)

        def get_period_totals(start: date, end: date) -> tuple[Decimal, Decimal]:
            """Get revenue and expenses for a date range."""
            result = db.execute(
                select(
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    AccountCategory.ifrs_category
                                    == IFRSCategory.REVENUE,
                                    PostedLedgerLine.credit_amount
                                    - PostedLedgerLine.debit_amount,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("revenue"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    AccountCategory.ifrs_category
                                    == IFRSCategory.EXPENSES,
                                    PostedLedgerLine.debit_amount
                                    - PostedLedgerLine.credit_amount,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("expenses"),
                )
                .join(Account, Account.account_id == PostedLedgerLine.account_id)
                .join(
                    AccountCategory, AccountCategory.category_id == Account.category_id
                )
                .where(
                    PostedLedgerLine.organization_id == org_id,
                    PostedLedgerLine.posting_date >= start,
                    PostedLedgerLine.posting_date <= end,
                    AccountCategory.organization_id == org_id,
                    Account.is_active.is_(True),
                )
            ).one()
            return _safe_decimal(result.revenue), _safe_decimal(result.expenses)

        current_revenue, current_expenses = get_period_totals(
            current_start, current_end
        )
        prev_revenue, prev_expenses = get_period_totals(prev_start, prev_end)

        current_income = current_revenue - current_expenses
        prev_income = prev_revenue - prev_expenses

        def calc_trend(current: Decimal, previous: Decimal) -> float | None:
            """Calculate percentage change. Returns None if no previous data."""
            if previous == 0:
                return None if current == 0 else 100.0
            change = ((current - previous) / abs(previous)) * 100
            return round(float(change), 1)

        return {
            "revenue_trend": calc_trend(current_revenue, prev_revenue),
            "income_trend": calc_trend(current_income, prev_income),
        }

    @staticmethod
    def get_invoice_status_breakdown(
        db: Session,
        organization_id: UUID,
        year: int | None = None,
    ) -> dict:
        """
        Get AR and AP posting status breakdown for status charts.

        Args:
            db: Database session
            organization_id: Organization scope

        Returns:
            Dict with ar_status and ap_status lists
        """
        org_id = coerce_uuid(organization_id)

        # AR posting status breakdown (GL journal status)
        ar_status_stmt = (
            select(
                JournalEntry.status,
                func.count(func.distinct(JournalEntry.journal_entry_id)).label("count"),
                func.coalesce(
                    func.sum(
                        PostedLedgerLine.credit_amount - PostedLedgerLine.debit_amount
                    ),
                    0,
                ).label("total"),
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == PostedLedgerLine.journal_entry_id,
            )
            .join(Account, Account.account_id == PostedLedgerLine.account_id)
            .join(AccountCategory, AccountCategory.category_id == Account.category_id)
            .where(
                PostedLedgerLine.organization_id == org_id,
                PostedLedgerLine.source_document_type == "INVOICE",
                AccountCategory.ifrs_category == IFRSCategory.REVENUE,
            )
            .group_by(JournalEntry.status)
        )
        ar_status_stmt = _apply_year_filter(
            ar_status_stmt, PostedLedgerLine.posting_date, year
        )
        ar_status = db.execute(ar_status_stmt).all()

        # AP posting status breakdown (GL journal status)
        ap_status_stmt = (
            select(
                JournalEntry.status,
                func.count(func.distinct(JournalEntry.journal_entry_id)).label("count"),
                func.coalesce(
                    func.sum(
                        PostedLedgerLine.debit_amount - PostedLedgerLine.credit_amount
                    ),
                    0,
                ).label("total"),
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == PostedLedgerLine.journal_entry_id,
            )
            .join(Account, Account.account_id == PostedLedgerLine.account_id)
            .join(AccountCategory, AccountCategory.category_id == Account.category_id)
            .where(
                PostedLedgerLine.organization_id == org_id,
                PostedLedgerLine.source_document_type == "SUPPLIER_INVOICE",
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
            )
            .group_by(JournalEntry.status)
        )
        ap_status_stmt = _apply_year_filter(
            ap_status_stmt, PostedLedgerLine.posting_date, year
        )
        ap_status = db.execute(ap_status_stmt).all()

        return {
            "ar_status": [
                {
                    "status": r.status.value
                    if hasattr(r.status, "value")
                    else r.status,
                    "count": r.count,
                    "total": float(_safe_decimal(r.total)),
                }
                for r in ar_status
            ],
            "ap_status": [
                {
                    "status": r.status.value
                    if hasattr(r.status, "value")
                    else r.status,
                    "count": r.count,
                    "total": float(_safe_decimal(r.total)),
                }
                for r in ap_status
            ],
        }


# Module-level instance
dashboard_service = DashboardService()
