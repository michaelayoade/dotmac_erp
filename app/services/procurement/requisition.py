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

REQUISITION_EXPORT_COLUMNS: list[str] = [
    "requisition_number",
    "requisition_date",
    "requester_id",
    "department_id",
    "urgency",
    "justification",
    "currency_code",
    "material_request_id",
    "plan_item_id",
    "line_number",
    "item_id",
    "description",
    "quantity",
    "uom",
    "estimated_unit_price",
    "estimated_amount",
    "expense_account_id",
    "cost_center_id",
    "project_id",
    "delivery_date",
]


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

    def export_requisitions_data(
        self,
        org_id: UUID,
        status_filter: str | None = None,
        urgency_filter: str | None = None,
        columns: list[str] | None = None,
    ) -> tuple[list[str], list[list[object]]]:
        """Build detailed per-line export data for requisitions.

        Each requisition line becomes one row.  Requisitions with no lines
        still produce a single row with blank line columns.

        Args:
            org_id: Organization ID
            status_filter: Optional status filter
            urgency_filter: Optional urgency filter
            columns: Header column names to use (defaults to REQUISITION_EXPORT_COLUMNS)

        Returns:
            Tuple of (headers, rows) for CSV/XLSX export.
        """
        from sqlalchemy.orm import selectinload

        stmt = (
            select(PurchaseRequisition)
            .where(PurchaseRequisition.organization_id == org_id)
            .options(selectinload(PurchaseRequisition.lines))
            .order_by(PurchaseRequisition.created_at.desc())
        )
        if status_filter:
            try:
                stmt = stmt.where(
                    PurchaseRequisition.status == RequisitionStatus(status_filter)
                )
            except ValueError:
                pass
        if urgency_filter:
            try:
                stmt = stmt.where(
                    PurchaseRequisition.urgency == UrgencyLevel(urgency_filter)
                )
            except ValueError:
                pass

        requisitions = list(self.db.scalars(stmt).all())
        headers = columns or REQUISITION_EXPORT_COLUMNS

        rows: list[list[object]] = []
        empty_line = [""] * 11  # 11 line-level columns
        for req in requisitions:
            header_values: list[object] = [
                req.requisition_number,
                req.requisition_date.isoformat() if req.requisition_date else "",
                str(req.requester_id),
                str(req.department_id) if req.department_id else "",
                req.urgency.value if req.urgency else "",
                req.justification or "",
                req.currency_code,
                str(req.material_request_id) if req.material_request_id else "",
                str(req.plan_item_id) if req.plan_item_id else "",
            ]
            if req.lines:
                for line in req.lines:
                    rows.append(
                        header_values
                        + [
                            line.line_number,
                            str(line.item_id) if line.item_id else "",
                            line.description,
                            line.quantity,
                            line.uom or "",
                            line.estimated_unit_price,
                            line.estimated_amount,
                            str(line.expense_account_id)
                            if line.expense_account_id
                            else "",
                            str(line.cost_center_id) if line.cost_center_id else "",
                            str(line.project_id) if line.project_id else "",
                            line.delivery_date.isoformat()
                            if line.delivery_date
                            else "",
                        ]
                    )
            else:
                rows.append(header_values + empty_line)
        return headers, rows

    def find_duplicate_requisition_numbers(
        self,
        org_id: UUID,
        requisition_numbers: list[str],
    ) -> list[str]:
        """Check which requisition numbers already exist.

        Args:
            org_id: Organization ID
            requisition_numbers: List of numbers to check

        Returns:
            List of requisition numbers that already exist.
        """
        if not requisition_numbers:
            return []

        existing = list(
            self.db.scalars(
                select(PurchaseRequisition.requisition_number).where(
                    PurchaseRequisition.organization_id == org_id,
                    PurchaseRequisition.requisition_number.in_(requisition_numbers),
                )
            ).all()
        )
        return existing
