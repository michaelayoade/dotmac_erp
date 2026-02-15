"""
RevenueComputer — produces revenue and sales pipeline metrics.

Metrics:
    revenue.monthly_total         Revenue recognized this calendar month
    revenue.ytd_total             Year-to-date revenue
    revenue.pipeline_value        Total value of open quotes (SENT/VIEWED)
    revenue.conversion_rate       Quote-to-SO conversion rate (last 90 days)
    revenue.average_invoice_value Average invoice value (last 90 days)
    revenue.open_so_value         Outstanding sales order value
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select

from app.services.analytics.base_computer import BaseComputer

logger = logging.getLogger(__name__)


class RevenueComputer(BaseComputer):
    """Compute revenue and sales pipeline KPIs for an organization."""

    METRIC_TYPES = [
        "revenue.monthly_total",
        "revenue.ytd_total",
        "revenue.pipeline_value",
        "revenue.conversion_rate",
        "revenue.average_invoice_value",
        "revenue.open_so_value",
    ]
    SOURCE_LABEL = "RevenueComputer"

    def compute_for_org(
        self,
        organization_id: UUID,
        snapshot_date: date,
    ) -> int:
        """Compute all revenue metrics for a single org. Returns count written."""
        from app.models.finance.ar.invoice import Invoice, InvoiceStatus
        from app.models.finance.ar.quote import Quote, QuoteStatus
        from app.models.finance.ar.sales_order import SalesOrder, SOStatus

        written = 0
        currency = self._get_org_currency(organization_id)

        # ── 1. Monthly revenue (invoices posted this month) ────────
        month_start = snapshot_date.replace(day=1)
        posted_statuses = (
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.PAID,
            InvoiceStatus.OVERDUE,
        )

        monthly_stmt = select(func.coalesce(func.sum(Invoice.total_amount), 0)).where(
            Invoice.organization_id == organization_id,
            Invoice.status.in_(posted_statuses),
            Invoice.invoice_date >= month_start,
            Invoice.invoice_date <= snapshot_date,
        )
        monthly_total = Decimal(str(self.db.scalar(monthly_stmt) or 0))

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="revenue.monthly_total",
            snapshot_date=snapshot_date,
            value_numeric=monthly_total,
            currency_code=currency,
        )
        written += 1

        # ── 2. YTD revenue ─────────────────────────────────────────
        year_start = snapshot_date.replace(month=1, day=1)
        ytd_stmt = select(func.coalesce(func.sum(Invoice.total_amount), 0)).where(
            Invoice.organization_id == organization_id,
            Invoice.status.in_(posted_statuses),
            Invoice.invoice_date >= year_start,
            Invoice.invoice_date <= snapshot_date,
        )
        ytd_total = Decimal(str(self.db.scalar(ytd_stmt) or 0))

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="revenue.ytd_total",
            snapshot_date=snapshot_date,
            value_numeric=ytd_total,
            currency_code=currency,
        )
        written += 1

        # ── 3. Pipeline value (open quotes) ────────────────────────
        pipeline_statuses = (QuoteStatus.SENT, QuoteStatus.VIEWED)
        pipeline_stmt = select(func.coalesce(func.sum(Quote.total_amount), 0)).where(
            Quote.organization_id == organization_id,
            Quote.status.in_(pipeline_statuses),
        )
        pipeline_value = Decimal(str(self.db.scalar(pipeline_stmt) or 0))

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="revenue.pipeline_value",
            snapshot_date=snapshot_date,
            value_numeric=pipeline_value,
            currency_code=currency,
        )
        written += 1

        # ── 4. Conversion rate (quotes → SO, last 90 days) ────────
        cutoff_90d = snapshot_date - timedelta(days=90)

        total_quotes_stmt = select(func.count(Quote.quote_id)).where(
            Quote.organization_id == organization_id,
            Quote.quote_date >= cutoff_90d,
            Quote.quote_date <= snapshot_date,
            Quote.status != QuoteStatus.DRAFT,
        )
        total_quotes = int(self.db.scalar(total_quotes_stmt) or 0)

        converted_quotes_stmt = select(func.count(Quote.quote_id)).where(
            Quote.organization_id == organization_id,
            Quote.quote_date >= cutoff_90d,
            Quote.quote_date <= snapshot_date,
            Quote.status == QuoteStatus.CONVERTED,
        )
        converted_quotes = int(self.db.scalar(converted_quotes_stmt) or 0)

        conversion_rate: Decimal | None = None
        if total_quotes > 0:
            conversion_rate = Decimal(
                str(round(converted_quotes / total_quotes * 100, 2))
            )

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="revenue.conversion_rate",
            snapshot_date=snapshot_date,
            value_numeric=conversion_rate,
        )
        written += 1

        # ── 5. Average invoice value (last 90 days) ───────────────
        avg_stmt = select(func.avg(Invoice.total_amount)).where(
            Invoice.organization_id == organization_id,
            Invoice.status.in_(posted_statuses),
            Invoice.invoice_date >= cutoff_90d,
            Invoice.invoice_date <= snapshot_date,
        )
        avg_raw = self.db.scalar(avg_stmt)
        avg_invoice = Decimal(str(round(float(avg_raw), 2))) if avg_raw else None

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="revenue.average_invoice_value",
            snapshot_date=snapshot_date,
            value_numeric=avg_invoice,
            currency_code=currency,
        )
        written += 1

        # ── 6. Open sales order value ──────────────────────────────
        open_so_statuses = (
            SOStatus.SUBMITTED,
            SOStatus.APPROVED,
            SOStatus.CONFIRMED,
            SOStatus.IN_PROGRESS,
        )
        open_so_stmt = select(
            func.coalesce(
                func.sum(SalesOrder.total_amount - SalesOrder.invoiced_amount),
                0,
            )
        ).where(
            SalesOrder.organization_id == organization_id,
            SalesOrder.status.in_(open_so_statuses),
        )
        open_so_value = Decimal(str(self.db.scalar(open_so_stmt) or 0))

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="revenue.open_so_value",
            snapshot_date=snapshot_date,
            value_numeric=open_so_value,
            currency_code=currency,
        )
        written += 1

        logger.info(
            "RevenueComputer wrote %d metrics for org %s on %s",
            written,
            organization_id,
            snapshot_date,
        )
        return written

    def _get_org_currency(self, organization_id: UUID) -> str:
        """Return the organization's functional currency code."""
        from app.models.finance.core_org.organization import Organization

        org = self.db.get(Organization, organization_id)
        if org and hasattr(org, "default_currency"):
            return str(org.default_currency)
        return "NGN"
