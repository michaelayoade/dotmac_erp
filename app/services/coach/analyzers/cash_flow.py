"""Cash-flow health analyzer (deterministic, no LLM required).

Computes DSO, DPO, CCC, and a 30-day cash forecast based on GL/AR/AP data.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.coach.insight import CoachInsight
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.core_org.organization import Organization

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_DAYS_IN_PERIOD = Decimal("90")


@dataclass(frozen=True)
class CashFlowHealthSummary:
    """Snapshot of cash-conversion-cycle metrics."""

    dso: Decimal  # Days Sales Outstanding
    dpo: Decimal  # Days Payable Outstanding
    ccc: Decimal  # Cash Conversion Cycle (DSO - DPO for service cos.)
    ar_outstanding: Decimal
    ap_outstanding: Decimal
    revenue_90d: Decimal
    cogs_90d: Decimal
    net_30d_forecast: Decimal  # estimated inflows minus outflows
    currency_code: str


def _severity_for_ccc(ccc: Decimal, dso: Decimal) -> str:
    if ccc > 60:
        return "WARNING"
    if dso > 45:
        return "ATTENTION"
    return "INFO"


class CashFlowAnalyzer:
    """Deterministic cash-flow health analyzer.

    Generates org-wide Finance insights covering DSO, DPO, CCC, and a
    simple 30-day cash-flow forecast.
    """

    _AR_OUTSTANDING = (
        InvoiceStatus.POSTED,
        InvoiceStatus.PARTIALLY_PAID,
        InvoiceStatus.OVERDUE,
        InvoiceStatus.DISPUTED,
    )
    _AP_OUTSTANDING = (
        SupplierInvoiceStatus.POSTED,
        SupplierInvoiceStatus.PARTIALLY_PAID,
    )

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # MetricStore fast-path
    # ------------------------------------------------------------------
    def _quick_check_from_store(self, organization_id: UUID) -> bool:
        """Return True if MetricStore shows zero AR *and* zero AP outstanding."""
        from app.services.coach.analyzers import metric_is_fresh

        ar_fresh, ar_val = metric_is_fresh(
            self.db, organization_id, "cash_flow.ar_overdue_total"
        )
        ap_fresh, ap_val = metric_is_fresh(
            self.db, organization_id, "cash_flow.ap_due_7d_total"
        )
        if ar_fresh and ap_fresh and (ar_val or _ZERO) <= 0 and (ap_val or _ZERO) <= 0:
            logger.debug("CashFlow fast-path: zero AR and AP outstanding, skipping")
            return True
        return False

    # ------------------------------------------------------------------
    # Core computations
    # ------------------------------------------------------------------
    def health_summary(self, organization_id: UUID) -> CashFlowHealthSummary:
        org = self.db.scalar(
            select(Organization).where(Organization.organization_id == organization_id)
        )
        currency_code = org.functional_currency_code if org else "NGN"

        cutoff_90d = date.today() - timedelta(days=90)

        # --- AR outstanding (balance_due > 0) ---
        ar_balance = Invoice.total_amount - Invoice.amount_paid
        ar_stmt = (
            select(func.coalesce(func.sum(ar_balance), 0))
            .where(
                Invoice.organization_id == organization_id,
                Invoice.status.in_(self._AR_OUTSTANDING),
                ar_balance > 0,
            )
            .select_from(Invoice)
        )
        ar_outstanding = Decimal(str(self.db.scalar(ar_stmt) or "0"))

        # --- Revenue last 90 days (paid invoices) ---
        rev_stmt = (
            select(func.coalesce(func.sum(Invoice.total_amount), 0))
            .where(
                Invoice.organization_id == organization_id,
                Invoice.status == InvoiceStatus.PAID,
                Invoice.invoice_date >= cutoff_90d,
            )
            .select_from(Invoice)
        )
        revenue_90d = Decimal(str(self.db.scalar(rev_stmt) or "0"))

        # --- AP outstanding ---
        ap_balance = SupplierInvoice.total_amount - SupplierInvoice.amount_paid
        ap_stmt = (
            select(func.coalesce(func.sum(ap_balance), 0))
            .where(
                SupplierInvoice.organization_id == organization_id,
                SupplierInvoice.status.in_(self._AP_OUTSTANDING),
                ap_balance > 0,
            )
            .select_from(SupplierInvoice)
        )
        ap_outstanding = Decimal(str(self.db.scalar(ap_stmt) or "0"))

        # --- COGS / purchases last 90 days ---
        cogs_stmt = (
            select(func.coalesce(func.sum(SupplierInvoice.total_amount), 0))
            .where(
                SupplierInvoice.organization_id == organization_id,
                SupplierInvoice.status == SupplierInvoiceStatus.PAID,
                SupplierInvoice.invoice_date >= cutoff_90d,
            )
            .select_from(SupplierInvoice)
        )
        cogs_90d = Decimal(str(self.db.scalar(cogs_stmt) or "0"))

        # --- DSO / DPO / CCC ---
        dso = (
            (ar_outstanding / revenue_90d * _DAYS_IN_PERIOD)
            if revenue_90d > 0
            else _ZERO
        )
        dpo = (ap_outstanding / cogs_90d * _DAYS_IN_PERIOD) if cogs_90d > 0 else _ZERO
        ccc = dso - dpo  # Simplified for service companies (no inventory days)

        # --- 30-day forecast: avg daily inflow - avg daily outflow ---
        daily_inflow = revenue_90d / 90 if revenue_90d > 0 else _ZERO
        daily_outflow = cogs_90d / 90 if cogs_90d > 0 else _ZERO
        net_30d_forecast = (daily_inflow - daily_outflow) * 30

        return CashFlowHealthSummary(
            dso=round(dso, 1),
            dpo=round(dpo, 1),
            ccc=round(ccc, 1),
            ar_outstanding=ar_outstanding,
            ap_outstanding=ap_outstanding,
            revenue_90d=revenue_90d,
            cogs_90d=cogs_90d,
            net_30d_forecast=round(net_30d_forecast, 2),
            currency_code=currency_code,
        )

    # ------------------------------------------------------------------
    # Insight generation
    # ------------------------------------------------------------------
    def generate_cash_flow_health_insight(
        self, organization_id: UUID
    ) -> CoachInsight | None:
        if self._quick_check_from_store(organization_id):
            return None

        summary = self.health_summary(organization_id)

        # Nothing to report if no activity
        if summary.revenue_90d <= 0 and summary.cogs_90d <= 0:
            return None

        severity = _severity_for_ccc(summary.ccc, summary.dso)
        title = "Cash conversion cycle health"

        summary_text = (
            f"DSO: {summary.dso} days, DPO: {summary.dpo} days, "
            f"CCC: {summary.ccc} days. "
            f"30-day net cash forecast: {summary.currency_code} {summary.net_30d_forecast:,.2f}."
        )
        coaching_action = (
            "A high CCC means cash is tied up longer. "
            "Reduce DSO by tightening collection terms and accelerating follow-ups. "
            "Extend DPO by negotiating longer supplier payment terms where possible."
        )

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="FINANCE",
            target_employee_id=None,
            category="CASH_FLOW",
            severity=severity,
            title=title,
            summary=summary_text,
            detail=None,
            coaching_action=coaching_action,
            confidence=0.85,
            data_sources={
                "ar.invoice": int(summary.ar_outstanding > 0),
                "ap.supplier_invoice": int(summary.ap_outstanding > 0),
            },
            evidence={
                "currency_code": summary.currency_code,
                "dso": str(summary.dso),
                "dpo": str(summary.dpo),
                "ccc": str(summary.ccc),
                "ar_outstanding": str(summary.ar_outstanding),
                "ap_outstanding": str(summary.ap_outstanding),
                "revenue_90d": str(summary.revenue_90d),
                "cogs_90d": str(summary.cogs_90d),
                "net_30d_forecast": str(summary.net_30d_forecast),
            },
            status="GENERATED",
            delivered_at=None,
            read_at=None,
            dismissed_at=None,
            feedback=None,
            valid_until=date.today() + timedelta(days=1),
            created_at=datetime.now(UTC),
        )

    def upsert_daily_org_insights(self, organization_id: UUID) -> int:
        today = date.today()
        self.db.execute(
            delete(CoachInsight).where(
                CoachInsight.organization_id == organization_id,
                CoachInsight.target_employee_id.is_(None),
                CoachInsight.category == "CASH_FLOW",
                CoachInsight.audience == "FINANCE",
                func.date(CoachInsight.created_at) == today,
                CoachInsight.title == "Cash conversion cycle health",
            )
        )

        insight = self.generate_cash_flow_health_insight(organization_id)
        if not insight:
            return 0
        self.db.add(insight)
        self.db.flush()
        return 1
