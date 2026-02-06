"""
Vendor Prequalification Service.

Business logic for vendor prequalification management.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.procurement.enums import PrequalificationStatus
from app.models.procurement.vendor_prequalification import VendorPrequalification
from app.schemas.procurement.vendor import (
    PrequalificationCreate,
    PrequalificationUpdate,
)
from app.services.common import NotFoundError

logger = logging.getLogger(__name__)


class VendorPrequalificationService:
    """Service for vendor prequalification management."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(
        self,
        organization_id: UUID,
        prequalification_id: UUID,
    ) -> Optional[VendorPrequalification]:
        """Get a prequalification by ID."""
        stmt = select(VendorPrequalification).where(
            VendorPrequalification.organization_id == organization_id,
            VendorPrequalification.prequalification_id == prequalification_id,
        )
        return self.db.scalar(stmt)

    def list_prequalifications(
        self,
        organization_id: UUID,
        *,
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 25,
    ) -> Tuple[List[VendorPrequalification], int]:
        """List prequalifications with filters."""
        base = select(VendorPrequalification).where(
            VendorPrequalification.organization_id == organization_id,
        )
        if status:
            base = base.where(
                VendorPrequalification.status == PrequalificationStatus(status),
            )

        total = self.db.scalar(select(func.count()).select_from(base.subquery()))
        items = list(
            self.db.scalars(
                base.order_by(VendorPrequalification.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )
        return items, total or 0

    def create(
        self,
        organization_id: UUID,
        data: PrequalificationCreate,
    ) -> VendorPrequalification:
        """Create a new prequalification record."""
        preq = VendorPrequalification(
            organization_id=organization_id,
            supplier_id=data.supplier_id,
            application_date=data.application_date,
            categories=data.categories,
            documents_verified=data.documents_verified,
            tax_clearance_valid=data.tax_clearance_valid,
            pension_compliance=data.pension_compliance,
            itf_compliance=data.itf_compliance,
            nsitf_compliance=data.nsitf_compliance,
        )
        self.db.add(preq)
        self.db.flush()
        logger.info("Created prequalification for supplier %s", data.supplier_id)
        return preq

    def update(
        self,
        organization_id: UUID,
        prequalification_id: UUID,
        data: PrequalificationUpdate,
    ) -> VendorPrequalification:
        """Update a prequalification record."""
        preq = self.get_by_id(organization_id, prequalification_id)
        if not preq:
            raise NotFoundError("Prequalification not found")

        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(preq, field, value)

        self.db.flush()
        return preq

    def qualify(
        self,
        organization_id: UUID,
        prequalification_id: UUID,
        reviewed_by_user_id: UUID,
    ) -> VendorPrequalification:
        """Qualify a vendor."""
        preq = self.get_by_id(organization_id, prequalification_id)
        if not preq:
            raise NotFoundError("Prequalification not found")

        preq.status = PrequalificationStatus.QUALIFIED
        preq.reviewed_by_user_id = reviewed_by_user_id
        preq.reviewed_at = datetime.now(timezone.utc)
        self.db.flush()
        logger.info("Qualified vendor %s", preq.supplier_id)
        return preq

    def disqualify(
        self,
        organization_id: UUID,
        prequalification_id: UUID,
        reviewed_by_user_id: UUID,
        reason: Optional[str] = None,
    ) -> VendorPrequalification:
        """Disqualify a vendor."""
        preq = self.get_by_id(organization_id, prequalification_id)
        if not preq:
            raise NotFoundError("Prequalification not found")

        preq.status = PrequalificationStatus.DISQUALIFIED
        preq.review_notes = reason
        preq.reviewed_by_user_id = reviewed_by_user_id
        preq.reviewed_at = datetime.now(timezone.utc)
        self.db.flush()
        logger.info("Disqualified vendor %s", preq.supplier_id)
        return preq

    def blacklist(
        self,
        organization_id: UUID,
        prequalification_id: UUID,
        reason: str,
        reviewed_by_user_id: UUID,
    ) -> VendorPrequalification:
        """Blacklist a vendor."""
        preq = self.get_by_id(organization_id, prequalification_id)
        if not preq:
            raise NotFoundError("Prequalification not found")

        preq.status = PrequalificationStatus.BLACKLISTED
        preq.blacklisted = True
        preq.blacklist_reason = reason
        preq.reviewed_by_user_id = reviewed_by_user_id
        preq.reviewed_at = datetime.now(timezone.utc)
        self.db.flush()
        logger.info("Blacklisted vendor %s", preq.supplier_id)
        return preq
