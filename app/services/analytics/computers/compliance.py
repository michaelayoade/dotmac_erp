"""
ComplianceComputer — produces tax and regulatory compliance metrics.

Metrics:
    compliance.overdue_tax_filings     Count of tax periods past due date, not filed
    compliance.upcoming_tax_deadlines  Count of tax periods due within 30 days
    compliance.open_fiscal_periods     Count of open fiscal periods
    compliance.total_tax_payable       Sum of net_tax_payable on unfiled returns
    compliance.filed_returns_ytd       Count of tax returns filed this year
    compliance.overdue_fiscal_periods  Fiscal periods past end_date still open
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select

from app.config import settings
from app.services.analytics.base_computer import BaseComputer

logger = logging.getLogger(__name__)


class ComplianceComputer(BaseComputer):
    """Compute tax and regulatory compliance KPIs for an organization."""

    METRIC_TYPES = [
        "compliance.overdue_tax_filings",
        "compliance.upcoming_tax_deadlines",
        "compliance.open_fiscal_periods",
        "compliance.total_tax_payable",
        "compliance.filed_returns_ytd",
        "compliance.overdue_fiscal_periods",
    ]
    SOURCE_LABEL = "ComplianceComputer"

    def compute_for_org(
        self,
        organization_id: UUID,
        snapshot_date: date,
    ) -> int:
        """Compute all compliance metrics for a single org. Returns count written."""
        from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
        from app.models.finance.tax.tax_period import TaxPeriod, TaxPeriodStatus
        from app.models.finance.tax.tax_return import TaxReturn, TaxReturnStatus

        written = 0
        currency = self._get_org_currency(organization_id)

        # ── 1. Overdue tax filings ─────────────────────────────────
        # Tax periods where due_date < today and status is still OPEN
        overdue_stmt = select(func.count(TaxPeriod.period_id)).where(
            TaxPeriod.organization_id == organization_id,
            TaxPeriod.status == TaxPeriodStatus.OPEN,
            TaxPeriod.due_date < snapshot_date,
        )
        overdue_filings = int(self.db.scalar(overdue_stmt) or 0)

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="compliance.overdue_tax_filings",
            snapshot_date=snapshot_date,
            value_numeric=overdue_filings,
        )
        written += 1

        # ── 2. Upcoming tax deadlines (due within 30 days) ────────
        deadline_cutoff = snapshot_date + timedelta(days=30)
        upcoming_stmt = select(func.count(TaxPeriod.period_id)).where(
            TaxPeriod.organization_id == organization_id,
            TaxPeriod.status == TaxPeriodStatus.OPEN,
            TaxPeriod.due_date >= snapshot_date,
            TaxPeriod.due_date <= deadline_cutoff,
        )
        upcoming_deadlines = int(self.db.scalar(upcoming_stmt) or 0)

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="compliance.upcoming_tax_deadlines",
            snapshot_date=snapshot_date,
            value_numeric=upcoming_deadlines,
        )
        written += 1

        # ── 3. Open fiscal periods ─────────────────────────────────
        open_fp_stmt = select(func.count(FiscalPeriod.fiscal_period_id)).where(
            FiscalPeriod.organization_id == organization_id,
            FiscalPeriod.status.in_(PeriodStatus.accepts_postings()),
        )
        open_periods = int(self.db.scalar(open_fp_stmt) or 0)

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="compliance.open_fiscal_periods",
            snapshot_date=snapshot_date,
            value_numeric=open_periods,
        )
        written += 1

        # ── 4. Total tax payable (unfiled returns) ─────────────────
        unfiled_statuses = (
            TaxReturnStatus.DRAFT,
            TaxReturnStatus.PREPARED,
            TaxReturnStatus.REVIEWED,
        )
        payable_stmt = select(
            func.coalesce(func.sum(TaxReturn.net_tax_payable), 0)
        ).where(
            TaxReturn.organization_id == organization_id,
            TaxReturn.status.in_(unfiled_statuses),
        )
        tax_payable = Decimal(str(self.db.scalar(payable_stmt) or 0))

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="compliance.total_tax_payable",
            snapshot_date=snapshot_date,
            value_numeric=tax_payable,
            currency_code=currency,
        )
        written += 1

        # ── 5. Filed returns YTD ───────────────────────────────────
        year_start = snapshot_date.replace(month=1, day=1)
        filed_stmt = select(func.count(TaxReturn.return_id)).where(
            TaxReturn.organization_id == organization_id,
            TaxReturn.status.in_((TaxReturnStatus.FILED, TaxReturnStatus.AMENDED)),
            TaxReturn.filed_date >= year_start,
            TaxReturn.filed_date <= snapshot_date,
        )
        filed_count = int(self.db.scalar(filed_stmt) or 0)

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="compliance.filed_returns_ytd",
            snapshot_date=snapshot_date,
            value_numeric=filed_count,
        )
        written += 1

        # ── 6. Overdue fiscal periods ──────────────────────────────
        # Open/Reopened periods past their end_date
        overdue_fp_stmt = select(func.count(FiscalPeriod.fiscal_period_id)).where(
            FiscalPeriod.organization_id == organization_id,
            FiscalPeriod.status.in_(PeriodStatus.accepts_postings()),
            FiscalPeriod.end_date < snapshot_date,
        )
        overdue_periods = int(self.db.scalar(overdue_fp_stmt) or 0)

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="compliance.overdue_fiscal_periods",
            snapshot_date=snapshot_date,
            value_numeric=overdue_periods,
        )
        written += 1

        logger.info(
            "ComplianceComputer wrote %d metrics for org %s on %s",
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
        return settings.default_functional_currency_code
