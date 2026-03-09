"""
Available Balance Service - IPSAS budget availability checking.

Calculates available balance considering appropriations, allotments,
commitments, obligations, and expenditures.
"""

import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.ipsas.appropriation import Allotment, Appropriation
from app.models.finance.ipsas.commitment import Commitment
from app.models.finance.ipsas.enums import AppropriationStatus, CommitmentStatus
from app.schemas.finance.ipsas import AvailableBalanceResponse
from app.services.finance.platform.org_context import org_context_service

logger = logging.getLogger(__name__)


class AvailableBalanceService:
    """Service for calculating IPSAS available balance."""

    def __init__(self, db: Session):
        self.db = db

    def calculate(
        self,
        organization_id: UUID,
        *,
        appropriation_id: UUID | None = None,
        fund_id: UUID | None = None,
        account_id: UUID | None = None,
    ) -> AvailableBalanceResponse:
        """
        Calculate available balance for given filters.

        Available = Appropriated - Net Committed
        Net Committed = Committed - Cancelled
        """
        # Sum appropriations
        approp_stmt = select(
            func.coalesce(func.sum(Appropriation.revised_amount), 0)
        ).where(
            Appropriation.organization_id == organization_id,
            Appropriation.status.in_(
                [
                    AppropriationStatus.APPROVED,
                    AppropriationStatus.ACTIVE,
                ]
            ),
        )
        if appropriation_id:
            approp_stmt = approp_stmt.where(
                Appropriation.appropriation_id == appropriation_id
            )
        if fund_id:
            approp_stmt = approp_stmt.where(Appropriation.fund_id == fund_id)
        if account_id:
            approp_stmt = approp_stmt.where(Appropriation.account_id == account_id)

        total_appropriated = self.db.scalar(approp_stmt) or Decimal(0)

        # Sum allotments
        allotment_stmt = select(
            func.coalesce(func.sum(Allotment.allotted_amount), 0)
        ).where(Allotment.organization_id == organization_id)
        if appropriation_id:
            allotment_stmt = allotment_stmt.where(
                Allotment.appropriation_id == appropriation_id
            )
        total_allotted = self.db.scalar(allotment_stmt) or Decimal(0)

        # Sum commitments
        commit_stmt = select(
            func.coalesce(func.sum(Commitment.committed_amount), 0),
            func.coalesce(func.sum(Commitment.obligated_amount), 0),
            func.coalesce(func.sum(Commitment.expended_amount), 0),
            func.coalesce(func.sum(Commitment.cancelled_amount), 0),
        ).where(
            Commitment.organization_id == organization_id,
            Commitment.status.notin_(
                [
                    CommitmentStatus.CANCELLED,
                    CommitmentStatus.LAPSED,
                ]
            ),
        )
        if fund_id:
            commit_stmt = commit_stmt.where(Commitment.fund_id == fund_id)
        if appropriation_id:
            commit_stmt = commit_stmt.where(
                Commitment.appropriation_id == appropriation_id
            )
        if account_id:
            commit_stmt = commit_stmt.where(Commitment.account_id == account_id)

        result = self.db.execute(commit_stmt).one()
        total_committed_raw = result[0] or Decimal(0)
        total_obligated = result[1] or Decimal(0)
        total_expended = result[2] or Decimal(0)
        total_cancelled = result[3] or Decimal(0)

        total_committed = total_committed_raw - total_cancelled
        available = total_appropriated - total_committed

        # Determine currency from first matching appropriation in scope
        currency_stmt = select(Appropriation.currency_code).where(
            Appropriation.organization_id == organization_id
        )
        if appropriation_id:
            currency_stmt = currency_stmt.where(
                Appropriation.appropriation_id == appropriation_id
            )
        if fund_id:
            currency_stmt = currency_stmt.where(Appropriation.fund_id == fund_id)
        if account_id:
            currency_stmt = currency_stmt.where(Appropriation.account_id == account_id)
        currency_code = self.db.scalar(currency_stmt.limit(1)) or (
            org_context_service.get_functional_currency(self.db, organization_id)
        )

        return AvailableBalanceResponse(
            organization_id=organization_id,
            appropriation_id=appropriation_id,
            fund_id=fund_id,
            account_id=account_id,
            total_appropriated=total_appropriated,
            total_allotted=total_allotted,
            total_committed=total_committed,
            total_obligated=total_obligated,
            total_expended=total_expended,
            available_balance=available,
            currency_code=currency_code,
        )

    def calculate_by_fund(
        self,
        organization_id: UUID,
        fund_id: UUID,
    ) -> AvailableBalanceResponse:
        """Calculate available balance for a specific fund."""
        return self.calculate(organization_id, fund_id=fund_id)
