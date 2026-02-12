"""
Project Sync Service - ERPNext to DotMac ERP.

Syncs ERPNext Project DocType to DotMac core_org.project.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.core_org.project import (
    Project,
    ProjectStatus,
)
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
        self._customer_cache: dict[str, uuid.UUID | None] = {}

    def fetch_records(self, client: Any, since: datetime | None = None):
        if since:
            yield from client.get_modified_since(
                doctype="Project",
                since=since,
            )
        else:
            yield from client.get_projects(include_completed=True)

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._mapping.transform_record(record)

    def _resolve_cost_center_id(self, source_name: str | None) -> uuid.UUID | None:
        """Resolve cost center ID from ERPNext cost center name via SyncEntity."""
        if not source_name:
            return None

        from app.models.sync import SyncEntity

        sync_entity = self.db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Cost Center",
                SyncEntity.source_name == source_name,
            )
        ).scalar_one_or_none()

        return sync_entity.target_id if sync_entity else None

    def _resolve_customer_id(self, source_name: str | None) -> uuid.UUID | None:
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
        from app.models.finance.core_org.project import ProjectPriority, ProjectType

        data.pop("_source_modified", None)
        data.pop("_source_name", None)
        data.pop("_company", None)
        cost_center_source = data.pop("_cost_center_source_name", None)
        customer_source = data.pop("_customer_source_name", None)

        # Resolve customer reference
        customer_id = self._resolve_customer_id(customer_source)
        if customer_source and not customer_id:
            logger.warning(
                "Could not resolve customer '%s' for project '%s' - customer may not be synced yet",
                customer_source,
                data.get("project_code"),
            )

        # Resolve cost center reference
        cost_center_id = self._resolve_cost_center_id(cost_center_source)

        # Map status
        status_str = data.get("status", "ACTIVE")
        try:
            status = ProjectStatus(status_str)
        except ValueError:
            status = ProjectStatus.ACTIVE

        # Map priority
        priority_str = data.pop("project_priority", "MEDIUM")
        try:
            priority = ProjectPriority(priority_str)
        except ValueError:
            priority = ProjectPriority.MEDIUM

        # Map project type
        type_str = data.pop("project_type", "INTERNAL")
        try:
            project_type = ProjectType(type_str)
        except ValueError:
            project_type = ProjectType.INTERNAL

        project = Project(
            organization_id=self.organization_id,
            project_code=data["project_code"][:20],
            project_name=data["project_name"][:200],
            description=data.get("description"),
            status=status,
            project_priority=priority,
            project_type=project_type,
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            budget_amount=data.get("budget_amount"),
            budget_currency_code=str(data.get("budget_currency_code") or "NGN")[:3],
            is_capitalizable=data.get("is_capitalizable", False),
            customer_id=customer_id,
            cost_center_id=cost_center_id,
            percent_complete=data.get("percent_complete") or Decimal("0.00"),
            actual_cost=data.get("actual_cost"),
        )
        return project

    def update_entity(self, entity: Project, data: dict[str, Any]) -> Project:
        from app.models.finance.core_org.project import ProjectPriority, ProjectType

        data.pop("_source_modified", None)
        data.pop("_source_name", None)
        data.pop("_company", None)
        cost_center_source = data.pop("_cost_center_source_name", None)
        customer_source = data.pop("_customer_source_name", None)

        entity.project_name = data["project_name"][:200]
        entity.description = data.get("description")
        entity.start_date = data.get("start_date")
        entity.end_date = data.get("end_date")
        entity.budget_amount = data.get("budget_amount")
        entity.budget_currency_code = str(
            data.get("budget_currency_code") or entity.budget_currency_code or "NGN"
        )[:3]

        # Update progress and cost tracking
        if data.get("percent_complete") is not None:
            entity.percent_complete = data["percent_complete"]
        if data.get("actual_cost") is not None:
            entity.actual_cost = data["actual_cost"]

        # Map status
        status_str = data.get("status", "ACTIVE")
        try:
            entity.status = ProjectStatus(status_str)
        except ValueError:
            pass

        # Map priority
        priority_str = data.pop("project_priority", None)
        if priority_str:
            try:
                entity.project_priority = ProjectPriority(priority_str)
            except ValueError:
                pass

        # Map project type
        type_str = data.pop("project_type", None)
        if type_str:
            try:
                entity.project_type = ProjectType(type_str)
            except ValueError:
                pass

        # Resolve cost center reference
        cost_center_id = self._resolve_cost_center_id(cost_center_source)
        if cost_center_id:
            entity.cost_center_id = cost_center_id

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

    def find_existing_entity(self, source_name: str) -> Project | None:
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
