"""
Budget Comparison Service - IPSAS 24 reporting.

Generates budget vs actual comparison reports as required by IPSAS 24.
"""

import logging
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.ipsas.appropriation import Appropriation
from app.models.finance.ipsas.commitment import Commitment
from app.models.finance.ipsas.enums import AppropriationStatus, CommitmentStatus
from app.models.finance.ipsas.fund import Fund
from app.schemas.finance.ipsas import BudgetComparisonResponse, BudgetLineItem

logger = logging.getLogger(__name__)


class BudgetComparisonService:
    """Service for IPSAS 24 budget vs actual comparison."""

    def __init__(self, db: Session):
        self.db = db

    def generate_comparison(
        self,
        organization_id: UUID,
        fiscal_year_id: UUID,
        *,
        fund_id: Optional[UUID] = None,
    ) -> BudgetComparisonResponse:
        """
        Generate IPSAS 24 Budget vs Actual comparison.

        For each appropriation, calculates:
        - Original budget (approved_amount)
        - Revised budget (revised_amount, after virements)
        - Committed, obligated, expended amounts from commitments
        - Available balance and utilization percentage
        """
        # Get appropriations for the fiscal year
        approp_stmt = select(Appropriation).where(
            Appropriation.organization_id == organization_id,
            Appropriation.fiscal_year_id == fiscal_year_id,
            Appropriation.status.in_(
                [
                    AppropriationStatus.APPROVED,
                    AppropriationStatus.ACTIVE,
                    AppropriationStatus.CLOSED,
                ]
            ),
        )
        if fund_id:
            approp_stmt = approp_stmt.where(Appropriation.fund_id == fund_id)
        approp_stmt = approp_stmt.order_by(Appropriation.appropriation_code)

        appropriations = list(self.db.scalars(approp_stmt).all())

        lines: list[BudgetLineItem] = []
        total_budget = Decimal(0)
        total_committed = Decimal(0)
        total_obligated = Decimal(0)
        total_expended = Decimal(0)
        total_available = Decimal(0)

        for approp in appropriations:
            # Get commitment totals for this appropriation
            commit_stmt = select(
                func.coalesce(func.sum(Commitment.committed_amount), 0),
                func.coalesce(func.sum(Commitment.obligated_amount), 0),
                func.coalesce(func.sum(Commitment.expended_amount), 0),
                func.coalesce(func.sum(Commitment.cancelled_amount), 0),
            ).where(
                Commitment.appropriation_id == approp.appropriation_id,
                Commitment.status.notin_(
                    [
                        CommitmentStatus.CANCELLED,
                        CommitmentStatus.LAPSED,
                    ]
                ),
            )
            result = self.db.execute(commit_stmt).one()

            committed_raw = result[0] or Decimal(0)
            obligated = result[1] or Decimal(0)
            expended = result[2] or Decimal(0)
            cancelled = result[3] or Decimal(0)
            committed = committed_raw - cancelled
            available = approp.revised_amount - committed

            utilization_pct = (
                (committed / approp.revised_amount * 100)
                if approp.revised_amount > 0
                else Decimal(0)
            )

            # Get fund code for display
            fund = self.db.get(Fund, approp.fund_id)
            fund_code = fund.fund_code if fund else None

            lines.append(
                BudgetLineItem(
                    appropriation_id=approp.appropriation_id,
                    appropriation_code=approp.appropriation_code,
                    appropriation_name=approp.appropriation_name,
                    fund_code=fund_code,
                    original_budget=approp.approved_amount,
                    revised_budget=approp.revised_amount,
                    committed=committed,
                    obligated=obligated,
                    expended=expended,
                    available=available,
                    utilization_pct=utilization_pct.quantize(Decimal("0.01")),
                )
            )

            total_budget += approp.revised_amount
            total_committed += committed
            total_obligated += obligated
            total_expended += expended
            total_available += available

        return BudgetComparisonResponse(
            organization_id=organization_id,
            fiscal_year_id=fiscal_year_id,
            fund_id=fund_id,
            total_budget=total_budget,
            total_committed=total_committed,
            total_obligated=total_obligated,
            total_expended=total_expended,
            total_available=total_available,
            lines=lines,
        )
