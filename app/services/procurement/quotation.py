"""
Quotation Response Service.

Business logic for vendor bid/quotation management.
"""

import logging
from typing import Any, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.procurement.quotation_response import QuotationResponse
from app.models.procurement.quotation_response_line import QuotationResponseLine
from app.services.common import NotFoundError

logger = logging.getLogger(__name__)


class QuotationResponseService:
    """Service for vendor quotation/bid management."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(
        self,
        organization_id: UUID,
        response_id: UUID,
    ) -> Optional[QuotationResponse]:
        """Get a quotation response by ID."""
        stmt = select(QuotationResponse).where(
            QuotationResponse.organization_id == organization_id,
            QuotationResponse.response_id == response_id,
        )
        return self.db.scalar(stmt)

    def list_for_rfq(
        self,
        organization_id: UUID,
        rfq_id: UUID,
    ) -> List[QuotationResponse]:
        """List all responses for an RFQ."""
        stmt = (
            select(QuotationResponse)
            .where(
                QuotationResponse.organization_id == organization_id,
                QuotationResponse.rfq_id == rfq_id,
            )
            .order_by(QuotationResponse.total_amount)
        )
        return list(self.db.scalars(stmt).all())

    def create(
        self,
        organization_id: UUID,
        data: Any,
        created_by_user_id: Optional[UUID] = None,
    ) -> QuotationResponse:
        """Record a vendor's quotation response."""
        response = QuotationResponse(
            organization_id=organization_id,
            rfq_id=data.rfq_id,
            supplier_id=data.supplier_id,
            response_number=data.response_number,
            response_date=data.response_date,
            total_amount=data.total_amount,
            currency_code=data.currency_code,
            delivery_period_days=data.delivery_period_days,
            validity_days=data.validity_days,
            technical_proposal=data.technical_proposal,
            notes=data.notes,
        )
        self.db.add(response)
        self.db.flush()

        for line_data in data.lines:
            line = QuotationResponseLine(
                response_id=response.response_id,
                requisition_line_id=line_data.requisition_line_id,
                line_number=line_data.line_number,
                description=line_data.description,
                quantity=line_data.quantity,
                unit_price=line_data.unit_price,
                line_amount=line_data.line_amount,
                delivery_date=line_data.delivery_date,
            )
            self.db.add(line)

        self.db.flush()
        logger.info("Recorded quotation response %s", response.response_number)
        return response

    def update(
        self,
        organization_id: UUID,
        response_id: UUID,
        data: Any,
    ) -> QuotationResponse:
        """Update a quotation response."""
        response = self.get_by_id(organization_id, response_id)
        if not response:
            raise NotFoundError("Quotation response not found")

        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(response, field, value)

        self.db.flush()
        return response
