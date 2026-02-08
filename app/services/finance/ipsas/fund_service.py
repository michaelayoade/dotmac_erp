"""
Fund Service - IPSAS fund accounting.

Manages IPSAS funds (general, capital, donor, trust, etc.).
"""

import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.ipsas.enums import FundStatus, FundType
from app.models.finance.ipsas.fund import Fund
from app.schemas.finance.ipsas import FundCreate, FundUpdate
from app.services.common import NotFoundError

logger = logging.getLogger(__name__)


class FundService:
    """Service for managing IPSAS funds."""

    def __init__(self, db: Session):
        self.db = db

    def _commit_and_refresh(self, fund: Fund) -> None:
        self.db.commit()
        self.db.refresh(fund)

    def list_for_org(
        self,
        organization_id: UUID,
        *,
        status: str | None = None,
        fund_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Fund]:
        """List funds for an organization with optional filters."""
        stmt = select(Fund).where(Fund.organization_id == organization_id)

        if status:
            stmt = stmt.where(Fund.status == FundStatus(status))
        if fund_type:
            stmt = stmt.where(Fund.fund_type == FundType(fund_type))

        stmt = stmt.order_by(Fund.fund_code).offset(offset).limit(limit)
        return list(self.db.scalars(stmt).all())

    def get_or_404(self, fund_id: UUID, organization_id: UUID | None = None) -> Fund:
        """Get a fund by ID or raise NotFoundError.

        If organization_id is provided, also verifies tenant ownership.
        """
        fund = self.db.get(Fund, fund_id)
        if not fund:
            raise NotFoundError(f"Fund {fund_id} not found")
        if organization_id and fund.organization_id != organization_id:
            raise NotFoundError(f"Fund {fund_id} not found")
        return fund

    def create(
        self,
        organization_id: UUID,
        data: FundCreate,
        user_id: UUID,
    ) -> Fund:
        """Create a new fund."""
        fund = Fund(
            organization_id=organization_id,
            fund_code=data.fund_code,
            fund_name=data.fund_name,
            fund_type=FundType(data.fund_type),
            effective_from=data.effective_from,
            description=data.description,
            is_restricted=data.is_restricted,
            restriction_description=data.restriction_description,
            donor_name=data.donor_name,
            donor_reference=data.donor_reference,
            parent_fund_id=data.parent_fund_id,
            created_by_user_id=user_id,
        )
        self.db.add(fund)
        self.db.flush()
        logger.info("Created fund %s: %s", fund.fund_code, fund.fund_id)
        self._commit_and_refresh(fund)
        return fund

    def update(self, fund_id: UUID, data: FundUpdate) -> Fund:
        """Update an existing fund."""
        fund = self.get_or_404(fund_id)
        update_data = data.model_dump(exclude_unset=True)

        if "status" in update_data and update_data["status"]:
            update_data["status"] = FundStatus(update_data["status"])

        for field, value in update_data.items():
            setattr(fund, field, value)

        self.db.flush()
        logger.info("Updated fund %s", fund_id)
        self._commit_and_refresh(fund)
        return fund

    def get_fund_balance(
        self,
        fund_id: UUID,
        fiscal_period_id: UUID | None = None,
    ) -> Decimal:
        """
        Get aggregated balance for a fund.

        Sums revised appropriations minus net commitments.
        """
        from app.models.finance.ipsas.appropriation import Appropriation
        from app.models.finance.ipsas.commitment import Commitment
        from app.models.finance.ipsas.enums import AppropriationStatus, CommitmentStatus

        self.get_or_404(fund_id)  # verify exists

        # Sum revised appropriation amounts
        approp_stmt = select(
            func.coalesce(func.sum(Appropriation.revised_amount), 0)
        ).where(
            Appropriation.fund_id == fund_id,
            Appropriation.status.in_(
                [
                    AppropriationStatus.APPROVED,
                    AppropriationStatus.ACTIVE,
                ]
            ),
        )
        total_appropriated = self.db.scalar(approp_stmt) or Decimal(0)

        # Sum committed amounts (net of cancellations)
        commitment_stmt = select(
            func.coalesce(func.sum(Commitment.committed_amount), 0),
            func.coalesce(func.sum(Commitment.cancelled_amount), 0),
        ).where(
            Commitment.fund_id == fund_id,
            Commitment.status.notin_(
                [
                    CommitmentStatus.CANCELLED,
                    CommitmentStatus.LAPSED,
                ]
            ),
        )

        if fiscal_period_id:
            commitment_stmt = commitment_stmt.where(
                Commitment.fiscal_period_id == fiscal_period_id
            )

        result = self.db.execute(commitment_stmt).one()
        total_committed = result[0] or Decimal(0)
        total_cancelled = result[1] or Decimal(0)

        return total_appropriated - (total_committed - total_cancelled)

    def count_for_org(self, organization_id: UUID) -> int:
        """Count funds for an organization."""
        stmt = select(func.count(Fund.fund_id)).where(
            Fund.organization_id == organization_id
        )
        return self.db.scalar(stmt) or 0
