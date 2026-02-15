"""
CashFlowComputer — produces cash position and flow metrics.

Metrics:
    cash_flow.net_position      Total cash/bank balance (from AccountBalance)
    cash_flow.inflow_30d        Cash inflows last 30 days (PostedLedgerLine debits)
    cash_flow.outflow_30d       Cash outflows last 30 days (PostedLedgerLine credits)
    cash_flow.net_flow_30d      Net = inflow - outflow
    cash_flow.monthly_summary   JSON: {inflow, outflow, net, month}
    cash_flow.ar_overdue_total  Total overdue AR balance
    cash_flow.ap_due_7d_total   AP due within 7 days
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select

from app.services.analytics.base_computer import BaseComputer

logger = logging.getLogger(__name__)


class CashFlowComputer(BaseComputer):
    """Compute cash-flow-related KPIs for an organization."""

    METRIC_TYPES = [
        "cash_flow.net_position",
        "cash_flow.inflow_30d",
        "cash_flow.outflow_30d",
        "cash_flow.net_flow_30d",
        "cash_flow.monthly_summary",
        "cash_flow.ar_overdue_total",
        "cash_flow.ap_due_7d_total",
    ]
    SOURCE_LABEL = "CashFlowComputer"

    def compute_for_org(
        self,
        organization_id: UUID,
        snapshot_date: date,
    ) -> int:
        """Compute all cash flow metrics for a single org. Returns count written."""
        from app.models.finance.ap.supplier_invoice import (
            SupplierInvoice,
            SupplierInvoiceStatus,
        )
        from app.models.finance.ar.invoice import Invoice, InvoiceStatus
        from app.models.finance.gl.account_balance import AccountBalance, BalanceType
        from app.models.finance.gl.posted_ledger_line import PostedLedgerLine

        written = 0

        # Fetch org default currency for labeling
        currency = self._get_org_currency(organization_id)

        # ── 1. Net cash position ─────────────────────────────────
        # Sum closing balances on cash-equivalent accounts (ACTUAL type)
        cash_account_ids = self._get_cash_account_ids(organization_id)

        net_position = Decimal("0")
        if cash_account_ids:
            # For each cash account, get the latest AccountBalance row
            # and sum the net_balance (debit - credit)
            stmt = select(func.coalesce(func.sum(AccountBalance.net_balance), 0)).where(
                AccountBalance.organization_id == organization_id,
                AccountBalance.account_id.in_(cash_account_ids),
                AccountBalance.balance_type == BalanceType.ACTUAL,
            )
            result = self.db.scalar(stmt)
            net_position = Decimal(str(result)) if result else Decimal("0")

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="cash_flow.net_position",
            snapshot_date=snapshot_date,
            value_numeric=net_position,
            currency_code=currency,
        )
        written += 1

        # ── 2. Cash inflows / outflows last 30 days ──────────────
        cutoff_30d = snapshot_date - timedelta(days=30)

        inflow_30d = Decimal("0")
        outflow_30d = Decimal("0")

        if cash_account_ids:
            flow_stmt = select(
                func.coalesce(func.sum(PostedLedgerLine.debit_amount), 0).label(
                    "total_debit"
                ),
                func.coalesce(func.sum(PostedLedgerLine.credit_amount), 0).label(
                    "total_credit"
                ),
            ).where(
                PostedLedgerLine.organization_id == organization_id,
                PostedLedgerLine.account_id.in_(cash_account_ids),
                PostedLedgerLine.posting_date >= cutoff_30d,
                PostedLedgerLine.posting_date <= snapshot_date,
            )
            flow_row = self.db.execute(flow_stmt).one_or_none()
            if flow_row:
                inflow_30d = Decimal(str(flow_row[0]))
                outflow_30d = Decimal(str(flow_row[1]))

        net_flow = inflow_30d - outflow_30d

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="cash_flow.inflow_30d",
            snapshot_date=snapshot_date,
            value_numeric=inflow_30d,
            currency_code=currency,
        )
        written += 1

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="cash_flow.outflow_30d",
            snapshot_date=snapshot_date,
            value_numeric=outflow_30d,
            currency_code=currency,
        )
        written += 1

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="cash_flow.net_flow_30d",
            snapshot_date=snapshot_date,
            value_numeric=net_flow,
            currency_code=currency,
        )
        written += 1

        # ── 3. Monthly summary (JSON) ────────────────────────────
        month_start = snapshot_date.replace(day=1)
        month_inflow = Decimal("0")
        month_outflow = Decimal("0")

        if cash_account_ids:
            month_stmt = select(
                func.coalesce(func.sum(PostedLedgerLine.debit_amount), 0),
                func.coalesce(func.sum(PostedLedgerLine.credit_amount), 0),
            ).where(
                PostedLedgerLine.organization_id == organization_id,
                PostedLedgerLine.account_id.in_(cash_account_ids),
                PostedLedgerLine.posting_date >= month_start,
                PostedLedgerLine.posting_date <= snapshot_date,
            )
            month_row = self.db.execute(month_stmt).one_or_none()
            if month_row:
                month_inflow = Decimal(str(month_row[0]))
                month_outflow = Decimal(str(month_row[1]))

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="cash_flow.monthly_summary",
            snapshot_date=snapshot_date,
            value_json={
                "inflow": str(month_inflow),
                "outflow": str(month_outflow),
                "net": str(month_inflow - month_outflow),
                "month": snapshot_date.strftime("%Y-%m"),
            },
            currency_code=currency,
        )
        written += 1

        # ── 4. AR overdue total ──────────────────────────────────
        ar_overdue_statuses = (
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
            InvoiceStatus.DISPUTED,
        )
        ar_stmt = select(
            func.coalesce(func.sum(Invoice.total_amount - Invoice.amount_paid), 0)
        ).where(
            Invoice.organization_id == organization_id,
            Invoice.status.in_(ar_overdue_statuses),
            Invoice.due_date < snapshot_date,
            (Invoice.total_amount - Invoice.amount_paid) > 0,
        )
        ar_overdue = Decimal(str(self.db.scalar(ar_stmt) or 0))

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="cash_flow.ar_overdue_total",
            snapshot_date=snapshot_date,
            value_numeric=ar_overdue,
            currency_code=currency,
        )
        written += 1

        # ── 5. AP due within 7 days ──────────────────────────────
        ap_due_cutoff = snapshot_date + timedelta(days=7)
        ap_active_statuses = (
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
            SupplierInvoiceStatus.APPROVED,
            SupplierInvoiceStatus.DISPUTED,
            SupplierInvoiceStatus.ON_HOLD,
        )
        ap_stmt = select(
            func.coalesce(
                func.sum(SupplierInvoice.total_amount - SupplierInvoice.amount_paid),
                0,
            )
        ).where(
            SupplierInvoice.organization_id == organization_id,
            SupplierInvoice.status.in_(ap_active_statuses),
            SupplierInvoice.due_date <= ap_due_cutoff,
            (SupplierInvoice.total_amount - SupplierInvoice.amount_paid) > 0,
        )
        ap_due_7d = Decimal(str(self.db.scalar(ap_stmt) or 0))

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="cash_flow.ap_due_7d_total",
            snapshot_date=snapshot_date,
            value_numeric=ap_due_7d,
            currency_code=currency,
        )
        written += 1

        logger.info(
            "CashFlowComputer wrote %d metrics for org %s on %s",
            written,
            organization_id,
            snapshot_date,
        )
        return written

    # ── helpers ───────────────────────────────────────────────────

    def _get_cash_account_ids(self, organization_id: UUID) -> list[UUID]:
        """Return account IDs flagged as cash equivalents for this org."""
        from app.models.finance.gl.account import Account

        stmt = select(Account.account_id).where(
            Account.organization_id == organization_id,
            Account.is_cash_equivalent == True,  # noqa: E712
            Account.is_active == True,  # noqa: E712
        )
        return list(self.db.scalars(stmt).all())

    def _get_org_currency(self, organization_id: UUID) -> str:
        """Return the organization's functional currency code."""
        from app.models.finance.core_org.organization import Organization

        org = self.db.get(Organization, organization_id)
        if org and hasattr(org, "default_currency"):
            return str(org.default_currency)
        return "NGN"
