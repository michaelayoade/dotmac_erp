"""
ERPNext Timesheet Sync Service.

Syncs Timesheet Detail rows to pm.time_entry table.
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pm import BillingStatus, TimeEntry
from app.models.sync import SyncEntity
from app.services.erpnext.mappings.timesheets import TimesheetDetailMapping

from .base import BaseSyncService

logger = logging.getLogger(__name__)


class TimesheetSyncService(BaseSyncService[TimeEntry]):
    """
    Sync service for ERPNext Timesheets.

    Syncs individual Timesheet Detail rows as TimeEntry records.

    Features:
    - Employee resolution from parent timesheet
    - Project and task linkage
    - Billable/non-billable tracking
    """

    source_doctype = "Timesheet Detail"
    target_table = "pm.time_entry"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self.mapping = TimesheetDetailMapping()
        self._employee_cache: dict[str, uuid.UUID] = {}
        self._project_cache: dict[str, uuid.UUID] = {}
        self._task_cache: dict[str, uuid.UUID] = {}

    def fetch_records(self, client: Any, since: datetime | None = None):
        """
        Fetch Timesheet records from ERPNext.

        Fetches parent Timesheet documents and yields each child detail row.
        """
        filters = []

        # Filter by modification date for incremental sync
        if since:
            filters.append(["modified", ">=", since.isoformat()])

        # Only fetch submitted timesheets
        filters.append(["docstatus", "=", "1"])

        # Get parent timesheet fields
        parent_fields = [
            "name",
            "employee",
            "employee_name",
            "company",
            "modified",
        ]

        for timesheet in client.get_all_documents(
            "Timesheet", filters=filters, fields=parent_fields
        ):
            timesheet_name = timesheet.get("name")
            employee_name = timesheet.get("employee")

            # Fetch child table rows
            detail_fields = [
                "name",
                "parent",
                "project",
                "task",
                "from_time",
                "to_time",
                "hours",
                "activity_type",
                "description",
                "is_billable",
                "billing_hours",
                "is_billed",
            ]

            details = client.list_documents(
                "Timesheet Detail",
                filters=[["parent", "=", timesheet_name]],
                fields=detail_fields,
            )

            for detail in details:
                # Attach parent employee reference
                detail["_parent_employee"] = employee_name
                detail["modified"] = timesheet.get("modified")
                yield detail

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform ERPNext Timesheet Detail to DotMac time_entry format."""
        return self.mapping.transform_record(record)

    def create_entity(self, data: dict[str, Any]) -> TimeEntry:
        """Create DotMac TimeEntry from transformed data."""
        # Resolve project ID
        project_source_name = data.pop("_project_source_name", None)
        project_id = self._resolve_project_id(project_source_name)

        if not project_id:
            # Skip entries without project (general time entries)
            raise ValueError(
                f"Cannot create time entry without project: {project_source_name}"
            )

        # Resolve task ID
        task_source_name = data.pop("_task_source_name", None)
        task_id = self._resolve_task_id(task_source_name)

        # Resolve employee ID
        employee_source_name = data.pop("_employee_source_name", None)
        employee_id = self._resolve_employee_id(employee_source_name)

        if not employee_id:
            raise ValueError(
                f"Cannot create time entry without employee: {employee_source_name}"
            )

        # Convert billing_status string to enum
        billing_status_value = data.pop("billing_status", "NOT_BILLED")

        time_entry = TimeEntry(
            organization_id=self.organization_id,
            project_id=project_id,
            task_id=task_id,
            employee_id=employee_id,
            billing_status=BillingStatus(billing_status_value),
            # Don't set created_by_id - synced data doesn't have a DotMac creator
            **data,
        )

        return time_entry

    def update_entity(self, entity: TimeEntry, data: dict[str, Any]) -> TimeEntry:
        """Update existing TimeEntry with new data."""
        # Remove reference fields we don't update
        data.pop("_project_source_name", None)
        data.pop("_task_source_name", None)
        data.pop("_employee_source_name", None)

        # Convert billing_status string to enum
        if "billing_status" in data:
            entity.billing_status = BillingStatus(data.pop("billing_status"))

        # Update other fields
        for key, value in data.items():
            if hasattr(entity, key) and value is not None:
                setattr(entity, key, value)

        # Don't set updated_by_id - synced data doesn't have a DotMac updater

        return entity

    def get_entity_id(self, entity: TimeEntry) -> uuid.UUID:
        """Get the time entry ID."""
        return entity.entry_id

    def find_existing_entity(self, source_name: str) -> TimeEntry | None:
        """Find existing TimeEntry by sync record."""
        sync_entity = self.get_sync_entity(source_name)
        if not sync_entity or not sync_entity.target_id:
            return None

        return self.db.execute(
            select(TimeEntry).where(TimeEntry.entry_id == sync_entity.target_id)
        ).scalar_one_or_none()

    def _resolve_project_id(self, project_source_name: str | None) -> uuid.UUID | None:
        """Resolve DotMac project_id from ERPNext project name."""
        if not project_source_name:
            return None

        # Check cache
        if project_source_name in self._project_cache:
            return self._project_cache[project_source_name]

        # Look up in sync entities
        result = self.db.execute(
            select(SyncEntity.target_id).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Project",
                SyncEntity.source_name == project_source_name,
            )
        ).scalar_one_or_none()

        if result:
            self._project_cache[project_source_name] = result

        return result

    def _resolve_task_id(self, task_source_name: str | None) -> uuid.UUID | None:
        """Resolve DotMac task_id from ERPNext task name."""
        if not task_source_name:
            return None

        # Check cache
        if task_source_name in self._task_cache:
            return self._task_cache[task_source_name]

        # Look up in sync entities
        result = self.db.execute(
            select(SyncEntity.target_id).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Task",
                SyncEntity.source_name == task_source_name,
            )
        ).scalar_one_or_none()

        if result:
            self._task_cache[task_source_name] = result

        return result

    def _resolve_employee_id(
        self, employee_source_name: str | None
    ) -> uuid.UUID | None:
        """Resolve DotMac employee_id from ERPNext employee name."""
        if not employee_source_name:
            return None

        # Check cache
        if employee_source_name in self._employee_cache:
            return self._employee_cache[employee_source_name]

        # Look up in sync entities
        result = self.db.execute(
            select(SyncEntity.target_id).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Employee",
                SyncEntity.source_name == employee_source_name,
            )
        ).scalar_one_or_none()

        if result:
            self._employee_cache[employee_source_name] = result

        return result
