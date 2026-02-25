"""
Inventory valuation reconciliation service.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account
from app.models.finance.gl.account_balance import AccountBalance, BalanceType
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.inventory.item_wac_ledger import ItemWACLedger
from app.services.common import coerce_uuid


@dataclass(frozen=True)
class ValuationReconciliationResult:
    fiscal_period_id: UUID
    inventory_total: Decimal
    gl_total: Decimal
    difference: Decimal
    is_balanced: bool


class ValuationReconciliationService:
    """Compare WAC inventory totals against GL inventory balances."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def reconcile(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID | None = None,
    ) -> ValuationReconciliationResult:
        org_id = coerce_uuid(organization_id)
        period_id = fiscal_period_id or self._latest_period_id(org_id)
        if period_id is None:
            raise ValueError("No fiscal period found for organization.")

        inventory_total = Decimal(
            str(
                self.db.scalar(
                    select(func.coalesce(func.sum(ItemWACLedger.total_value), 0)).where(
                        ItemWACLedger.organization_id == org_id
                    )
                )
                or 0
            )
        )

        gl_total = Decimal(
            str(
                self.db.scalar(
                    select(func.coalesce(func.sum(AccountBalance.net_balance), 0))
                    .join(Account, Account.account_id == AccountBalance.account_id)
                    .where(
                        AccountBalance.organization_id == org_id,
                        AccountBalance.fiscal_period_id == period_id,
                        AccountBalance.balance_type == BalanceType.ACTUAL,
                        Account.subledger_type == "INVENTORY",
                    )
                )
                or 0
            )
        )

        difference = inventory_total - gl_total
        return ValuationReconciliationResult(
            fiscal_period_id=period_id,
            inventory_total=inventory_total,
            gl_total=gl_total,
            difference=difference,
            is_balanced=(difference == Decimal("0")),
        )

    def _latest_period_id(self, organization_id: UUID) -> UUID | None:
        stmt = (
            select(FiscalPeriod.fiscal_period_id)
            .where(FiscalPeriod.organization_id == organization_id)
            .order_by(FiscalPeriod.end_date.desc())
            .limit(1)
        )
        return self.db.scalar(stmt)
