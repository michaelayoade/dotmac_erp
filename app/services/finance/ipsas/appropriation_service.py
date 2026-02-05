"""
Appropriation Service - IPSAS budget authority management.

Manages appropriations (legislatively authorized spending authority)
and allotments (sub-allocations to cost centers/periods).
"""

import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.finance.ipsas.appropriation import Allotment, Appropriation
from app.models.finance.ipsas.enums import (
    AppropriationStatus,
    AppropriationType,
)
from app.schemas.finance.ipsas import AllotmentCreate, AppropriationCreate
from app.services.common import ForbiddenError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)


class AppropriationService:
    """Service for managing IPSAS appropriations and allotments."""

    def __init__(self, db: Session):
        self.db = db

    # ─── Appropriations ───────────────────────────────────────────────

    def list_for_org(
        self,
        organization_id: UUID,
        *,
        fiscal_year_id: Optional[UUID] = None,
        fund_id: Optional[UUID] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Appropriation]:
        """List appropriations with optional filters."""
        stmt = select(Appropriation).where(
            Appropriation.organization_id == organization_id
        )

        if fiscal_year_id:
            stmt = stmt.where(Appropriation.fiscal_year_id == fiscal_year_id)
        if fund_id:
            stmt = stmt.where(Appropriation.fund_id == fund_id)
        if status:
            stmt = stmt.where(Appropriation.status == AppropriationStatus(status))

        stmt = (
            stmt.order_by(Appropriation.appropriation_code).offset(offset).limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    def get_or_404(
        self, appropriation_id: UUID, organization_id: Optional[UUID] = None
    ) -> Appropriation:
        """Get an appropriation by ID or raise NotFoundError.

        If organization_id is provided, also verifies tenant ownership.
        """
        approp = self.db.get(Appropriation, appropriation_id)
        if not approp:
            raise NotFoundError(f"Appropriation {appropriation_id} not found")
        if organization_id and approp.organization_id != organization_id:
            raise NotFoundError(f"Appropriation {appropriation_id} not found")
        return approp

    def create(
        self,
        organization_id: UUID,
        data: AppropriationCreate,
        user_id: UUID,
    ) -> Appropriation:
        """Create a new appropriation."""
        approp = Appropriation(
            organization_id=organization_id,
            fiscal_year_id=data.fiscal_year_id,
            fund_id=data.fund_id,
            appropriation_code=data.appropriation_code,
            appropriation_name=data.appropriation_name,
            appropriation_type=AppropriationType(data.appropriation_type),
            approved_amount=data.approved_amount,
            revised_amount=data.approved_amount,  # Initially same as approved
            currency_code=data.currency_code,
            effective_from=data.effective_from,
            effective_to=data.effective_to,
            budget_id=data.budget_id,
            account_id=data.account_id,
            cost_center_id=data.cost_center_id,
            business_unit_id=data.business_unit_id,
            appropriation_act_reference=data.appropriation_act_reference,
            created_by_user_id=user_id,
        )
        self.db.add(approp)
        self.db.flush()
        logger.info(
            "Created appropriation %s: %s",
            approp.appropriation_code,
            approp.appropriation_id,
        )
        return approp

    def approve(self, appropriation_id: UUID, approver_id: UUID) -> Appropriation:
        """Approve an appropriation (transition DRAFT/SUBMITTED -> APPROVED)."""
        approp = self.get_or_404(appropriation_id)

        if approp.status not in (
            AppropriationStatus.DRAFT,
            AppropriationStatus.SUBMITTED,
        ):
            raise ValidationError(
                f"Cannot approve appropriation in {approp.status.value} status"
            )

        if approp.created_by_user_id == approver_id:
            raise ValidationError("Segregation of duties: creator cannot approve")

        approp.status = AppropriationStatus.APPROVED
        approp.approved_by_user_id = approver_id
        approp.approved_at = datetime.now()
        self.db.flush()

        logger.info("Approved appropriation %s by %s", appropriation_id, approver_id)
        return approp

    def count_for_org(
        self,
        organization_id: UUID,
        *,
        fiscal_year_id: Optional[UUID] = None,
    ) -> int:
        """Count appropriations for an organization."""
        stmt = select(func.count(Appropriation.appropriation_id)).where(
            Appropriation.organization_id == organization_id
        )
        if fiscal_year_id:
            stmt = stmt.where(Appropriation.fiscal_year_id == fiscal_year_id)
        return self.db.scalar(stmt) or 0

    # ─── Allotments ───────────────────────────────────────────────────

    def list_allotments(
        self,
        organization_id: UUID,
        *,
        appropriation_id: Optional[UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Allotment]:
        """List allotments with optional filters."""
        stmt = select(Allotment).where(Allotment.organization_id == organization_id)

        if appropriation_id:
            stmt = stmt.where(Allotment.appropriation_id == appropriation_id)

        stmt = stmt.order_by(Allotment.allotment_code).offset(offset).limit(limit)
        return list(self.db.scalars(stmt).all())

    def create_allotment(
        self,
        organization_id: UUID,
        data: AllotmentCreate,
    ) -> Allotment:
        """Create an allotment under an appropriation."""
        # Verify appropriation exists and belongs to org
        approp = self.get_or_404(data.appropriation_id)
        if approp.organization_id != organization_id:
            raise ForbiddenError("Appropriation does not belong to this organization")

        allotment = Allotment(
            appropriation_id=data.appropriation_id,
            organization_id=organization_id,
            allotment_code=data.allotment_code,
            allotment_name=data.allotment_name,
            allotted_amount=data.allotted_amount,
            period_from=data.period_from,
            period_to=data.period_to,
            cost_center_id=data.cost_center_id,
            business_unit_id=data.business_unit_id,
        )
        self.db.add(allotment)
        self.db.flush()

        logger.info(
            "Created allotment %s under appropriation %s",
            allotment.allotment_code,
            approp.appropriation_code,
        )
        return allotment
