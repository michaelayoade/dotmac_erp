"""
RFQ Service.

Business logic for Request for Quotation management.
"""

import logging
from typing import Any, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.procurement.enums import RFQStatus
from app.models.procurement.rfq import RequestForQuotation
from app.models.procurement.rfq_invitation import RFQInvitation
from app.services.common import NotFoundError, ValidationError

logger = logging.getLogger(__name__)


class RFQService:
    """Service for RFQ management."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(
        self,
        organization_id: UUID,
        rfq_id: UUID,
    ) -> Optional[RequestForQuotation]:
        """Get an RFQ by ID."""
        stmt = select(RequestForQuotation).where(
            RequestForQuotation.organization_id == organization_id,
            RequestForQuotation.rfq_id == rfq_id,
        )
        return self.db.scalar(stmt)

    def list_rfqs(
        self,
        organization_id: UUID,
        *,
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 25,
    ) -> Tuple[List[RequestForQuotation], int]:
        """List RFQs with filters."""
        base = select(RequestForQuotation).where(
            RequestForQuotation.organization_id == organization_id,
        )
        if status:
            base = base.where(RequestForQuotation.status == RFQStatus(status))

        total = self.db.scalar(select(func.count()).select_from(base.subquery()))
        items = list(
            self.db.scalars(
                base.order_by(RequestForQuotation.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )
        return items, total or 0

    def create(
        self,
        organization_id: UUID,
        data: Any,
        created_by_user_id: UUID,
    ) -> RequestForQuotation:
        """Create a new RFQ."""
        rfq = RequestForQuotation(
            organization_id=organization_id,
            rfq_number=data.rfq_number,
            title=data.title,
            rfq_date=data.rfq_date,
            closing_date=data.closing_date,
            procurement_method=data.procurement_method,
            requisition_id=data.requisition_id,
            plan_item_id=data.plan_item_id,
            evaluation_criteria=data.evaluation_criteria,
            terms_and_conditions=data.terms_and_conditions,
            estimated_value=data.estimated_value,
            currency_code=data.currency_code,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(rfq)
        self.db.flush()
        logger.info("Created RFQ %s", rfq.rfq_number)
        return rfq

    def update(
        self,
        organization_id: UUID,
        rfq_id: UUID,
        data: Any,
    ) -> RequestForQuotation:
        """Update an RFQ."""
        rfq = self.get_by_id(organization_id, rfq_id)
        if not rfq:
            raise NotFoundError("RFQ not found")
        if rfq.status != RFQStatus.DRAFT:
            raise ValidationError("Only draft RFQs can be updated")

        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(rfq, field, value)

        self.db.flush()
        return rfq

    def publish(
        self,
        organization_id: UUID,
        rfq_id: UUID,
    ) -> RequestForQuotation:
        """Publish an RFQ for vendor responses."""
        rfq = self.get_by_id(organization_id, rfq_id)
        if not rfq:
            raise NotFoundError("RFQ not found")
        if rfq.status != RFQStatus.DRAFT:
            raise ValidationError("Only draft RFQs can be published")

        rfq.status = RFQStatus.PUBLISHED
        self.db.flush()
        logger.info("Published RFQ %s", rfq.rfq_number)
        return rfq

    def invite_vendor(
        self,
        organization_id: UUID,
        rfq_id: UUID,
        supplier_id: UUID,
    ) -> RFQInvitation:
        """Invite a vendor to respond to an RFQ."""
        rfq = self.get_by_id(organization_id, rfq_id)
        if not rfq:
            raise NotFoundError("RFQ not found")

        invitation = RFQInvitation(
            rfq_id=rfq_id,
            supplier_id=supplier_id,
        )
        self.db.add(invitation)
        self.db.flush()
        logger.info("Invited vendor %s to RFQ %s", supplier_id, rfq.rfq_number)
        return invitation

    def close_bidding(
        self,
        organization_id: UUID,
        rfq_id: UUID,
    ) -> RequestForQuotation:
        """Close bidding on an RFQ."""
        rfq = self.get_by_id(organization_id, rfq_id)
        if not rfq:
            raise NotFoundError("RFQ not found")
        if rfq.status != RFQStatus.PUBLISHED:
            raise ValidationError("Only published RFQs can be closed")

        rfq.status = RFQStatus.CLOSED
        self.db.flush()
        logger.info("Closed bidding on RFQ %s", rfq.rfq_number)
        return rfq
