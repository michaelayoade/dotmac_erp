"""
Attendance Sync Services - ERPNext to DotMac ERP.

Sync services for Attendance entities:
- Shift Type
- Attendance
"""

import logging
import uuid
from datetime import datetime
from datetime import time as datetime_time
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.attendance.attendance import Attendance, AttendanceStatus
from app.models.people.attendance.shift_type import ShiftType
from app.services.erpnext.mappings.attendance import (
    AttendanceMapping,
    ShiftTypeMapping,
)

from .base import BaseSyncService

logger = logging.getLogger(__name__)


class ShiftTypeSyncService(BaseSyncService[ShiftType]):
    """Sync Shift Types from ERPNext."""

    source_doctype = "Shift Type"
    target_table = "attendance.shift_type"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = ShiftTypeMapping()
        self._shift_type_cache: dict[str, ShiftType] = {}

    def fetch_records(self, client: Any, since: datetime | None = None):
        if since:
            yield from client.get_modified_since(
                doctype="Shift Type",
                since=since,
            )
        else:
            yield from client.get_shift_types()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._mapping.transform_record(record)

    def create_entity(self, data: dict[str, Any]) -> ShiftType:
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        # Ensure times are valid
        start_time = data.get("start_time")
        end_time = data.get("end_time")

        if not isinstance(start_time, datetime_time):
            start_time = datetime_time(9, 0)  # Default 9 AM
        if not isinstance(end_time, datetime_time):
            end_time = datetime_time(17, 0)  # Default 5 PM

        shift = ShiftType(
            organization_id=self.organization_id,
            shift_code=data["shift_code"][:30],
            shift_name=data["shift_name"][:100],
            start_time=start_time,
            end_time=end_time,
            working_hours=data.get("working_hours", Decimal("8")),
            late_entry_grace_period=data.get("late_entry_grace_period", 0),
            early_exit_grace_period=data.get("early_exit_grace_period", 0),
            half_day_threshold_hours=data.get("half_day_threshold_hours"),
            break_duration_minutes=data.get("break_duration_minutes", 60),
            is_active=data.get("is_active", True),
            # created_by_id not set for synced records
        )
        return shift

    def update_entity(self, entity: ShiftType, data: dict[str, Any]) -> ShiftType:
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        # Update times if provided
        if data.get("start_time") and isinstance(data["start_time"], datetime_time):
            entity.start_time = data["start_time"]
        if data.get("end_time") and isinstance(data["end_time"], datetime_time):
            entity.end_time = data["end_time"]

        entity.shift_name = data["shift_name"][:100]
        entity.working_hours = data.get("working_hours", entity.working_hours)
        entity.late_entry_grace_period = data.get(
            "late_entry_grace_period", entity.late_entry_grace_period
        )
        entity.early_exit_grace_period = data.get(
            "early_exit_grace_period", entity.early_exit_grace_period
        )
        entity.half_day_threshold_hours = data.get("half_day_threshold_hours")
        entity.break_duration_minutes = data.get(
            "break_duration_minutes", entity.break_duration_minutes
        )
        entity.is_active = data.get("is_active", True)
        entity.updated_by_id = self.user_id
        return entity

    def get_entity_id(self, entity: ShiftType) -> uuid.UUID:
        return entity.shift_type_id

    def find_existing_entity(self, source_name: str) -> ShiftType | None:
        if source_name in self._shift_type_cache:
            return self._shift_type_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            shift = self.db.get(ShiftType, sync_entity.target_id)
            if shift:
                self._shift_type_cache[source_name] = shift
                return shift

        return None


class AttendanceSyncService(BaseSyncService[Attendance]):
    """Sync Attendance records from ERPNext."""

    source_doctype = "Attendance"
    target_table = "attendance.attendance"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = AttendanceMapping()
        self._attendance_cache: dict[str, Attendance] = {}

    def fetch_records(self, client: Any, since: datetime | None = None):
        if since:
            yield from client.get_modified_since(
                doctype="Attendance",
                since=since,
            )
        else:
            yield from client.get_attendance()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._mapping.transform_record(record)

    def _resolve_entity_id(
        self, source_name: str | None, source_doctype: str
    ) -> uuid.UUID | None:
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

    def create_entity(self, data: dict[str, Any]) -> Attendance:
        emp_source = data.pop("_employee_source_name", None)
        shift_source = data.pop("_shift_source_name", None)
        data.pop("_leave_type_source_name", None)
        data.pop("_leave_application_source_name", None)
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        # Resolve foreign keys
        employee_id = self._resolve_entity_id(emp_source, "Employee")
        shift_type_id = self._resolve_entity_id(shift_source, "Shift Type")

        if not employee_id:
            raise ValueError(f"Employee not found for: {emp_source}")

        # Map status
        status_str = data.get("status", "PRESENT")
        try:
            status = AttendanceStatus(status_str)
        except ValueError:
            status = AttendanceStatus.PRESENT

        attendance = Attendance(
            organization_id=self.organization_id,
            employee_id=employee_id,
            shift_type_id=shift_type_id,
            attendance_date=data["attendance_date"],
            status=status,
            check_in=data.get("check_in"),
            check_out=data.get("check_out"),
            working_hours=data.get("working_hours"),
            overtime_hours=data.get("overtime_hours", Decimal("0")),
            late_entry=data.get("is_late", False),
            early_exit=data.get("is_early_exit", False),
            # created_by_id not set for synced records
        )
        return attendance

    def update_entity(self, entity: Attendance, data: dict[str, Any]) -> Attendance:
        data.pop("_employee_source_name", None)
        data.pop("_shift_source_name", None)
        data.pop("_leave_type_source_name", None)
        data.pop("_leave_application_source_name", None)
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        # Map status
        status_str = data.get("status", "PRESENT")
        try:
            entity.status = AttendanceStatus(status_str)
        except ValueError:
            pass

        entity.check_in = data.get("check_in")
        entity.check_out = data.get("check_out")
        entity.working_hours = data.get("working_hours")
        entity.overtime_hours = data.get("overtime_hours", entity.overtime_hours)
        entity.late_entry = data.get("is_late", entity.late_entry)
        entity.early_exit = data.get("is_early_exit", entity.early_exit)
        entity.updated_by_id = self.user_id
        return entity

    def get_entity_id(self, entity: Attendance) -> uuid.UUID:
        return entity.attendance_id

    def find_existing_entity(self, source_name: str) -> Attendance | None:
        if source_name in self._attendance_cache:
            return self._attendance_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            attendance = self.db.get(Attendance, sync_entity.target_id)
            if attendance:
                self._attendance_cache[source_name] = attendance
                return attendance

        return None
