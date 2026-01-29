"""
CRM Project Sync Service.

Syncs projects from CRM to DotMac ERP core_org.project table.
Handles create/update detection and field mapping.
"""

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Generator, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.core_org.project import (
    Project,
    ProjectPriority,
    ProjectStatus,
    ProjectType,
)
from app.services.crm.client import CRMClient
from app.services.crm.sync.base import BaseCRMSyncService

logger = logging.getLogger(__name__)


# CRM status to ERP status mapping
CRM_PROJECT_STATUS_MAP: dict[str, ProjectStatus] = {
    "planning": ProjectStatus.PLANNING,
    "Planning": ProjectStatus.PLANNING,
    "PLANNING": ProjectStatus.PLANNING,
    "draft": ProjectStatus.PLANNING,
    "Draft": ProjectStatus.PLANNING,
    "active": ProjectStatus.ACTIVE,
    "Active": ProjectStatus.ACTIVE,
    "ACTIVE": ProjectStatus.ACTIVE,
    "in_progress": ProjectStatus.ACTIVE,
    "in-progress": ProjectStatus.ACTIVE,
    "In Progress": ProjectStatus.ACTIVE,
    "on_hold": ProjectStatus.ON_HOLD,
    "on-hold": ProjectStatus.ON_HOLD,
    "On Hold": ProjectStatus.ON_HOLD,
    "ON_HOLD": ProjectStatus.ON_HOLD,
    "paused": ProjectStatus.ON_HOLD,
    "Paused": ProjectStatus.ON_HOLD,
    "completed": ProjectStatus.COMPLETED,
    "Completed": ProjectStatus.COMPLETED,
    "COMPLETED": ProjectStatus.COMPLETED,
    "done": ProjectStatus.COMPLETED,
    "Done": ProjectStatus.COMPLETED,
    "cancelled": ProjectStatus.CANCELLED,
    "Cancelled": ProjectStatus.CANCELLED,
    "CANCELLED": ProjectStatus.CANCELLED,
    "canceled": ProjectStatus.CANCELLED,
}

# CRM priority to ERP priority mapping
CRM_PROJECT_PRIORITY_MAP: dict[str, ProjectPriority] = {
    "low": ProjectPriority.LOW,
    "Low": ProjectPriority.LOW,
    "LOW": ProjectPriority.LOW,
    "medium": ProjectPriority.MEDIUM,
    "Medium": ProjectPriority.MEDIUM,
    "MEDIUM": ProjectPriority.MEDIUM,
    "normal": ProjectPriority.MEDIUM,
    "Normal": ProjectPriority.MEDIUM,
    "high": ProjectPriority.HIGH,
    "High": ProjectPriority.HIGH,
    "HIGH": ProjectPriority.HIGH,
    "critical": ProjectPriority.CRITICAL,
    "Critical": ProjectPriority.CRITICAL,
    "CRITICAL": ProjectPriority.CRITICAL,
    "urgent": ProjectPriority.CRITICAL,
    "Urgent": ProjectPriority.CRITICAL,
}

# CRM project type mapping
CRM_PROJECT_TYPE_MAP: dict[str, ProjectType] = {
    "internal": ProjectType.INTERNAL,
    "Internal": ProjectType.INTERNAL,
    "INTERNAL": ProjectType.INTERNAL,
    "client": ProjectType.CLIENT,
    "Client": ProjectType.CLIENT,
    "CLIENT": ProjectType.CLIENT,
    "customer": ProjectType.CLIENT,
    "fixed_price": ProjectType.FIXED_PRICE,
    "fixed-price": ProjectType.FIXED_PRICE,
    "Fixed Price": ProjectType.FIXED_PRICE,
    "time_material": ProjectType.TIME_MATERIAL,
    "time-material": ProjectType.TIME_MATERIAL,
    "Time & Material": ProjectType.TIME_MATERIAL,
    "T&M": ProjectType.TIME_MATERIAL,
    # ISP-specific project types
    "fiber_installation": ProjectType.FIBER_OPTICS_INSTALLATION,
    "fiber_optics_installation": ProjectType.FIBER_OPTICS_INSTALLATION,
    "FTTH Installation": ProjectType.FIBER_OPTICS_INSTALLATION,
    "air_fiber_installation": ProjectType.AIR_FIBER_INSTALLATION,
    "Air Fiber Installation": ProjectType.AIR_FIBER_INSTALLATION,
    "cable_rerun": ProjectType.CABLE_RERUN,
    "Cable Rerun": ProjectType.CABLE_RERUN,
    "fiber_relocation": ProjectType.FIBER_OPTICS_RELOCATION,
    "Fiber Relocation": ProjectType.FIBER_OPTICS_RELOCATION,
    "air_fiber_relocation": ProjectType.AIR_FIBER_RELOCATION,
    "Air Fiber Relocation": ProjectType.AIR_FIBER_RELOCATION,
}


