"""
Requisition Service.

Business logic for purchase requisition management.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.procurement.enums import RequisitionStatus, UrgencyLevel
from app.models.procurement.purchase_requisition import PurchaseRequisition
from app.models.procurement.purchase_requisition_line import PurchaseRequisitionLine
from app.schemas.procurement.requisition import RequisitionCreate, RequisitionUpdate
from app.services.common import NotFoundError, ValidationError

logger = logging.getLogger(__name__)


class RequisitionService:
    """Service for purchase requisition management."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(
        self,
        organization_id: UUID,
        requisition_id: UUID,
    ) -> PurchaseRequisition | None:
        """Get a requisition by ID."""
        stmt = select(PurchaseRequisition).where(
            PurchaseRequisition.organization_id == organization_id,
            PurchaseRequisition.requisition_id == requisition_id,
        )
        return self.db.scalar(stmt)

    def list_requisitions(
        self,
        organization_id: UUID,
        *,
        status: str | None = None,
        urgency: str | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> tuple[list[PurchaseRequisition], int]:
        """List requisitions with filters."""
        base = select(PurchaseRequisition).where(
            PurchaseRequisition.organization_id == organization_id,
        )
        if status:
            base = base.where(
                PurchaseRequisition.status == RequisitionStatus(status),
            )
        if urgency:
            try:
                urgency_enum = UrgencyLevel(urgency)
            except ValueError:
                urgency_enum = None
            if urgency_enum:
                base = base.where(
                    PurchaseRequisition.urgency == urgency_enum,
                )
        if search:
            from sqlalchemy import or_

            term = f"%{search}%"
            base = base.where(
                or_(
                    PurchaseRequisition.requisition_number.ilike(term),
                    PurchaseRequisition.justification.ilike(term),
                )
            )

        total = self.db.scalar(select(func.count()).select_from(base.subquery()))
        items = list(
            self.db.scalars(
                base.order_by(PurchaseRequisition.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )
        return items, total or 0

    def create(
        self,
        organization_id: UUID,
        data: RequisitionCreate,
        created_by_user_id: UUID,
    ) -> PurchaseRequisition:
        """Create a new requisition."""
        req = PurchaseRequisition(
            organization_id=organization_id,
            requisition_number=data.requisition_number,
            requisition_date=data.requisition_date,
            requester_id=data.requester_id,
            department_id=data.department_id,
            urgency=data.urgency,
            justification=data.justification,
            currency_code=data.currency_code,
            material_request_id=data.material_request_id,
            plan_item_id=data.plan_item_id,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(req)
        self.db.flush()

        total = Decimal("0")
        for line_data in data.lines:
            line = PurchaseRequisitionLine(
                requisition_id=req.requisition_id,
                organization_id=organization_id,
                line_number=line_data.line_number,
                item_id=line_data.item_id,
                description=line_data.description,
                quantity=line_data.quantity,
                uom=line_data.uom,
                estimated_unit_price=line_data.estimated_unit_price,
                estimated_amount=line_data.estimated_amount,
                expense_account_id=line_data.expense_account_id,
                cost_center_id=line_data.cost_center_id,
                project_id=line_data.project_id,
                delivery_date=line_data.delivery_date,
            )
            self.db.add(line)
            total += line_data.estimated_amount

        req.total_estimated_amount = total
        self.db.flush()
        logger.info("Created requisition %s", req.requisition_number)
        return req

    def update(
        self,
        organization_id: UUID,
        requisition_id: UUID,
        data: RequisitionUpdate,
    ) -> PurchaseRequisition:
        """Update a requisition."""
        req = self.get_by_id(organization_id, requisition_id)
        if not req:
            raise NotFoundError("Requisition not found")
        if req.status != RequisitionStatus.DRAFT:
            raise ValidationError("Only draft requisitions can be updated")

        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(req, field, value)

        self.db.flush()
        return req

    def submit(
        self,
        organization_id: UUID,
        requisition_id: UUID,
    ) -> PurchaseRequisition:
        """Submit a requisition for review."""
        req = self.get_by_id(organization_id, requisition_id)
        if not req:
            raise NotFoundError("Requisition not found")
        if req.status != RequisitionStatus.DRAFT:
            raise ValidationError("Only draft requisitions can be submitted")

        req.status = RequisitionStatus.SUBMITTED
        self.db.flush()
        logger.info("Submitted requisition %s", req.requisition_number)
        return req

    def verify_budget(
        self,
        organization_id: UUID,
        requisition_id: UUID,
        verified_by_user_id: UUID,
    ) -> PurchaseRequisition:
        """Mark requisition as budget verified."""
        req = self.get_by_id(organization_id, requisition_id)
        if not req:
            raise NotFoundError("Requisition not found")
        if req.status != RequisitionStatus.SUBMITTED:
            raise ValidationError("Only submitted requisitions can be budget verified")

        req.status = RequisitionStatus.BUDGET_VERIFIED
        req.budget_verified = True
        req.budget_verified_by_id = verified_by_user_id
        req.budget_verified_at = datetime.now(UTC)
        self.db.flush()
        logger.info("Budget verified requisition %s", req.requisition_number)
        return req

    def approve(
        self,
        organization_id: UUID,
        requisition_id: UUID,
        approved_by_user_id: UUID,
    ) -> PurchaseRequisition:
        """Approve a requisition."""
        req = self.get_by_id(organization_id, requisition_id)
        if not req:
            raise NotFoundError("Requisition not found")
        if req.status not in (
            RequisitionStatus.SUBMITTED,
            RequisitionStatus.BUDGET_VERIFIED,
        ):
            raise ValidationError("Requisition is not in an approvable state")

        req.status = RequisitionStatus.APPROVED
        req.approved_by_user_id = approved_by_user_id
        req.approved_at = datetime.now(UTC)
        self.db.flush()
        logger.info("Approved requisition %s", req.requisition_number)
        return req

    def reject(
        self,
        organization_id: UUID,
        requisition_id: UUID,
    ) -> PurchaseRequisition:
        """Reject a requisition."""
        req = self.get_by_id(organization_id, requisition_id)
        if not req:
            raise NotFoundError("Requisition not found")

        req.status = RequisitionStatus.REJECTED
        self.db.flush()
        logger.info("Rejected requisition %s", req.requisition_number)
        return req
