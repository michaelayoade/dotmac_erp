"""
CoA Segment Service - IPSAS Chart of Accounts segment management.

Manages government chart of accounts segment definitions and values
(Administrative, Economic, Fund, Functional, Program, Project).
"""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ipsas.coa_segment import CoASegmentDefinition, CoASegmentValue
from app.models.finance.ipsas.enums import CoASegmentType
from app.schemas.finance.ipsas import CoASegmentDefinitionCreate, CoASegmentValueCreate
from app.services.common import ConflictError, ForbiddenError, NotFoundError

logger = logging.getLogger(__name__)


class CoASegmentService:
    """Service for managing IPSAS CoA segments."""

    def __init__(self, db: Session):
        self.db = db

    def _commit_and_refresh(self, entity) -> None:
        self.db.commit()
        self.db.refresh(entity)

    def list_definitions(self, organization_id: UUID) -> list[CoASegmentDefinition]:
        """List all segment definitions for an organization."""
        stmt = (
            select(CoASegmentDefinition)
            .where(CoASegmentDefinition.organization_id == organization_id)
            .order_by(CoASegmentDefinition.display_order)
        )
        return list(self.db.scalars(stmt).all())

    def define_segment(
        self,
        organization_id: UUID,
        data: CoASegmentDefinitionCreate,
    ) -> CoASegmentDefinition:
        """Create a CoA segment definition."""
        # Check for duplicate segment type
        existing = self.db.scalar(
            select(CoASegmentDefinition).where(
                CoASegmentDefinition.organization_id == organization_id,
                CoASegmentDefinition.segment_type == CoASegmentType(data.segment_type),
            )
        )
        if existing:
            raise ConflictError(f"Segment type {data.segment_type} already defined")

        segment_def = CoASegmentDefinition(
            organization_id=organization_id,
            segment_type=CoASegmentType(data.segment_type),
            segment_name=data.segment_name,
            code_position_start=data.code_position_start,
            code_length=data.code_length,
            separator=data.separator,
            is_required=data.is_required,
            display_order=data.display_order,
        )
        self.db.add(segment_def)
        self.db.flush()

        logger.info(
            "Defined CoA segment %s for org %s",
            data.segment_type,
            organization_id,
        )
        self._commit_and_refresh(segment_def)
        return segment_def

    def list_values(self, segment_def_id: UUID) -> list[CoASegmentValue]:
        """List all values for a segment definition."""
        stmt = (
            select(CoASegmentValue)
            .where(CoASegmentValue.segment_def_id == segment_def_id)
            .order_by(CoASegmentValue.segment_code)
        )
        return list(self.db.scalars(stmt).all())

    def create_value(
        self,
        organization_id: UUID,
        segment_def_id: UUID,
        data: CoASegmentValueCreate,
    ) -> CoASegmentValue:
        """Create a value for a CoA segment."""
        # Verify segment definition exists
        seg_def = self.db.get(CoASegmentDefinition, segment_def_id)
        if not seg_def:
            raise NotFoundError(f"Segment definition {segment_def_id} not found")
        if seg_def.organization_id != organization_id:
            raise ForbiddenError(
                "Segment definition does not belong to this organization"
            )

        value = CoASegmentValue(
            segment_def_id=segment_def_id,
            organization_id=organization_id,
            segment_code=data.segment_code,
            segment_name=data.segment_name,
            parent_segment_value_id=data.parent_segment_value_id,
            is_active=data.is_active,
        )
        self.db.add(value)
        self.db.flush()

        logger.info(
            "Created CoA segment value %s for segment %s",
            data.segment_code,
            segment_def_id,
        )
        self._commit_and_refresh(value)
        return value