class ProjectSyncService(BaseCRMSyncService[Project]):
    """
    Sync projects from CRM to ERP.

    Maps CRM project fields to DotMac Project model.
    """

    source_entity_type = "project"
    target_table = "core_org.project"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None,
    ):
        super().__init__(db, organization_id, user_id)

    def fetch_records(
        self,
        client: CRMClient,
        since: Optional[datetime] = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Fetch projects from CRM API."""
        yield from client.get_projects(since=since)

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """
        Transform CRM project record to ERP format.

        Args:
            record: Raw CRM project data

        Returns:
            Dict ready for Project model creation
        """
        # Parse dates
        start_date = self._parse_date(record.get("start_date"))
        end_date = self._parse_date(record.get("end_date") or record.get("due_date"))

        # Get project code - prefer CRM's code, fallback to ID
        project_code = (
            record.get("project_code")
            or record.get("code")
            or str(record.get("id", ""))[:20]
        )

        # Parse budget
        budget_amount = self._parse_decimal(record.get("budget"))
        budget_currency = record.get("currency") or record.get("budget_currency")

        # Calculate percent complete
        percent_complete = self._parse_decimal(
            record.get("percent_complete") or record.get("progress") or 0
        )

        return {
            "organization_id": self.organization_id,
            "project_code": project_code,
            "project_name": record.get("name") or record.get("title") or "Untitled",
            "description": record.get("description"),
            "status": self._map_status(record.get("status")),
            "project_priority": self._map_priority(record.get("priority")),
            "project_type": self._map_type(record.get("project_type") or record.get("type")),
            "start_date": start_date,
            "end_date": end_date,
            "budget_amount": budget_amount,
            "budget_currency_code": budget_currency,
            "percent_complete": percent_complete,
            # Customer/subscriber handled separately
            "_subscriber_id": record.get("subscriber_id"),
        }

    def create_entity(self, data: dict[str, Any]) -> Project:
        """Create a new Project entity."""
        # Remove internal fields
        subscriber_id = data.pop("_subscriber_id", None)

        project = Project(**data)

        # Look up customer by subscriber ID
        if subscriber_id:
            customer = self._lookup_customer_by_subscriber(subscriber_id)
            if customer:
                project.customer_id = customer.customer_id

        return project

    def update_entity(self, entity: Project, data: dict[str, Any]) -> Project:
        """Update existing Project entity."""
        # Remove internal fields
        subscriber_id = data.pop("_subscriber_id", None)

        # Update fields
        for key, value in data.items():
            if key != "organization_id" and hasattr(entity, key):
                setattr(entity, key, value)

        # Update customer if subscriber changed
        if subscriber_id:
            customer = self._lookup_customer_by_subscriber(subscriber_id)
            if customer:
                entity.customer_id = customer.customer_id

        return entity

    def get_entity_id(self, entity: Project) -> uuid.UUID:
        """Get primary key from Project."""
        return entity.project_id

    def get_existing_entity(self, sync_entity) -> Optional[Project]:
        """Look up existing project by sync entity's target_id."""
        if not sync_entity.target_id:
            return None
        return self.db.get(Project, sync_entity.target_id)

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _map_status(self, status: Optional[str]) -> ProjectStatus:
        """Map CRM status to ERP status."""
        if not status:
            return ProjectStatus.ACTIVE
        return CRM_PROJECT_STATUS_MAP.get(status, ProjectStatus.ACTIVE)

    def _map_priority(self, priority: Optional[str]) -> ProjectPriority:
        """Map CRM priority to ERP priority."""
        if not priority:
            return ProjectPriority.MEDIUM
        return CRM_PROJECT_PRIORITY_MAP.get(priority, ProjectPriority.MEDIUM)

    def _map_type(self, project_type: Optional[str]) -> ProjectType:
        """Map CRM project type to ERP type."""
        if not project_type:
            return ProjectType.CLIENT
        return CRM_PROJECT_TYPE_MAP.get(project_type, ProjectType.CLIENT)

    def _parse_date(self, value: Any) -> Optional[date]:
        """Parse date from CRM value."""
        if not value:
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, datetime):
            return value.date()
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt.date()
        except (ValueError, AttributeError):
            return None

    def _parse_decimal(self, value: Any) -> Decimal:
        """Parse decimal from CRM value."""
        if not value:
            return Decimal("0")
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal("0")

    def _lookup_customer_by_subscriber(self, subscriber_id: str) -> Optional[Any]:
        """Look up customer by CRM subscriber ID."""
        from app.models.finance.ar.customer import Customer

        stmt = select(Customer).where(
            Customer.organization_id == self.organization_id,
            Customer.customer_code == subscriber_id,
        )
        return self.db.scalar(stmt)

    # =========================================================================
    # Project-Specific Operations
    # =========================================================================

    def get_by_crm_id(self, crm_project_id: str) -> Optional[Project]:
        """
        Get ERP project by CRM project ID.

        Useful for linking expenses/tickets to synced projects.
        """
        from app.models.sync import SyncEntity

        from .base import CRM_SOURCE_SYSTEM

        sync_entity = self.db.scalar(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == CRM_SOURCE_SYSTEM,
                SyncEntity.source_doctype == self.source_entity_type,
                SyncEntity.source_name == crm_project_id,
            )
        )

        if sync_entity and sync_entity.target_id:
            return self.db.get(Project, sync_entity.target_id)
        return None

    def link_tickets_to_project(self, crm_project_id: str) -> int:
        """
        Link all tickets to a project after both are synced.

        Returns count of tickets linked.
        """
        from app.models.support.ticket import Ticket
        from app.models.sync import SyncEntity

        from .base import CRM_SOURCE_SYSTEM

        # Get ERP project
        project = self.get_by_crm_id(crm_project_id)
        if not project:
            return 0

        # Find all tickets that reference this project in CRM
        # This requires the ticket sync to store the CRM project_id reference
        # For now, just log - full implementation depends on CRM ticket data structure
        logger.info(
            "Project %s synced, would link related tickets",
            project.project_code,
        )

        return 0
