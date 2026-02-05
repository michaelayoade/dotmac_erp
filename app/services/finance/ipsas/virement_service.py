"""
Virement Service - IPSAS budget reallocation.

Manages virements (transfers of budget authority between appropriations).
Workflow: DRAFT -> SUBMITTED -> APPROVED -> APPLIED.
"""

import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.finance.ipsas.enums import VirementStatus
from app.models.finance.ipsas.virement import Virement
from app.schemas.finance.ipsas import VirementCreate
from app.services.common import NotFoundError, ValidationError

logger = logging.getLogger(__name__)


class VirementService:
    """Service for managing IPSAS virements."""

    def __init__(self, db: Session):
        self.db = db

    def list_for_org(
        self,
        organization_id: UUID,
        *,
        fiscal_year_id: Optional[UUID] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Virement]:
        """List virements with optional filters."""
        stmt = select(Virement).where(Virement.organization_id == organization_id)

        if fiscal_year_id:
            stmt = stmt.where(Virement.fiscal_year_id == fiscal_year_id)
        if status:
            stmt = stmt.where(Virement.status == VirementStatus(status))

        stmt = stmt.order_by(Virement.created_at.desc()).offset(offset).limit(limit)
        return list(self.db.scalars(stmt).all())

    def get_or_404(
        self, virement_id: UUID, organization_id: Optional[UUID] = None
    ) -> Virement:
        """Get a virement by ID or raise NotFoundError.

        If organization_id is provided, also verifies tenant ownership.
        """
        virement = self.db.get(Virement, virement_id)
        if not virement:
            raise NotFoundError(f"Virement {virement_id} not found")
        if organization_id and virement.organization_id != organization_id:
            raise NotFoundError(f"Virement {virement_id} not found")
        return virement

    def create(
        self,
        organization_id: UUID,
        data: VirementCreate,
        user_id: UUID,
        virement_number: str,
    ) -> Virement:
        """Create a virement request."""
        if data.from_appropriation_id == data.to_appropriation_id:
            raise ValidationError(
                "Source and destination appropriations must be different"
            )

        virement = Virement(
            organization_id=organization_id,
            fiscal_year_id=data.fiscal_year_id,
            virement_number=virement_number,
            description=data.description,
            from_appropriation_id=data.from_appropriation_id,
            to_appropriation_id=data.to_appropriation_id,
            amount=data.amount,
            currency_code=data.currency_code,
            justification=data.justification,
            from_account_id=data.from_account_id,
            from_cost_center_id=data.from_cost_center_id,
            from_fund_id=data.from_fund_id,
            to_account_id=data.to_account_id,
            to_cost_center_id=data.to_cost_center_id,
            to_fund_id=data.to_fund_id,
            approval_authority=data.approval_authority,
            created_by_user_id=user_id,
        )
        self.db.add(virement)
        self.db.flush()

        logger.info("Created virement %s: %s", virement_number, virement.virement_id)
        return virement

    def approve(self, virement_id: UUID, approver_id: UUID) -> Virement:
        """Approve a virement (transition DRAFT/SUBMITTED -> APPROVED)."""
        virement = self.get_or_404(virement_id)

        if virement.status not in (VirementStatus.DRAFT, VirementStatus.SUBMITTED):
            raise ValidationError(
                f"Cannot approve virement in {virement.status.value} status"
            )

        if virement.created_by_user_id == approver_id:
            raise ValidationError("Segregation of duties: creator cannot approve")

        virement.status = VirementStatus.APPROVED
        virement.approved_by_user_id = approver_id
        virement.approved_at = datetime.now()
        self.db.flush()

        logger.info("Approved virement %s by %s", virement_id, approver_id)
        return virement

    def apply(self, virement_id: UUID) -> Virement:
        """Apply an approved virement (transfer budget amounts)."""
        from app.models.finance.ipsas.appropriation import Appropriation

        virement = self.get_or_404(virement_id)

        if virement.status != VirementStatus.APPROVED:
            raise ValidationError(
                f"Cannot apply virement in {virement.status.value} status"
            )

        # Load source and destination appropriations
        from_approp = self.db.get(Appropriation, virement.from_appropriation_id)
        to_approp = self.db.get(Appropriation, virement.to_appropriation_id)

        if not from_approp or not to_approp:
            raise ValidationError("Source or destination appropriation not found")

        if (
            from_approp.organization_id != virement.organization_id
            or to_approp.organization_id != virement.organization_id
        ):
            raise ValidationError("Appropriations must belong to the same organization")

        if (
            from_approp.fiscal_year_id != virement.fiscal_year_id
            or to_approp.fiscal_year_id != virement.fiscal_year_id
        ):
            raise ValidationError(
                "Appropriations must be in the same fiscal year as the virement"
            )

        if (
            from_approp.currency_code != virement.currency_code
            or to_approp.currency_code != virement.currency_code
        ):
            raise ValidationError("Appropriation currency must match virement currency")

        # Check source has sufficient balance
        if from_approp.revised_amount < virement.amount:
            raise ValidationError(
                f"Insufficient balance in source appropriation "
                f"{from_approp.appropriation_code}: "
                f"available {from_approp.revised_amount}, requested {virement.amount}"
            )

        # Transfer amounts
        from_approp.revised_amount -= virement.amount
        to_approp.revised_amount += virement.amount

        virement.status = VirementStatus.APPLIED
        virement.applied_at = datetime.now()
        self.db.flush()

        logger.info(
            "Applied virement %s: %s %s from %s to %s",
            virement.virement_number,
            virement.currency_code,
            virement.amount,
            from_approp.appropriation_code,
            to_approp.appropriation_code,
        )
        return virement

    def count_for_org(self, organization_id: UUID) -> int:
        """Count virements for an organization."""
        stmt = select(func.count(Virement.virement_id)).where(
            Virement.organization_id == organization_id
        )
        return self.db.scalar(stmt) or 0
