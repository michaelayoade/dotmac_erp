"""
DashboardMetricsService — MetricStore-backed dashboard statistics.

Reads pre-computed metrics from OrgMetricSnapshot for fast dashboard rendering.
Falls back to live DashboardService queries when metrics are stale (>24h) or
when the user selects a historical year filter.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.analytics.metric_store import MetricStore, MetricValue

logger = logging.getLogger(__name__)

# Metrics consumed from pre-computed snapshots.
_CASH_FLOW_METRICS = [
    "cash_flow.net_position",
    "cash_flow.inflow_30d",
    "cash_flow.outflow_30d",
    "cash_flow.net_flow_30d",
    "cash_flow.ar_overdue_total",
    "cash_flow.ap_due_7d_total",
    "cash_flow.monthly_summary",
]
_EFFICIENCY_METRICS = [
    "efficiency.dso",
    "efficiency.dpo",
    "efficiency.ccc",
    "efficiency.reconciliation_freshness_days",
    "efficiency.unreconciled_account_count",
    "efficiency.pending_expense_approvals",
]
_REVENUE_METRICS = [
    "revenue.monthly_total",
    "revenue.ytd_total",
    "revenue.pipeline_value",
    "revenue.conversion_rate",
    "revenue.average_invoice_value",
    "revenue.open_so_value",
]
_COMPLIANCE_METRICS = [
    "compliance.overdue_tax_filings",
    "compliance.upcoming_tax_deadlines",
    "compliance.open_fiscal_periods",
    "compliance.total_tax_payable",
    "compliance.filed_returns_ytd",
    "compliance.overdue_fiscal_periods",
]
_WORKFORCE_METRICS = [
    "workforce.active_headcount",
    "workforce.turnover_30d",
    "workforce.leave_utilization_30d",
    "workforce.attendance_rate_30d",
    "workforce.pending_leave_approvals",
    "workforce.department_distribution",
]
_SUPPLY_CHAIN_METRICS = [
    "supply_chain.total_inventory_value",
    "supply_chain.low_stock_item_count",
    "supply_chain.stockout_count",
    "supply_chain.transaction_volume_30d",
    "supply_chain.receipt_value_30d",
    "supply_chain.issue_value_30d",
]

ALL_DASHBOARD_METRICS = (
    _CASH_FLOW_METRICS
    + _EFFICIENCY_METRICS
    + _REVENUE_METRICS
    + _COMPLIANCE_METRICS
    + _WORKFORCE_METRICS
    + _SUPPLY_CHAIN_METRICS
)

_MAX_STALE_HOURS = 24


def _numeric(mv: MetricValue | None) -> Decimal | None:
    """Extract numeric value from a MetricValue, or None."""
    return mv.value_numeric if mv else None


def _numeric_or_zero(mv: MetricValue | None) -> Decimal:
    """Extract numeric value or default to zero."""
    return mv.value_numeric if mv and mv.value_numeric is not None else Decimal("0")


def _json_val(mv: MetricValue | None) -> dict[str, Any] | None:
    """Extract JSON value from a MetricValue, or None."""
    return mv.value_json if mv else None


class DashboardMetricsService:
    """Read pre-computed dashboard metrics from MetricStore.

    Usage::

        svc = DashboardMetricsService(db)
        snapshot = svc.get_org_snapshot(org_id)
        if snapshot is not None:
            # Use pre-computed values
            cash_inflow = snapshot["cash_flow"]["inflow_30d"]
        else:
            # Metrics stale — fall back to live DashboardService
            ...
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self._store = MetricStore(db)

    def get_org_snapshot(
        self,
        organization_id: UUID,
        *,
        max_age_hours: int = _MAX_STALE_HOURS,
    ) -> dict[str, Any] | None:
        """Fetch all dashboard metrics for an org.

        Returns a structured dict grouped by domain, or ``None`` if
        no fresh metrics are available (caller should fall back to live queries).
        """
        metrics = self._store.get_latest(organization_id, ALL_DASHBOARD_METRICS)

        if not metrics:
            logger.debug("No pre-computed metrics for org %s", organization_id)
            return None

        # Check freshness — use the most recent computed_at across all returned metrics.
        if not self._any_fresh(metrics, max_age_hours):
            logger.debug(
                "All metrics stale (>%dh) for org %s", max_age_hours, organization_id
            )
            return None

        return self._structure(metrics)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _any_fresh(
        metrics: dict[str, MetricValue],
        max_age_hours: int,
    ) -> bool:
        """Return True if at least one metric was computed within the staleness window."""
        cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
        for mv in metrics.values():
            computed = mv.computed_at
            if computed is None:
                continue
            if hasattr(computed, "tzinfo") and computed.tzinfo is None:
                computed = computed.replace(tzinfo=UTC)
            if computed >= cutoff:
                return True
        return False

    @staticmethod
    def _structure(metrics: dict[str, MetricValue]) -> dict[str, Any]:
        """Group flat metric dict into domain-keyed structure."""
        g = metrics.get  # shorthand

        return {
            "cash_flow": {
                "net_position": _numeric_or_zero(g("cash_flow.net_position")),
                "inflow_30d": _numeric_or_zero(g("cash_flow.inflow_30d")),
                "outflow_30d": _numeric_or_zero(g("cash_flow.outflow_30d")),
                "net_flow_30d": _numeric_or_zero(g("cash_flow.net_flow_30d")),
                "ar_overdue_total": _numeric_or_zero(g("cash_flow.ar_overdue_total")),
                "ap_due_7d_total": _numeric_or_zero(g("cash_flow.ap_due_7d_total")),
                "monthly_summary": _json_val(g("cash_flow.monthly_summary")),
            },
            "efficiency": {
                "dso": _numeric(g("efficiency.dso")),
                "dpo": _numeric(g("efficiency.dpo")),
                "ccc": _numeric(g("efficiency.ccc")),
                "reconciliation_freshness_days": _numeric(
                    g("efficiency.reconciliation_freshness_days")
                ),
                "unreconciled_account_count": _numeric_or_zero(
                    g("efficiency.unreconciled_account_count")
                ),
                "pending_expense_approvals": _numeric_or_zero(
                    g("efficiency.pending_expense_approvals")
                ),
            },
            "revenue": {
                "monthly_total": _numeric_or_zero(g("revenue.monthly_total")),
                "ytd_total": _numeric_or_zero(g("revenue.ytd_total")),
                "pipeline_value": _numeric_or_zero(g("revenue.pipeline_value")),
                "conversion_rate": _numeric(g("revenue.conversion_rate")),
                "average_invoice_value": _numeric(g("revenue.average_invoice_value")),
                "open_so_value": _numeric_or_zero(g("revenue.open_so_value")),
            },
            "compliance": {
                "overdue_tax_filings": _numeric_or_zero(
                    g("compliance.overdue_tax_filings")
                ),
                "upcoming_tax_deadlines": _numeric_or_zero(
                    g("compliance.upcoming_tax_deadlines")
                ),
                "open_fiscal_periods": _numeric_or_zero(
                    g("compliance.open_fiscal_periods")
                ),
                "total_tax_payable": _numeric_or_zero(
                    g("compliance.total_tax_payable")
                ),
                "filed_returns_ytd": _numeric_or_zero(
                    g("compliance.filed_returns_ytd")
                ),
                "overdue_fiscal_periods": _numeric_or_zero(
                    g("compliance.overdue_fiscal_periods")
                ),
            },
            "workforce": {
                "active_headcount": _numeric_or_zero(g("workforce.active_headcount")),
                "turnover_30d": _numeric_or_zero(g("workforce.turnover_30d")),
                "leave_utilization_30d": _numeric(g("workforce.leave_utilization_30d")),
                "attendance_rate_30d": _numeric(g("workforce.attendance_rate_30d")),
                "pending_leave_approvals": _numeric_or_zero(
                    g("workforce.pending_leave_approvals")
                ),
                "department_distribution": _json_val(
                    g("workforce.department_distribution")
                ),
            },
            "supply_chain": {
                "total_inventory_value": _numeric_or_zero(
                    g("supply_chain.total_inventory_value")
                ),
                "low_stock_item_count": _numeric_or_zero(
                    g("supply_chain.low_stock_item_count")
                ),
                "stockout_count": _numeric_or_zero(g("supply_chain.stockout_count")),
                "transaction_volume_30d": _numeric_or_zero(
                    g("supply_chain.transaction_volume_30d")
                ),
                "receipt_value_30d": _numeric_or_zero(
                    g("supply_chain.receipt_value_30d")
                ),
                "issue_value_30d": _numeric_or_zero(g("supply_chain.issue_value_30d")),
            },
        }
