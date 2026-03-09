"""
EfficiencyComputer — produces operational efficiency metrics.

Metrics:
    efficiency.dso                          Days Sales Outstanding
    efficiency.dpo                          Days Payable Outstanding
    efficiency.ccc                          Cash Conversion Cycle (DSO - DPO)
    efficiency.reconciliation_freshness_days  Avg days since last bank recon
    efficiency.unreconciled_account_count   Active accounts not reconciled 14+ days
    efficiency.pending_expense_approvals    Count of pending expense claims
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

# Revenue/COGS account code prefixes (IFRS-standard)
_REVENUE_PREFIX = "4"  # Revenue accounts start with 4
_COGS_PREFIX = "5"  # Cost of Sales accounts start with 5
_RECON_STALE_DAYS = 14


class EfficiencyComputer(BaseComputer):
    """Compute operational efficiency KPIs for an organization."""

    METRIC_TYPES = [
        "efficiency.dso",
        "efficiency.dpo",
        "efficiency.ccc",
        "efficiency.reconciliation_freshness_days",
        "efficiency.unreconciled_account_count",
        "efficiency.pending_expense_approvals",
    ]
    SOURCE_LABEL = "EfficiencyComputer"

    def compute_for_org(
        self,
        organization_id: UUID,
        snapshot_date: date,
    ) -> int:
        """Compute all efficiency metrics for a single org. Returns count written."""
        written = 0

        currency = self._get_org_currency(organization_id)

        # ── 1. DSO / DPO / CCC ──────────────────────────────────
        dso = self._compute_dso(organization_id, snapshot_date)
        dpo = self._compute_dpo(organization_id, snapshot_date)
        ccc = (dso - dpo) if dso is not None and dpo is not None else None

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="efficiency.dso",
            snapshot_date=snapshot_date,
            value_numeric=dso,
            currency_code=currency,
        )
        written += 1

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="efficiency.dpo",
            snapshot_date=snapshot_date,
            value_numeric=dpo,
            currency_code=currency,
        )
        written += 1

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="efficiency.ccc",
            snapshot_date=snapshot_date,
            value_numeric=ccc,
            currency_code=currency,
        )
        written += 1

        # ── 2. Bank reconciliation freshness ─────────────────────
        freshness, stale_count = self._compute_recon_freshness(
            organization_id, snapshot_date
        )

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="efficiency.reconciliation_freshness_days",
            snapshot_date=snapshot_date,
            value_numeric=freshness,
        )
        written += 1

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="efficiency.unreconciled_account_count",
            snapshot_date=snapshot_date,
            value_numeric=stale_count,
        )
        written += 1

        # ── 3. Pending expense approvals ─────────────────────────
        pending = self._count_pending_expenses(organization_id)

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="efficiency.pending_expense_approvals",
            snapshot_date=snapshot_date,
            value_numeric=pending,
        )
        written += 1

        logger.info(
            "EfficiencyComputer wrote %d metrics for org %s on %s",
            written,
            organization_id,
            snapshot_date,
        )
        return written

    # ── DSO ──────────────────────────────────────────────────────

    def _compute_dso(
        self,
        organization_id: UUID,
        snapshot_date: date,
    ) -> Decimal | None:
        """Days Sales Outstanding = AR balance / (annual revenue / 365).

        Returns None if no revenue data available.
        """
        from app.models.finance.ar.invoice import Invoice, InvoiceStatus

        # AR balance: sum of outstanding invoices (including disputed)
        ar_stmt = select(
            func.coalesce(func.sum(Invoice.total_amount - Invoice.amount_paid), 0)
        ).where(
            Invoice.organization_id == organization_id,
            Invoice.status.in_(InvoiceStatus.outstanding() | {InvoiceStatus.DISPUTED}),
            (Invoice.total_amount - Invoice.amount_paid) > 0,
        )
        ar_balance = Decimal(str(self.db.scalar(ar_stmt) or 0))

        # Annual revenue: sum of revenue-account credits over last 365 days
        annual_revenue = self._get_annual_revenue(organization_id, snapshot_date)
        if not annual_revenue or annual_revenue <= 0:
            return None

        daily_revenue = annual_revenue / 365
        return ar_balance / daily_revenue

    # ── DPO ──────────────────────────────────────────────────────

    def _compute_dpo(
        self,
        organization_id: UUID,
        snapshot_date: date,
    ) -> Decimal | None:
        """Days Payable Outstanding = AP balance / (annual COGS / 365).

        Returns None if no COGS data available.
        """
        from app.models.finance.ap.supplier_invoice import (
            SupplierInvoice,
            SupplierInvoiceStatus,
        )

        outstanding_statuses = (
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
            SupplierInvoice.status.in_(outstanding_statuses),
            (SupplierInvoice.total_amount - SupplierInvoice.amount_paid) > 0,
        )
        ap_balance = Decimal(str(self.db.scalar(ap_stmt) or 0))

        annual_cogs = self._get_annual_cogs(organization_id, snapshot_date)
        if not annual_cogs or annual_cogs <= 0:
            return None

        daily_cogs = annual_cogs / 365
        return ap_balance / daily_cogs

    # ── Bank reconciliation freshness ────────────────────────────

    def _compute_recon_freshness(
        self,
        organization_id: UUID,
        snapshot_date: date,
    ) -> tuple[Decimal | None, int]:
        """Compute average days since last reconciliation and count of stale accounts.

        Returns (avg_days, stale_count).
        """
        from app.models.finance.banking.bank_account import BankAccount
        from app.models.finance.banking.bank_reconciliation import (
            BankReconciliation,
            ReconciliationStatus,
        )

        # Get active bank accounts with their latest approved recon date
        latest_recon_sq = (
            select(
                BankReconciliation.bank_account_id,
                func.max(BankReconciliation.reconciliation_date).label("last_recon"),
            )
            .where(BankReconciliation.status == ReconciliationStatus.approved)
            .group_by(BankReconciliation.bank_account_id)
            .subquery()
        )

        stmt = (
            select(
                BankAccount.bank_account_id,
                latest_recon_sq.c.last_recon,
            )
            .outerjoin(
                latest_recon_sq,
                BankAccount.bank_account_id == latest_recon_sq.c.bank_account_id,
            )
            .where(
                BankAccount.organization_id == organization_id,
                BankAccount.status == "active",
            )
        )

        rows = self.db.execute(stmt).all()
        if not rows:
            return None, 0

        stale_cutoff = snapshot_date - timedelta(days=_RECON_STALE_DAYS)
        total_days = Decimal("0")
        stale_count = 0
        count = 0

        for _account_id, last_recon in rows:
            count += 1
            if last_recon is None:
                # Never reconciled — count as stale using a high day count
                total_days += Decimal("365")
                stale_count += 1
            else:
                recon_date = (
                    last_recon if isinstance(last_recon, date) else last_recon.date()
                )
                days_since = (snapshot_date - recon_date).days
                total_days += Decimal(str(max(days_since, 0)))
                if recon_date < stale_cutoff:
                    stale_count += 1

        avg_days = total_days / count if count > 0 else None
        return avg_days, stale_count

    # ── Pending expense approvals ────────────────────────────────

    def _count_pending_expenses(self, organization_id: UUID) -> int:
        """Count expense claims awaiting approval."""
        from app.models.expense.expense_claim import (
            ExpenseClaim,
            ExpenseClaimStatus,
        )

        stmt = select(func.count(ExpenseClaim.claim_id)).where(
            ExpenseClaim.organization_id == organization_id,
            ExpenseClaim.status == ExpenseClaimStatus.PENDING_APPROVAL,
        )
        return int(self.db.scalar(stmt) or 0)

    # ── Revenue / COGS helpers ───────────────────────────────────

    def _get_annual_revenue(
        self,
        organization_id: UUID,
        snapshot_date: date,
    ) -> Decimal:
        """Sum of credit amounts on revenue accounts over the last 365 days."""
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.posted_ledger_line import PostedLedgerLine

        cutoff = snapshot_date - timedelta(days=365)

        revenue_acct_ids = select(Account.account_id).where(
            Account.organization_id == organization_id,
            Account.account_code.like(f"{_REVENUE_PREFIX}%"),
            Account.is_active == True,  # noqa: E712
        )

        stmt = select(func.coalesce(func.sum(PostedLedgerLine.credit_amount), 0)).where(
            PostedLedgerLine.organization_id == organization_id,
            PostedLedgerLine.account_id.in_(revenue_acct_ids),
            PostedLedgerLine.posting_date >= cutoff,
            PostedLedgerLine.posting_date <= snapshot_date,
        )
        return Decimal(str(self.db.scalar(stmt) or 0))

    def _get_annual_cogs(
        self,
        organization_id: UUID,
        snapshot_date: date,
    ) -> Decimal:
        """Sum of debit amounts on COGS accounts over the last 365 days."""
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.posted_ledger_line import PostedLedgerLine

        cutoff = snapshot_date - timedelta(days=365)

        cogs_acct_ids = select(Account.account_id).where(
            Account.organization_id == organization_id,
            Account.account_code.like(f"{_COGS_PREFIX}%"),
            Account.is_active == True,  # noqa: E712
        )

        stmt = select(func.coalesce(func.sum(PostedLedgerLine.debit_amount), 0)).where(
            PostedLedgerLine.organization_id == organization_id,
            PostedLedgerLine.account_id.in_(cogs_acct_ids),
            PostedLedgerLine.posting_date >= cutoff,
            PostedLedgerLine.posting_date <= snapshot_date,
        )
        return Decimal(str(self.db.scalar(stmt) or 0))

    def _get_org_currency(self, organization_id: UUID) -> str:
        """Return the organization's functional currency code."""
        from app.models.finance.core_org.organization import Organization

        org = self.db.get(Organization, organization_id)
        if org and hasattr(org, "default_currency"):
            return str(org.default_currency)
        return settings.default_functional_currency_code
