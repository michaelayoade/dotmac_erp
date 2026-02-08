"""Asset assignment service."""

from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.fixed_assets.asset import Asset, AssetStatus
from app.models.people.assets.assignment import (
    AssetAssignment,
    AssetCondition,
    AssignmentStatus,
)
from app.models.people.hr.employee import Employee
from app.services.common import (
    ConflictError,
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    ValidationError,
)

logger = logging.getLogger(__name__)

__all__ = ["AssetAssignmentService"]


class AssetAssignmentService:
    """Manage asset assignments to employees."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _get_asset(self, org_id: UUID, asset_id: UUID) -> Asset:
        asset = self.db.get(Asset, asset_id)
        if not asset or asset.organization_id != org_id:
            raise NotFoundError("Asset not found")
        return asset

    def _get_employee(self, org_id: UUID, employee_id: UUID) -> Employee:
        employee = self.db.get(Employee, employee_id)
        if not employee or employee.organization_id != org_id:
            raise NotFoundError("Employee not found")
        return employee

    def _sync_finance_asset_on_issue(
        self,
        asset: Asset,
        employee: Employee,
        issued_on: date,
    ) -> None:
        if asset.status in {AssetStatus.DISPOSED, AssetStatus.IMPAIRED}:
            raise ValidationError(f"Cannot assign asset in {asset.status.value} status")
        if asset.status == AssetStatus.DRAFT:
            asset.status = AssetStatus.ACTIVE
            asset.in_service_date = asset.in_service_date or issued_on
            asset.depreciation_start_date = (
                asset.depreciation_start_date or asset.in_service_date
            )
        asset.custodian_employee_id = employee.employee_id

    def list_assignments(
        self,
        org_id: UUID,
        *,
        asset_id: UUID | None = None,
        employee_id: UUID | None = None,
        status: AssignmentStatus | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[AssetAssignment]:
        query = select(AssetAssignment).where(AssetAssignment.organization_id == org_id)

        if asset_id:
            query = query.where(AssetAssignment.asset_id == asset_id)

        if employee_id:
            query = query.where(AssetAssignment.employee_id == employee_id)

        if status:
            query = query.where(AssetAssignment.status == status)

        query = query.order_by(AssetAssignment.issued_on.desc())

        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_assignment(self, org_id: UUID, assignment_id: UUID) -> AssetAssignment:
        assignment = self.db.scalar(
            select(AssetAssignment).where(
                AssetAssignment.organization_id == org_id,
                AssetAssignment.assignment_id == assignment_id,
            )
        )
        if not assignment:
            raise NotFoundError(f"Assignment {assignment_id} not found")
        return assignment

    def issue_asset(
        self,
        org_id: UUID,
        *,
        asset_id: UUID,
        employee_id: UUID,
        issued_on: date,
        expected_return_date: date | None = None,
        condition_on_issue: AssetCondition | None = None,
        notes: str | None = None,
    ) -> AssetAssignment:
        asset = self._get_asset(org_id, asset_id)
        employee = self._get_employee(org_id, employee_id)

        existing = self.db.scalar(
            select(AssetAssignment).where(
                AssetAssignment.organization_id == org_id,
                AssetAssignment.asset_id == asset_id,
                AssetAssignment.status == AssignmentStatus.ISSUED,
            )
        )
        if existing:
            raise ConflictError("Asset is already assigned")

        self._sync_finance_asset_on_issue(asset, employee, issued_on)

        assignment = AssetAssignment(
            organization_id=org_id,
            asset_id=asset_id,
            employee_id=employee_id,
            issued_on=issued_on,
            expected_return_date=expected_return_date,
            condition_on_issue=condition_on_issue,
            status=AssignmentStatus.ISSUED,
            notes=notes,
        )
        self.db.add(assignment)
        self.db.flush()
        return assignment

    def return_asset(
        self,
        org_id: UUID,
        assignment_id: UUID,
        *,
        returned_on: date | None = None,
        condition_on_return: AssetCondition | None = None,
        notes: str | None = None,
    ) -> AssetAssignment:
        assignment = self.get_assignment(org_id, assignment_id)
        if assignment.status != AssignmentStatus.ISSUED:
            raise ValidationError(
                f"Cannot return assignment in {assignment.status.value} status"
            )
        asset = self._get_asset(org_id, assignment.asset_id)
        assignment.status = AssignmentStatus.RETURNED
        assignment.returned_on = returned_on or date.today()
        assignment.condition_on_return = condition_on_return
        if notes:
            assignment.notes = notes
        asset.custodian_employee_id = None
        self.db.flush()
        return assignment

    def transfer_asset(
        self,
        org_id: UUID,
        assignment_id: UUID,
        *,
        new_employee_id: UUID,
        issued_on: date | None = None,
        expected_return_date: date | None = None,
        condition_on_issue: AssetCondition | None = None,
        notes: str | None = None,
    ) -> AssetAssignment:
        assignment = self.get_assignment(org_id, assignment_id)
        if assignment.status != AssignmentStatus.ISSUED:
            raise ValidationError(
                f"Cannot transfer assignment in {assignment.status.value} status"
            )
        asset = self._get_asset(org_id, assignment.asset_id)
        new_employee = self._get_employee(org_id, new_employee_id)
        assignment.status = AssignmentStatus.TRANSFERRED
        self.db.flush()

        self._sync_finance_asset_on_issue(
            asset,
            new_employee,
            issued_on or date.today(),
        )

        new_assignment = AssetAssignment(
            organization_id=org_id,
            asset_id=assignment.asset_id,
            employee_id=new_employee_id,
            issued_on=issued_on or date.today(),
            expected_return_date=expected_return_date,
            condition_on_issue=condition_on_issue or assignment.condition_on_issue,
            status=AssignmentStatus.ISSUED,
            notes=notes,
            transfer_from_assignment_id=assignment.assignment_id,
        )
        self.db.add(new_assignment)
        self.db.flush()
        return new_assignment
