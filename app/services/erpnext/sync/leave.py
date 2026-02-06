"""
Leave Sync Services - ERPNext to DotMac ERP.

Sync services for Leave entities:
- Leave Type
- Leave Allocation
- Leave Application
"""

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.leave.leave_allocation import LeaveAllocation
from app.models.people.leave.leave_application import (
    LeaveApplication,
    LeaveApplicationStatus,
)
from app.models.people.leave.leave_type import LeaveType, LeaveTypePolicy
from app.services.erpnext.mappings.leave import (
    LeaveAllocationMapping,
    LeaveApplicationMapping,
    LeaveTypeMapping,
)

from .base import BaseSyncService

logger = logging.getLogger(__name__)


class LeaveTypeSyncService(BaseSyncService[LeaveType]):
    """Sync Leave Types from ERPNext."""

    source_doctype = "Leave Type"
    target_table = "leave.leave_type"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = LeaveTypeMapping()
        self._leave_type_cache: dict[str, LeaveType] = {}

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        if since:
            yield from client.get_modified_since(
                doctype="Leave Type",
                since=since,
            )
        else:
            yield from client.get_leave_types()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._mapping.transform_record(record)

    def create_entity(self, data: dict[str, Any]) -> LeaveType:
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        # Map allocation policy
        policy_str = data.get("allocation_policy", "ANNUAL")
        try:
            policy = LeaveTypePolicy(policy_str)
        except ValueError:
            policy = LeaveTypePolicy.ANNUAL

        leave_type = LeaveType(
            organization_id=self.organization_id,
            leave_type_code=data["leave_type_code"][:30],
            leave_type_name=data["leave_type_name"][:100],
            allocation_policy=policy,
            max_days_per_year=data.get("max_days_per_year"),
            max_continuous_days=data.get("max_continuous_days"),
            allow_carry_forward=data.get("allow_carry_forward", False),
            is_compensatory=data.get("is_compensatory", False),
            is_lwp=data.get("is_lwp", False),
            include_holidays=data.get("include_holidays", False),
            is_active=data.get("is_active", True),
            # created_by_id not set for synced records
        )
        return leave_type

    def update_entity(self, entity: LeaveType, data: dict[str, Any]) -> LeaveType:
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        entity.leave_type_name = data["leave_type_name"][:100]
        entity.max_days_per_year = data.get("max_days_per_year")
        entity.max_continuous_days = data.get("max_continuous_days")
        entity.allow_carry_forward = data.get("allow_carry_forward", False)
        entity.is_compensatory = data.get("is_compensatory", False)
        entity.is_lwp = data.get("is_lwp", False)
        entity.include_holidays = data.get("include_holidays", False)
        entity.is_active = data.get("is_active", True)
        entity.updated_by_id = self.user_id
        return entity

    def get_entity_id(self, entity: LeaveType) -> uuid.UUID:
        return entity.leave_type_id

    def find_existing_entity(self, source_name: str) -> Optional[LeaveType]:
        if source_name in self._leave_type_cache:
            return self._leave_type_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            leave_type = self.db.get(LeaveType, sync_entity.target_id)
            if leave_type:
                self._leave_type_cache[source_name] = leave_type
                return leave_type

        return None


class LeaveAllocationSyncService(BaseSyncService[LeaveAllocation]):
    """Sync Leave Allocations from ERPNext."""

    source_doctype = "Leave Allocation"
    target_table = "leave.leave_allocation"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = LeaveAllocationMapping()
        self._allocation_cache: dict[str, LeaveAllocation] = {}

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        if since:
            yield from client.get_modified_since(
                doctype="Leave Allocation",
                since=since,
            )
        else:
            yield from client.get_leave_allocations()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._mapping.transform_record(record)

    def _resolve_entity_id(
        self, source_name: Optional[str], source_doctype: str
    ) -> Optional[uuid.UUID]:
        """Resolve a foreign key ID from ERPNext source name."""
        if not source_name:
            return None

        from app.models.sync import SyncEntity

        sync_entity = self.db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == source_doctype,
                SyncEntity.source_name == source_name,
            )
        ).scalar_one_or_none()

        if sync_entity and sync_entity.target_id:
            return sync_entity.target_id
        return None

    def create_entity(self, data: dict[str, Any]) -> LeaveAllocation:
        emp_source = data.pop("_employee_source_name", None)
        leave_type_source = data.pop("_leave_type_source_name", None)
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        # Resolve foreign keys
        employee_id = self._resolve_entity_id(emp_source, "Employee")
        leave_type_id = self._resolve_entity_id(leave_type_source, "Leave Type")

        if not employee_id:
            raise ValueError(f"Employee not found for: {emp_source}")
        if not leave_type_id:
            raise ValueError(f"Leave Type not found for: {leave_type_source}")

        allocation = LeaveAllocation(
            organization_id=self.organization_id,
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            from_date=data["from_date"],
            to_date=data["to_date"],
            new_leaves_allocated=data.get("new_leaves_allocated", Decimal("0")),
            carry_forward_leaves=data.get("carry_forward_leaves", Decimal("0")),
            total_leaves_allocated=data.get("total_leaves_allocated", Decimal("0")),
            is_active=data.get("is_active", True),
            # created_by_id not set for synced records
        )
        return allocation

    def update_entity(
        self, entity: LeaveAllocation, data: dict[str, Any]
    ) -> LeaveAllocation:
        data.pop("_employee_source_name", None)
        data.pop("_leave_type_source_name", None)
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        entity.new_leaves_allocated = data.get(
            "new_leaves_allocated", entity.new_leaves_allocated
        )
        entity.carry_forward_leaves = data.get(
            "carry_forward_leaves", entity.carry_forward_leaves
        )
        entity.total_leaves_allocated = data.get(
            "total_leaves_allocated", entity.total_leaves_allocated
        )
        entity.is_active = data.get("is_active", True)
        entity.updated_by_id = self.user_id
        return entity

    def get_entity_id(self, entity: LeaveAllocation) -> uuid.UUID:
        return entity.allocation_id

    def find_existing_entity(self, source_name: str) -> Optional[LeaveAllocation]:
        if source_name in self._allocation_cache:
            return self._allocation_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            allocation = self.db.get(LeaveAllocation, sync_entity.target_id)
            if allocation:
                self._allocation_cache[source_name] = allocation
                return allocation

        return None


