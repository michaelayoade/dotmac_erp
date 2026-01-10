"""
DashboardService - IFRS Dashboard data aggregation.

Provides aggregated statistics and data for the IFRS dashboard.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

from sqlalchemy import func, and_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.ifrs.ap.supplier_invoice import SupplierInvoice, SupplierInvoiceStatus
from app.models.ifrs.ar.invoice import Invoice, InvoiceStatus
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


# Module-level instance
dashboard_service = DashboardService()
