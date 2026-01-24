"""
Project Sync Service - ERPNext to DotMac ERP.

Syncs ERPNext Project DocType to DotMac core_org.project.
"""
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.core_org.project import Project, ProjectStatus
from app.services.erpnext.mappings.projects import ProjectMapping

from .base import BaseSyncService

logger = logging.getLogger(__name__)


class ProjectSyncService(BaseSyncService[Project]):
    """Sync Projects from ERPNext."""

    source_doctype = "Project"
    target_table = "core_org.project"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = ProjectMapping()
        self._project_cache: dict[str, Project] = {}
        self._customer_cache: dict[str, Optional[uuid.UUID]] = {}

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        if since:
            yield from client.get_modified_since(
                doctype="Project",
                since=since,
            )
        else:
            yield from client.get_projects(include_completed=True)

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._mapping.transform_record(record)

    def _resolve_customer_id(self, source_name: Optional[str]) -> Optional[uuid.UUID]:
        """Resolve customer ID from ERPNext customer name."""
        if not source_name:
            return None

        # Check cache first
        if source_name in self._customer_cache:
            return self._customer_cache[source_name]

        from app.models.sync import SyncEntity

        sync_entity = self.db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Customer",
                SyncEntity.source_name == source_name,
            )
        ).scalar_one_or_none()

        customer_id = sync_entity.target_id if sync_entity else None
        self._customer_cache[source_name] = customer_id
        return customer_id

    def create_entity(self, data: dict[str, Any]) -> Project:
        data.pop("_source_modified", None)
        data.pop("_source_name", None)
        data.pop("_company", None)
        data.pop("_cost_center_source_name", None)
        customer_source = data.pop("_customer_source_name", None)

        # Resolve customer reference
        customer_id = self._resolve_customer_id(customer_source)
        if customer_source and not customer_id:
            logger.warning(
                "Could not resolve customer '%s' for project '%s' - customer may not be synced yet",
                customer_source,
                data.get("project_code"),
            )

        # Map status
        status_str = data.get("status", "ACTIVE")
        try:
            status = ProjectStatus(status_str)
        except ValueError:
            status = ProjectStatus.ACTIVE

        project = Project(
            organization_id=self.organization_id,
            project_code=data["project_code"][:20],
            project_name=data["project_name"][:200],
            status=status,
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            budget_amount=data.get("budget_amount"),
            budget_currency_code=data.get("budget_currency_code", "NGN")[:3],
            is_capitalizable=data.get("is_capitalizable", False),
            customer_id=customer_id,
        )
        return project

    def update_entity(self, entity: Project, data: dict[str, Any]) -> Project:
        data.pop("_source_modified", None)
        data.pop("_source_name", None)
        data.pop("_company", None)
        data.pop("_cost_center_source_name", None)
        customer_source = data.pop("_customer_source_name", None)

        entity.project_name = data["project_name"][:200]
        entity.start_date = data.get("start_date")
        entity.end_date = data.get("end_date")
        entity.budget_amount = data.get("budget_amount")
        entity.budget_currency_code = data.get("budget_currency_code", entity.budget_currency_code)[:3]

        # Map status
        status_str = data.get("status", "ACTIVE")
        try:
            entity.status = ProjectStatus(status_str)
        except ValueError:
            pass

        # Resolve customer reference
        if customer_source:
            customer_id = self._resolve_customer_id(customer_source)
            if customer_id:
                entity.customer_id = customer_id
            else:
                logger.warning(
                    "Could not resolve customer '%s' for project '%s'",
                    customer_source,
                    entity.project_code,
                )
        elif entity.customer_id:
            # Customer was removed in ERPNext
            entity.customer_id = None

        return entity

    def get_entity_id(self, entity: Project) -> uuid.UUID:
        return entity.project_id

    def find_existing_entity(self, source_name: str) -> Optional[Project]:
        if source_name in self._project_cache:
            return self._project_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            project = self.db.get(Project, sync_entity.target_id)
            if project:
                self._project_cache[source_name] = project
                return project

        # Fallback: try to find by project code
        result = self.db.execute(
            select(Project).where(
                Project.organization_id == self.organization_id,
                Project.project_code == source_name[:20],
            )
        ).scalar_one_or_none()

        if result:
            self._project_cache[source_name] = result
            return result

        return None