class LeaveApplicationSyncService(BaseSyncService[LeaveApplication]):
    """Sync Leave Applications from ERPNext."""

    source_doctype = "Leave Application"
    target_table = "leave.leave_application"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = LeaveApplicationMapping()
        self._application_cache: dict[str, LeaveApplication] = {}

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        if since:
            yield from client.get_modified_since(
                doctype="Leave Application",
                since=since,
            )
        else:
            yield from client.get_leave_applications()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._mapping.transform_record(record)

    def _resolve_entity_id(
        self, source_name: Optional[str], source_doctype: str
    ) -> Optional[uuid.UUID]:
        if not source_name:
            return None

        from app.models.sync import SyncEntity

        sync_entity = self.db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == source_doctype,
                SyncEntity.source_name == source_name,
            )
        ).scalar_one_or_none()

        if sync_entity and sync_entity.target_id:
            return sync_entity.target_id
        return None

    def create_entity(self, data: dict[str, Any]) -> LeaveApplication:
        emp_source = data.pop("_employee_source_name", None)
        leave_type_source = data.pop("_leave_type_source_name", None)
        data.pop("_approver_user", None)
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        # Resolve foreign keys
        employee_id = self._resolve_entity_id(emp_source, "Employee")
        leave_type_id = self._resolve_entity_id(leave_type_source, "Leave Type")

        if not employee_id:
            raise ValueError(f"Employee not found for: {emp_source}")
        if not leave_type_id:
            raise ValueError(f"Leave Type not found for: {leave_type_source}")

        # Map status
        status_str = data.get("status", "SUBMITTED")
        try:
            status = LeaveApplicationStatus(status_str)
        except ValueError:
            status = LeaveApplicationStatus.SUBMITTED

        application = LeaveApplication(
            organization_id=self.organization_id,
            application_number=data["application_number"][:30],
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            from_date=data["from_date"],
            to_date=data["to_date"],
            total_leave_days=data.get("total_leave_days", Decimal("1")),
            half_day=data.get("half_day", False),
            half_day_date=data.get("half_day_date"),
            status=status,
            reason=data.get("reason"),
            # created_by_id not set for synced records
        )
        return application

    def update_entity(
        self, entity: LeaveApplication, data: dict[str, Any]
    ) -> LeaveApplication:
        data.pop("_employee_source_name", None)
        data.pop("_leave_type_source_name", None)
        data.pop("_approver_user", None)
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        entity.total_leave_days = data.get("total_leave_days", entity.total_leave_days)
        entity.half_day = data.get("half_day", entity.half_day)
        entity.half_day_date = data.get("half_day_date")
        entity.reason = data.get("reason")

        # Map status
        status_str = data.get("status", "SUBMITTED")
        try:
            entity.status = LeaveApplicationStatus(status_str)
        except ValueError:
            pass

        entity.updated_by_id = self.user_id
        return entity

    def get_entity_id(self, entity: LeaveApplication) -> uuid.UUID:
        return entity.application_id

    def find_existing_entity(self, source_name: str) -> Optional[LeaveApplication]:
        if source_name in self._application_cache:
            return self._application_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            application = self.db.get(LeaveApplication, sync_entity.target_id)
            if application:
                self._application_cache[source_name] = application
                return application

        return None
