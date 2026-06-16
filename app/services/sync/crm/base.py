"""Shared base for the CRM sync mixins: session handle, mapping-store
primitives, and the cross-domain entity resolvers.

Extracted from the former monolithic dotmac_crm_sync_service.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


if TYPE_CHECKING:
    from app.models.finance.ap.supplier import Supplier  # noqa: F401

from app.models.finance.core_org.project import Project, ProjectStatus, ProjectType
from app.models.people.hr.employee import Employee
from app.models.person import Person
from app.models.sync.dotmac_crm_sync import (
    CRMEntityType,
    CRMSyncMapping,
    CRMSyncStatus,
)

# CRM → ERP translation policy lives in crm_mappings (pure, side-effect-free).
# Re-imported here so the canonical import sites
# (`from ...dotmac_crm_sync_service import PROJECT_STATUS_MAP`) and the in-class
# references keep resolving against this module's namespace.
from app.services.sync.crm_mappings import (  # noqa: E402
    CRM_SYNC_STATUS_MAP,
)

logger = logging.getLogger(__name__)


class _CRMSyncBase:
    def __init__(self, db: Session):
        self.db = db

    def get_local_project_id(self, org_id: UUID, crm_id: str) -> UUID | None:
        """Get local project ID for a CRM project."""
        mapping = self._get_mapping(org_id, CRMEntityType.PROJECT, crm_id)
        return mapping.local_entity_id if mapping else None

    def get_local_ticket_id(self, org_id: UUID, crm_id: str) -> UUID | None:
        """Get local ticket ID for a CRM ticket."""
        mapping = self._get_mapping(org_id, CRMEntityType.TICKET, crm_id)
        return mapping.local_entity_id if mapping else None

    def get_local_task_id(self, org_id: UUID, crm_id: str) -> UUID | None:
        """Get local task ID for a CRM work order."""
        mapping = self._get_mapping(org_id, CRMEntityType.WORK_ORDER, crm_id)
        return mapping.local_entity_id if mapping else None

    def _get_mapping(
        self,
        org_id: UUID,
        entity_type: CRMEntityType,
        crm_id: str,
    ) -> CRMSyncMapping | None:
        """Get CRM sync mapping by org, type, and CRM ID."""
        stmt = select(CRMSyncMapping).where(
            CRMSyncMapping.organization_id == org_id,
            CRMSyncMapping.crm_entity_type == entity_type,
            CRMSyncMapping.crm_id == crm_id,
        )
        return self.db.scalar(stmt)

    def _batch_get_mappings(
        self,
        org_id: UUID,
        entity_type: CRMEntityType,
        crm_ids: list[str],
    ) -> dict[str, UUID]:
        """Get multiple CRM sync mappings in a single query.

        Returns:
            Dict mapping crm_id -> local_entity_id
        """
        if not crm_ids:
            return {}
        stmt = select(CRMSyncMapping.crm_id, CRMSyncMapping.local_entity_id).where(
            CRMSyncMapping.organization_id == org_id,
            CRMSyncMapping.crm_entity_type == entity_type,
            CRMSyncMapping.crm_id.in_(crm_ids),
        )
        rows = self.db.execute(stmt).all()
        return {crm_id: local_id for crm_id, local_id in rows}

    def _update_mapping(
        self,
        mapping: CRMSyncMapping,
        display_name: str,
        display_code: str | None,
        customer_name: str | None,
        status: str,
        crm_data: dict | None = None,
    ) -> None:
        """Update mapping fields on sync."""
        mapping.display_name = display_name
        mapping.display_code = display_code
        mapping.customer_name = customer_name
        mapping.crm_status = CRM_SYNC_STATUS_MAP.get(
            status.lower(), CRMSyncStatus.ACTIVE
        )
        mapping.synced_at = datetime.now(UTC)
        if crm_data is not None:
            mapping.crm_data = crm_data

    def _generate_unique_code(self, prefix: str, crm_id: str, max_len: int = 20) -> str:
        """Generate a unique code from CRM ID using hash to avoid collisions."""
        # Use hash of full CRM ID for uniqueness, take enough chars to fit max_len
        hash_suffix = hashlib.sha256(crm_id.encode()).hexdigest()[
            : max_len - len(prefix) - 1
        ]
        return f"{prefix}-{hash_suffix.upper()}"

    def _resolve_project_id(self, org_id: UUID, crm_id: str | None) -> UUID | None:
        """Resolve CRM project ID to local project ID."""
        if not crm_id:
            return None
        mapping = self._get_mapping(org_id, CRMEntityType.PROJECT, crm_id)
        return mapping.local_entity_id if mapping else None

    def _resolve_ticket_id(self, org_id: UUID, crm_id: str | None) -> UUID | None:
        """Resolve CRM ticket ID to local ticket ID."""
        if not crm_id:
            return None
        mapping = self._get_mapping(org_id, CRMEntityType.TICKET, crm_id)
        return mapping.local_entity_id if mapping else None

    def _resolve_employee_id(self, org_id: UUID, email: str | None) -> UUID | None:
        """Resolve employee email to employee ID.

        Looks up by person.email (work email) or employee.personal_email.
        """
        if not email:
            return None
        email_lower = email.lower()

        # First try via Person.email (work email)
        stmt = (
            select(Employee.employee_id)
            .join(Person, Employee.person_id == Person.id)
            .where(
                Employee.organization_id == org_id,
                func.lower(Person.email) == email_lower,
            )
        )
        result = self.db.scalar(stmt)
        if result:
            return result

        # Fallback to personal_email
        stmt = select(Employee.employee_id).where(
            Employee.organization_id == org_id,
            func.lower(Employee.personal_email) == email_lower,
        )
        return self.db.scalar(stmt)

    def _get_or_create_default_project(self, org_id: UUID) -> UUID:
        """Get or create a default project for orphan work orders.

        Handles race condition by catching IntegrityError on duplicate insert.
        Uses a savepoint to avoid rolling back the entire transaction.
        """

        stmt = select(Project).where(
            Project.organization_id == org_id,
            Project.project_code == "CRM-DEFAULT",
        )
        project = self.db.scalar(stmt)

        if project:
            return project.project_id

        # Try to create inside savepoint so failure doesn't roll back the
        # outer transaction (e.g. an in-progress bulk sync batch).
        savepoint = self.db.begin_nested()
        try:
            project = Project(
                organization_id=org_id,
                project_code="CRM-DEFAULT",
                project_name="CRM Work Orders (Unassigned)",
                description="Default project for CRM work orders without a project assignment",
                status=ProjectStatus.ACTIVE,
                project_type=ProjectType.INTERNAL,
            )
            self.db.add(project)
            self.db.flush()
            savepoint.commit()
            return project.project_id
        except IntegrityError:
            # Race condition - another request created it
            savepoint.rollback()
            project = self.db.scalar(stmt)
            if project:
                return project.project_id
            raise  # Re-raise if still not found (unexpected)
