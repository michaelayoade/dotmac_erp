"""
DashboardService - IFRS Dashboard data aggregation.

Provides aggregated statistics and data for the IFRS dashboard.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

from sqlalchemy import func, and_, extract, case
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.ifrs.ap.supplier import Supplier
from app.models.ifrs.ap.supplier_invoice import SupplierInvoice, SupplierInvoiceStatus
from app.models.ifrs.ar.customer import Customer
from app.models.ifrs.ar.invoice import Invoice, InvoiceStatus
from app.models.ifrs.gl.account import Account
from app.models.ifrs.gl.account_category import AccountCategory, IFRSCategory
from app.models.ifrs.gl.account_balance import AccountBalance
from app.models.ifrs.gl.journal_entry import JournalEntry
from app.models.ifrs.gl.fiscal_period import FiscalPeriod
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


@dataclass
class DashboardStats:
    """Dashboard statistics."""

    total_revenue: Decimal
    total_expenses: Decimal
    net_income: Decimal
    open_ar_invoices: int
    open_ap_invoices: int
    pending_ar_amount: Decimal
    pending_ap_amount: Decimal

    @property
    def open_invoices(self) -> int:
        return self.open_ar_invoices + self.open_ap_invoices

    @property
    def pending_amount(self) -> Decimal:
        return self.pending_ar_amount + self.pending_ap_amount


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
    def get_stats(
        db: Session,
        organization_id: UUID,
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

        # AR Revenue (total of all AR invoices)
        ar_total = db.query(
            func.coalesce(func.sum(Invoice.total_amount), 0)
        ).filter(
            Invoice.organization_id == org_id
        ).scalar() or Decimal("0")

        # AP Expenses (total of all AP invoices)
        ap_total = db.query(
            func.coalesce(func.sum(SupplierInvoice.total_amount), 0)
        ).filter(
            SupplierInvoice.organization_id == org_id
        ).scalar() or Decimal("0")

        # Open AR invoices count
        open_ar_statuses = [
            InvoiceStatus.DRAFT.value,
            InvoiceStatus.SUBMITTED.value,
            InvoiceStatus.APPROVED.value,
            InvoiceStatus.PARTIALLY_PAID.value,
        ]
        open_ar_invoices = db.query(
            func.count(Invoice.invoice_id)
        ).filter(
            Invoice.organization_id == org_id,
            Invoice.status.in_(open_ar_statuses)
        ).scalar() or 0

        # Pending AR amount (total - paid for open invoices)
        pending_ar_amount = db.query(
            func.coalesce(
                func.sum(Invoice.total_amount - Invoice.amount_paid),
                0
            )
        ).filter(
            Invoice.organization_id == org_id,
            Invoice.status.in_(open_ar_statuses)
        ).scalar() or Decimal("0")

        # Open AP invoices count
        open_ap_statuses = [
            SupplierInvoiceStatus.DRAFT.value,
            SupplierInvoiceStatus.SUBMITTED.value,
            SupplierInvoiceStatus.APPROVED.value,
            SupplierInvoiceStatus.PARTIALLY_PAID.value,
        ]
        open_ap_invoices = db.query(
            func.count(SupplierInvoice.invoice_id)
        ).filter(
            SupplierInvoice.organization_id == org_id,
            SupplierInvoice.status.in_(open_ap_statuses)
        ).scalar() or 0

        # Pending AP amount
        pending_ap_amount = db.query(
            func.coalesce(
                func.sum(SupplierInvoice.total_amount - SupplierInvoice.amount_paid),
                0
            )
        ).filter(
            SupplierInvoice.organization_id == org_id,
            SupplierInvoice.status.in_(open_ap_statuses)
        ).scalar() or Decimal("0")

        # Convert to Decimal safely
        revenue = _safe_decimal(ar_total)
        expenses = _safe_decimal(ap_total)
        pending_ar = _safe_decimal(pending_ar_amount)
        pending_ap = _safe_decimal(pending_ap_amount)

        return DashboardStats(
            total_revenue=revenue,
            total_expenses=expenses,
            net_income=revenue - expenses,
            open_ar_invoices=open_ar_invoices or 0,
            open_ap_invoices=open_ap_invoices or 0,
            pending_ar_amount=pending_ar,
            pending_ap_amount=pending_ap,
        )

    @staticmethod
    def get_recent_journals(
        db: Session,
        organization_id: UUID,
        limit: int = 10,
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

        journals = (
            db.query(JournalEntry)
            .filter(JournalEntry.organization_id == org_id)
            .order_by(JournalEntry.created_at.desc())
            .limit(limit)
            .all()
        )

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

                result.append(JournalEntrySummary(
                    entry_number=journal.journal_number or "",
                    entry_date=journal.entry_date.strftime("%Y-%m-%d") if journal.entry_date else "",
                    description=journal.description or "",
                    total_debit=total_debit,
                    status=journal.status.value if journal.status else "DRAFT",
                ))
            except (AttributeError, TypeError) as e:
                logger.warning(f"Error processing journal entry: {e}")
                continue

        return result

    @staticmethod
    def get_fiscal_periods(
        db: Session,
        organization_id: UUID,
        limit: int = 8,
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

        periods = (
            db.query(FiscalPeriod)
            .filter(FiscalPeriod.organization_id == org_id)
            .order_by(FiscalPeriod.start_date.desc())
            .limit(limit)
            .all()
        )

        result = []
        for period in periods:
            result.append(FiscalPeriodSummary(
                period_name=period.period_name,
                start_date=period.start_date.strftime("%Y-%m-%d") if period.start_date else "",
                end_date=period.end_date.strftime("%Y-%m-%d") if period.end_date else "",
                status=period.status.value if period.status else "FUTURE",
            ))

        return result

    @staticmethod
    def get_monthly_revenue_expenses(
        db: Session,
        organization_id: UUID,
        months: int = 12,
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
        start_date = today.replace(day=1) - timedelta(days=(months - 1) * 30)
        start_date = start_date.replace(day=1)

        # Revenue by month (AR invoices)
        ar_monthly = (
            db.query(
                extract("year", Invoice.invoice_date).label("year"),
                extract("month", Invoice.invoice_date).label("month"),
                func.coalesce(func.sum(Invoice.total_amount), 0).label("total"),
            )
            .filter(
                Invoice.organization_id == org_id,
                Invoice.invoice_date >= start_date,
            )
            .group_by(
                extract("year", Invoice.invoice_date),
                extract("month", Invoice.invoice_date),
            )
            .all()
        )

        # Expenses by month (AP invoices)
        ap_monthly = (
            db.query(
                extract("year", SupplierInvoice.invoice_date).label("year"),
                extract("month", SupplierInvoice.invoice_date).label("month"),
                func.coalesce(func.sum(SupplierInvoice.total_amount), 0).label("total"),
            )
            .filter(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.invoice_date >= start_date,
            )
            .group_by(
                extract("year", SupplierInvoice.invoice_date),
                extract("month", SupplierInvoice.invoice_date),
            )
            .all()
        )

        # Build result with all months
        ar_dict = {(int(r.year), int(r.month)): _safe_decimal(r.total) for r in ar_monthly}
        ap_dict = {(int(r.year), int(r.month)): _safe_decimal(r.total) for r in ap_monthly}

        result = []
        current = start_date
        while current <= today:
            key = (current.year, current.month)
            result.append({
                "month": current.strftime("%b %Y"),
                "month_short": current.strftime("%b"),
                "revenue": float(ar_dict.get(key, Decimal("0"))),
                "expenses": float(ap_dict.get(key, Decimal("0"))),
            })
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
        results = (
            db.query(
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
            .filter(
                AccountCategory.organization_id == org_id,
                Account.is_active == True,
            )
            .group_by(AccountCategory.ifrs_category)
            .all()
        )

        # Map to friendly names and colors
        category_config = {
            IFRSCategory.ASSETS: {"name": "Assets", "color": "#0d9488"},
            IFRSCategory.LIABILITIES: {"name": "Liabilities", "color": "#f97316"},
            IFRSCategory.EQUITY: {"name": "Equity", "color": "#8b5cf6"},
            IFRSCategory.REVENUE: {"name": "Revenue", "color": "#10b981"},
            IFRSCategory.EXPENSES: {"name": "Expenses", "color": "#ef4444"},
            IFRSCategory.OTHER_COMPREHENSIVE_INCOME: {"name": "OCI", "color": "#d97706"},
        }

        return [
            {
                "category": category_config.get(r.ifrs_category, {}).get("name", str(r.ifrs_category)),
                "value": abs(float(_safe_decimal(r.total))),
                "color": category_config.get(r.ifrs_category, {}).get("color", "#64748b"),
            }
            for r in results
            if _safe_decimal(r.total) != 0
        ]

    @staticmethod
    def get_top_customers(
        db: Session,
        organization_id: UUID,
        limit: int = 5,
    ) -> list[dict]:
        """
        Get top customers by invoice amount.

        Args:
            db: Database session
            organization_id: Organization scope
            limit: Maximum number of customers

        Returns:
            List of dicts with customer name and total amount
        """
        org_id = coerce_uuid(organization_id)

        results = (
            db.query(
                Customer.legal_name,
                Customer.trading_name,
                func.coalesce(func.sum(Invoice.total_amount), 0).label("total"),
            )
            .join(Invoice, Invoice.customer_id == Customer.customer_id, isouter=True)
            .filter(Customer.organization_id == org_id)
            .group_by(Customer.customer_id, Customer.legal_name, Customer.trading_name)
            .order_by(func.sum(Invoice.total_amount).desc().nullslast())
            .limit(limit)
            .all()
        )

        return [
            {
                "name": r.trading_name or r.legal_name,
                "value": float(_safe_decimal(r.total)),
            }
            for r in results
            if _safe_decimal(r.total) > 0
        ]

    @staticmethod
    def get_top_suppliers(
        db: Session,
        organization_id: UUID,
        limit: int = 5,
    ) -> list[dict]:
        """
        Get top suppliers by invoice amount.

        Args:
            db: Database session
            organization_id: Organization scope
            limit: Maximum number of suppliers

        Returns:
            List of dicts with supplier name and total amount
        """
        org_id = coerce_uuid(organization_id)

        results = (
            db.query(
                Supplier.legal_name,
                Supplier.trading_name,
                func.coalesce(func.sum(SupplierInvoice.total_amount), 0).label("total"),
            )
            .join(SupplierInvoice, SupplierInvoice.supplier_id == Supplier.supplier_id, isouter=True)
            .filter(Supplier.organization_id == org_id)
            .group_by(Supplier.supplier_id, Supplier.legal_name, Supplier.trading_name)
            .order_by(func.sum(SupplierInvoice.total_amount).desc().nullslast())
            .limit(limit)
            .all()
        )

        return [
            {
                "name": r.trading_name or r.legal_name,
                "value": float(_safe_decimal(r.total)),
            }
            for r in results
            if _safe_decimal(r.total) > 0
        ]

    @staticmethod
    def get_monthly_cash_flow(
        db: Session,
        organization_id: UUID,
        months: int = 6,
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
        start_date = today.replace(day=1) - timedelta(days=(months - 1) * 30)
        start_date = start_date.replace(day=1)

        # Cash inflows: Paid AR invoices
        paid_ar_statuses = [InvoiceStatus.PAID.value, InvoiceStatus.PARTIALLY_PAID.value]
        ar_monthly = (
            db.query(
                extract("year", Invoice.invoice_date).label("year"),
                extract("month", Invoice.invoice_date).label("month"),
                func.coalesce(func.sum(Invoice.amount_paid), 0).label("total"),
            )
            .filter(
                Invoice.organization_id == org_id,
                Invoice.invoice_date >= start_date,
                Invoice.status.in_(paid_ar_statuses),
            )
            .group_by(
                extract("year", Invoice.invoice_date),
                extract("month", Invoice.invoice_date),
            )
            .all()
        )

        # Cash outflows: Paid AP invoices
        paid_ap_statuses = [SupplierInvoiceStatus.PAID.value, SupplierInvoiceStatus.PARTIALLY_PAID.value]
        ap_monthly = (
            db.query(
                extract("year", SupplierInvoice.invoice_date).label("year"),
                extract("month", SupplierInvoice.invoice_date).label("month"),
                func.coalesce(func.sum(SupplierInvoice.amount_paid), 0).label("total"),
            )
            .filter(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.invoice_date >= start_date,
                SupplierInvoice.status.in_(paid_ap_statuses),
            )
            .group_by(
                extract("year", SupplierInvoice.invoice_date),
                extract("month", SupplierInvoice.invoice_date),
            )
            .all()
        )

        ar_dict = {(int(r.year), int(r.month)): _safe_decimal(r.total) for r in ar_monthly}
        ap_dict = {(int(r.year), int(r.month)): _safe_decimal(r.total) for r in ap_monthly}

        result = []
        current = start_date
        while current <= today:
            key = (current.year, current.month)
            inflow = float(ar_dict.get(key, Decimal("0")))
            outflow = float(ap_dict.get(key, Decimal("0")))
            result.append({
                "month": current.strftime("%b"),
                "inflow": inflow,
                "outflow": outflow,
                "net": inflow - outflow,
            })
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

        return result

    @staticmethod
    def get_invoice_status_breakdown(
        db: Session,
        organization_id: UUID,
    ) -> dict:
        """
        Get AR and AP invoice status breakdown for status charts.

        Args:
            db: Database session
            organization_id: Organization scope

        Returns:
            Dict with ar_status and ap_status lists
        """
        org_id = coerce_uuid(organization_id)

        # AR Invoice status breakdown
        ar_status = (
            db.query(
                Invoice.status,
                func.count(Invoice.invoice_id).label("count"),
                func.coalesce(func.sum(Invoice.total_amount), 0).label("total"),
            )
            .filter(Invoice.organization_id == org_id)
            .group_by(Invoice.status)
            .all()
        )

        # AP Invoice status breakdown
        ap_status = (
            db.query(
                SupplierInvoice.status,
                func.count(SupplierInvoice.invoice_id).label("count"),
                func.coalesce(func.sum(SupplierInvoice.total_amount), 0).label("total"),
            )
            .filter(SupplierInvoice.organization_id == org_id)
            .group_by(SupplierInvoice.status)
            .all()
        )

        status_colors = {
            "DRAFT": "#94a3b8",
            "SUBMITTED": "#60a5fa",
            "APPROVED": "#34d399",
            "PARTIALLY_PAID": "#fbbf24",
            "PAID": "#10b981",
            "VOIDED": "#ef4444",
            "CANCELLED": "#f87171",
        }

        return {
            "ar_status": [
                {
                    "status": r.status,
                    "count": r.count,
                    "total": float(_safe_decimal(r.total)),
                    "color": status_colors.get(r.status, "#64748b"),
                }
                for r in ar_status
            ],
            "ap_status": [
                {
                    "status": r.status,
                    "count": r.count,
                    "total": float(_safe_decimal(r.total)),
                    "color": status_colors.get(r.status, "#64748b"),
                }
                for r in ap_status
            ],
        }


# Module-level instance
dashboard_service = DashboardService()
