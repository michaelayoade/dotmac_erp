"""
ERPNext Material Request Sync Service.

Syncs Material Request DocType to inv.material_request table with items.
Supports cross-module links to Projects, Support Tickets, and Tasks.
"""

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import (
    MaterialRequest,
    MaterialRequestItem,
    MaterialRequestStatus,
    MaterialRequestType,
)
from app.models.sync import SyncEntity
from app.services.erpnext.mappings.material_request import (
    MaterialRequestItemMapping,
    MaterialRequestMapping,
)

from .base import BaseSyncService

logger = logging.getLogger(__name__)


class MaterialRequestSyncService(BaseSyncService[MaterialRequest]):
    """
    Sync service for ERPNext Material Requests.

    Features:
    - Header + line items sync
    - FK resolution for items, warehouses, projects, tickets, tasks
    - Employee lookup via user email for requested_by
    - Cross-module inventory tracking links
    """

    source_doctype = "Material Request"
    target_table = "inv.material_request"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = MaterialRequestMapping()
        self._item_mapping = MaterialRequestItemMapping()
        # Caches for FK resolution
        self._request_cache: dict[str, MaterialRequest] = {}
        self._item_cache: dict[str, uuid.UUID] = {}
        self._warehouse_cache: dict[str, uuid.UUID] = {}
        self._project_cache: dict[str, uuid.UUID] = {}
        self._ticket_cache: dict[str, uuid.UUID] = {}
        self._task_cache: dict[str, uuid.UUID] = {}
        self._employee_by_user_cache: dict[str, uuid.UUID | None] = {}

    def fetch_records(self, client: Any, since: datetime | None = None):
        """Fetch Material Requests with their items."""
        if since:
            for request in client.get_modified_since(
                doctype="Material Request",
                since=since,
            ):
                # Fetch items for each request
                request["items"] = client.list_documents(
                    doctype="Material Request Item",
                    filters={"parent": request["name"]},
                )
                yield request
        else:
            yield from client.get_material_requests()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform Material Request with items."""
        result = self._mapping.transform_record(record)

        # Transform items
        result["_items"] = []
        for item in record.get("items", []):
            item_data = self._item_mapping.transform_record(item)
            result["_items"].append(item_data)

        return result

    def _resolve_entity_id(
        self, source_name: str | None, source_doctype: str
    ) -> uuid.UUID | None:
        """Resolve DotMac entity ID from ERPNext source name via SyncEntity."""
        if not source_name:
            return None

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

    def _resolve_item_id(self, source_name: str | None) -> uuid.UUID | None:
        """Resolve DotMac item_id from ERPNext item_code."""
        if not source_name:
            return None

        if source_name in self._item_cache:
            return self._item_cache[source_name]

        result = self._resolve_entity_id(source_name, "Item")
        if result:
            self._item_cache[source_name] = result
        return result

    def _resolve_warehouse_id(self, source_name: str | None) -> uuid.UUID | None:
        """Resolve DotMac warehouse_id from ERPNext warehouse name."""
        if not source_name:
            return None

        if source_name in self._warehouse_cache:
            return self._warehouse_cache[source_name]

        result = self._resolve_entity_id(source_name, "Warehouse")
        if result:
            self._warehouse_cache[source_name] = result
        return result

    def _resolve_project_id(self, source_name: str | None) -> uuid.UUID | None:
        """Resolve DotMac project_id from ERPNext project name."""
        if not source_name:
            return None

        if source_name in self._project_cache:
            return self._project_cache[source_name]

        result = self._resolve_entity_id(source_name, "Project")
        if result:
            self._project_cache[source_name] = result
        return result

    def _resolve_ticket_id(self, source_name: str | None) -> uuid.UUID | None:
        """Resolve DotMac ticket_id from ERPNext Issue/HD Ticket name."""
        if not source_name:
            return None

        if source_name in self._ticket_cache:
            return self._ticket_cache[source_name]

        # Try Issue first, then HD Ticket
        result = self._resolve_entity_id(source_name, "Issue")
        if not result:
            result = self._resolve_entity_id(source_name, "HD Ticket")

        if result:
            self._ticket_cache[source_name] = result
        return result

    def _resolve_task_id(self, source_name: str | None) -> uuid.UUID | None:
        """Resolve DotMac task_id from ERPNext task name."""
        if not source_name:
            return None

        if source_name in self._task_cache:
            return self._task_cache[source_name]

        result = self._resolve_entity_id(source_name, "Task")
        if result:
            self._task_cache[source_name] = result
        return result

    def _resolve_employee_by_user(self, user_email: str | None) -> uuid.UUID | None:
        """
        Resolve employee_id from ERPNext user email.

        ERPNext stores requested_by as user ID (email).
        We look up the employee via their personal_email or company_email.
        """
        if not user_email:
            return None

        email_lower = user_email.lower()
        if email_lower in self._employee_by_user_cache:
            return self._employee_by_user_cache[email_lower]

        from sqlalchemy import func, or_

        from app.models.people.hr import Employee
        from app.models.person import Person

        result = self.db.execute(
            select(Employee)
            .join(Person, Person.id == Employee.person_id)
            .where(
                Employee.organization_id == self.organization_id,
                or_(
                    func.lower(Person.email) == email_lower,
                    func.lower(Employee.personal_email) == email_lower,
                ),
            )
        ).scalar_one_or_none()

        employee_id = result.employee_id if result else None
        self._employee_by_user_cache[email_lower] = employee_id
        return employee_id

    def _create_request_items(
        self, request: MaterialRequest, items_data: list[dict]
    ) -> None:
        """Create Material Request items."""
        for seq, item_data in enumerate(items_data, 1):
            # Pop FK source names
            item_source = item_data.pop("_item_source_name", None)
            warehouse_source = item_data.pop("_warehouse_source_name", None)
            project_source = item_data.pop("_project_source_name", None)
            item_data.pop("_source_modified", None)
            item_data.pop("_source_name", None)

            # Resolve FKs
            inventory_item_id = self._resolve_item_id(item_source)
            if not inventory_item_id:
                logger.warning(
                    "Could not resolve item %s for request %s, skipping item",
                    item_source,
                    request.request_number,
                )
                continue

            warehouse_id = self._resolve_warehouse_id(warehouse_source)
            project_id = self._resolve_project_id(project_source)

            # Create the item
            item = MaterialRequestItem(
                organization_id=self.organization_id,
                request_id=request.request_id,
                inventory_item_id=inventory_item_id,
                warehouse_id=warehouse_id,
                requested_qty=item_data.get("requested_qty", Decimal("0")),
                ordered_qty=item_data.get("ordered_qty", Decimal("0")),
                uom=item_data.get("uom"),
                schedule_date=item_data.get("schedule_date"),
                project_id=project_id,
                # ticket_id and task_id are not directly available in ERPNext MR
                # They would need to be set via custom fields or derived from project
                sequence=seq,
            )
            self.db.add(item)

    def create_entity(self, data: dict[str, Any]) -> MaterialRequest:
        """Create DotMac Material Request from transformed data."""
        # Pop FK source names
        warehouse_source = data.pop("_warehouse_source_name", None)
        requested_by_user = data.pop("_requested_by_user", None)
        items_data = data.pop("_items", [])
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        # Resolve FKs
        default_warehouse_id = self._resolve_warehouse_id(warehouse_source)
        requested_by_id = self._resolve_employee_by_user(requested_by_user)

        # Map enums
        request_type_str = data.get("request_type", "PURCHASE")
        try:
            request_type = MaterialRequestType(request_type_str)
        except ValueError:
            request_type = MaterialRequestType.PURCHASE

        status_str = data.get("status", "DRAFT")
        try:
            status = MaterialRequestStatus(status_str)
        except ValueError:
            status = MaterialRequestStatus.DRAFT

        request = MaterialRequest(
            organization_id=self.organization_id,
            request_number=data["request_number"][:50],
            request_type=request_type,
            status=status,
            schedule_date=data.get("schedule_date"),
            requested_by_id=requested_by_id,
            default_warehouse_id=default_warehouse_id,
            remarks=data.get("remarks"),
            # created_by_id not set for synced records
        )

        # Add request to session to get ID
        self.db.add(request)
        self.db.flush()

        # Create line items
        if items_data:
            self._create_request_items(request, items_data)

        return request

    def update_entity(
        self, entity: MaterialRequest, data: dict[str, Any]
    ) -> MaterialRequest:
        """Update existing Material Request with new data."""
        # Pop FK source names
        data.pop("_warehouse_source_name", None)
        data.pop("_requested_by_user", None)
        items_data = data.pop("_items", [])
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        # Update status
        status_str = data.get("status", "DRAFT")
        try:
            entity.status = MaterialRequestStatus(status_str)
        except ValueError:
            pass

        # Update other fields
        entity.schedule_date = data.get("schedule_date", entity.schedule_date)
        entity.remarks = data.get("remarks", entity.remarks)
        entity.updated_by_id = self.user_id

        # Update items (delete and recreate for simplicity)
        if items_data:
            # Delete existing items
            for item in entity.items:
                self.db.delete(item)
            self.db.flush()

            # Create new items
            self._create_request_items(entity, items_data)

        return entity

    def get_entity_id(self, entity: MaterialRequest) -> uuid.UUID:
        """Get the request ID."""
        return entity.request_id

    def find_existing_entity(self, source_name: str) -> MaterialRequest | None:
        """Find existing Material Request by sync record."""
        if source_name in self._request_cache:
            return self._request_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            request = self.db.get(MaterialRequest, sync_entity.target_id)
            if request:
                self._request_cache[source_name] = request
                return request

        return None
