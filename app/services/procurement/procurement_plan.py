"""
Procurement Plan Service.

Business logic for procurement plan management.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.procurement.enums import ProcurementPlanStatus
from app.models.procurement.procurement_plan import ProcurementPlan
from app.models.procurement.procurement_plan_item import ProcurementPlanItem
from app.schemas.procurement.procurement_plan import (
    ProcurementPlanCreate,
    ProcurementPlanUpdate,
)
from app.services.common import NotFoundError, ValidationError
from app.services.procurement.thresholds import determine_procurement_method

logger = logging.getLogger(__name__)

PLAN_EXPORT_COLUMNS: list[str] = [
    "plan_number",
    "fiscal_year",
    "title",
    "currency_code",
    "line_number",
    "description",
    "budget_line_code",
    "budget_id",
    "estimated_value",
    "procurement_method",
    "planned_quarter",
    "category",
]


class ProcurementPlanService:
    """Service for procurement plan management."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, organization_id: UUID, plan_id: UUID) -> ProcurementPlan | None:
        """Get a plan by ID."""
        stmt = select(ProcurementPlan).where(
            ProcurementPlan.organization_id == organization_id,
            ProcurementPlan.plan_id == plan_id,
        )
        return self.db.scalar(stmt)

    def list_plans(
        self,
        organization_id: UUID,
        *,
        status: str | None = None,
        fiscal_year: str | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> tuple[list[ProcurementPlan], int]:
        """List plans with filters."""
        base = select(ProcurementPlan).where(
            ProcurementPlan.organization_id == organization_id,
        )
        if status:
            base = base.where(ProcurementPlan.status == ProcurementPlanStatus(status))
        if fiscal_year:
            base = base.where(ProcurementPlan.fiscal_year == fiscal_year)
        if search:
            from sqlalchemy import or_

            term = f"%{search}%"
            base = base.where(
                or_(
                    ProcurementPlan.plan_number.ilike(term),
                    ProcurementPlan.title.ilike(term),
                )
            )

        total = self.db.scalar(select(func.count()).select_from(base.subquery()))
        plans = list(
            self.db.scalars(
                base.order_by(ProcurementPlan.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )
        return plans, total or 0

    def get_summary(self, organization_id: UUID) -> dict[str, Any]:
        """Get summary statistics for procurement plans."""
        plans = self.db.scalars(
            select(ProcurementPlan).where(
                ProcurementPlan.organization_id == organization_id,
            )
        ).all()

        status_counts: dict[str, int] = {}
        total_value = Decimal("0")
        for plan in plans:
            key = plan.status.value
            status_counts[key] = status_counts.get(key, 0) + 1
            total_value += plan.total_estimated_value

        return {
            "total_plans": len(list(plans)),
            "status_counts": status_counts,
            "total_estimated_value": total_value,
        }

    def create(
        self,
        organization_id: UUID,
        data: ProcurementPlanCreate,
        created_by_user_id: UUID,
    ) -> ProcurementPlan:
        """Create a new procurement plan."""
        plan = ProcurementPlan(
            organization_id=organization_id,
            plan_number=data.plan_number,
            fiscal_year=data.fiscal_year,
            title=data.title,
            currency_code=data.currency_code,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(plan)
        self.db.flush()

        total = Decimal("0")
        for item_data in data.items:
            method, authority = determine_procurement_method(
                item_data.estimated_value, self.db, organization_id
            )
            item = ProcurementPlanItem(
                plan_id=plan.plan_id,
                organization_id=organization_id,
                line_number=item_data.line_number,
                description=item_data.description,
                budget_line_code=item_data.budget_line_code,
                budget_id=item_data.budget_id,
                estimated_value=item_data.estimated_value,
                procurement_method=item_data.procurement_method,
                planned_quarter=item_data.planned_quarter,
                approving_authority=authority,
                category=item_data.category,
            )
            self.db.add(item)
            total += item_data.estimated_value

        plan.total_estimated_value = total
        self.db.flush()
        logger.info("Created procurement plan %s", plan.plan_number)
        return plan

    def update(
        self,
        organization_id: UUID,
        plan_id: UUID,
        data: ProcurementPlanUpdate,
    ) -> ProcurementPlan:
        """Update a procurement plan."""
        plan = self.get_by_id(organization_id, plan_id)
        if not plan:
            raise NotFoundError("Procurement plan not found")
        if plan.status != ProcurementPlanStatus.DRAFT:
            raise ValidationError("Only draft plans can be updated")

        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(plan, field, value)

        self.db.flush()
        return plan

    def submit(self, organization_id: UUID, plan_id: UUID) -> ProcurementPlan:
        """Submit a plan for approval."""
        plan = self.get_by_id(organization_id, plan_id)
        if not plan:
            raise NotFoundError("Procurement plan not found")
        if plan.status != ProcurementPlanStatus.DRAFT:
            raise ValidationError("Only draft plans can be submitted")

        plan.status = ProcurementPlanStatus.SUBMITTED
        self.db.flush()
        logger.info("Submitted procurement plan %s", plan.plan_number)
        return plan

    def approve(
        self,
        organization_id: UUID,
        plan_id: UUID,
        approved_by_user_id: UUID,
    ) -> ProcurementPlan:
        """Approve a procurement plan."""
        plan = self.get_by_id(organization_id, plan_id)
        if not plan:
            raise NotFoundError("Procurement plan not found")
        if plan.status != ProcurementPlanStatus.SUBMITTED:
            raise ValidationError("Only submitted plans can be approved")

        plan.status = ProcurementPlanStatus.APPROVED
        plan.approved_by_user_id = approved_by_user_id
        plan.approved_at = datetime.now(UTC)
        self.db.flush()
        logger.info("Approved procurement plan %s", plan.plan_number)
        return plan

    def export_plans_data(
        self,
        org_id: UUID,
        status_filter: str | None = None,
        fiscal_year: str | None = None,
        columns: list[str] | None = None,
    ) -> tuple[list[str], list[list[object]]]:
        """Build detailed per-item export data for procurement plans.

        Each plan item becomes one row.  Plans with no items still produce
        a single row with blank item columns.

        Args:
            org_id: Organization ID
            status_filter: Optional status filter
            fiscal_year: Optional fiscal year filter
            columns: Header column names to use (defaults to PLAN_EXPORT_COLUMNS)

        Returns:
            Tuple of (headers, rows) for CSV/XLSX export.
        """
        from sqlalchemy.orm import selectinload

        stmt = (
            select(ProcurementPlan)
            .where(ProcurementPlan.organization_id == org_id)
            .options(selectinload(ProcurementPlan.items))
            .order_by(ProcurementPlan.created_at.desc())
        )
        if status_filter:
            try:
                stmt = stmt.where(
                    ProcurementPlan.status == ProcurementPlanStatus(status_filter)
                )
            except ValueError:
                pass
        if fiscal_year:
            stmt = stmt.where(ProcurementPlan.fiscal_year == fiscal_year)

        plans = list(self.db.scalars(stmt).all())
        headers = columns or PLAN_EXPORT_COLUMNS

        rows: list[list[object]] = []
        empty_item = [""] * 8  # 8 item-level columns
        for plan in plans:
            plan_prefix: list[object] = [
                plan.plan_number,
                plan.fiscal_year,
                plan.title,
                plan.currency_code,
            ]
            if plan.items:
                for item in plan.items:
                    rows.append(
                        plan_prefix
                        + [
                            item.line_number,
                            item.description,
                            item.budget_line_code or "",
                            str(item.budget_id) if item.budget_id else "",
                            item.estimated_value,
                            item.procurement_method.value
                            if item.procurement_method
                            else "",
                            item.planned_quarter,
                            item.category or "",
                        ]
                    )
            else:
                rows.append(plan_prefix + empty_item)
        return headers, rows

    def find_duplicate_plan_numbers(
        self,
        org_id: UUID,
        plan_numbers: list[str],
    ) -> list[str]:
        """Check which plan numbers already exist.

        Args:
            org_id: Organization ID
            plan_numbers: List of plan numbers to check

        Returns:
            List of plan numbers that already exist.
        """
        if not plan_numbers:
            return []

        existing = list(
            self.db.scalars(
                select(ProcurementPlan.plan_number).where(
                    ProcurementPlan.organization_id == org_id,
                    ProcurementPlan.plan_number.in_(plan_numbers),
                )
            ).all()
        )
        return existing
