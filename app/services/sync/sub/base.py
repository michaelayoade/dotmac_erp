"""Shared base for the Sub sync mixins: session handle, mapping-store
primitives, and the cross-domain entity resolvers.

Extracted from the former monolithic dotmac_sub_sync_service.
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
from app.models.sync.source_correlation import (
    SourceEntityType,
    SourceCorrelation,
    SourceCorrelationStatus,
)

# Sub → ERP translation policy lives in sub_mappings (pure, side-effect-free).
# Re-imported here so the canonical import sites
# (`from ...dotmac_sub_sync_service import PROJECT_STATUS_MAP`) and the in-class
# references keep resolving against this module's namespace.
from app.services.sync.sub_mappings import (  # noqa: E402
    SOURCE_STATUS_MAP,
)

logger = logging.getLogger(__name__)


class _SubSyncBase:
    _SOURCE_APPLICATION = "sub"

    def __init__(self, db: Session):
        self.db = db

    def get_local_project_id(self, org_id: UUID, source_reference: str) -> UUID | None:
        """Get local project ID for a Sub project."""
        mapping = self._get_mapping(org_id, SourceEntityType.PROJECT, source_reference)
        return mapping.local_entity_id if mapping else None

    def get_local_ticket_id(self, org_id: UUID, source_reference: str) -> UUID | None:
        """Get local ticket ID for a Sub ticket."""
        mapping = self._get_mapping(org_id, SourceEntityType.TICKET, source_reference)
        return mapping.local_entity_id if mapping else None

    def get_local_task_id(self, org_id: UUID, source_reference: str) -> UUID | None:
        """Get local task ID for a Sub work order."""
        mapping = self._get_mapping(org_id, SourceEntityType.WORK_ORDER, source_reference)
        return mapping.local_entity_id if mapping else None

    def _get_mapping(
        self,
        org_id: UUID,
        entity_type: SourceEntityType,
        source_reference: str,
    ) -> SourceCorrelation | None:
        """Get Sub sync mapping by org, type, and Sub ID."""
        stmt = select(SourceCorrelation).where(
            SourceCorrelation.organization_id == org_id,
            SourceCorrelation.source_application == self._SOURCE_APPLICATION,
            SourceCorrelation.source_entity_type == entity_type,
            SourceCorrelation.source_reference == source_reference,
        )
        mapping = self.db.scalar(stmt)
        if mapping is not None:
            return mapping

        # The retired shared table carried no source discriminator.  A current,
        # authenticated Sub observation is the first safe evidence that can
        # adopt the exact legacy correlation; no offline backfill guesses.
        legacy = self.db.scalar(
            select(SourceCorrelation).where(
                SourceCorrelation.organization_id == org_id,
                SourceCorrelation.source_application == "legacy_unknown",
                SourceCorrelation.source_entity_type == entity_type,
                SourceCorrelation.source_reference == source_reference,
            )
        )
        if legacy is not None:
            legacy.source_application = self._SOURCE_APPLICATION
            self.db.flush()
        return legacy

    def _batch_get_mappings(
        self,
        org_id: UUID,
        entity_type: SourceEntityType,
        source_references: list[str],
    ) -> dict[str, UUID]:
        """Get multiple Sub sync mappings in a single query.

        Returns:
            Dict mapping source_reference -> local_entity_id
        """
        if not source_references:
            return {}
        rows = self.db.scalars(
            select(SourceCorrelation).where(
                SourceCorrelation.organization_id == org_id,
                SourceCorrelation.source_application.in_(
                    (self._SOURCE_APPLICATION, "legacy_unknown")
                ),
                SourceCorrelation.source_entity_type == entity_type,
                SourceCorrelation.source_reference.in_(source_references),
            )
        ).all()
        resolved: dict[str, UUID] = {}
        for row in sorted(
            rows,
            key=lambda value: value.source_application == "legacy_unknown",
        ):
            if row.source_reference in resolved:
                continue
            if row.source_application == "legacy_unknown":
                row.source_application = self._SOURCE_APPLICATION
            resolved[row.source_reference] = row.local_entity_id
        self.db.flush()
        return resolved

    def _update_mapping(
        self,
        mapping: SourceCorrelation,
        display_name: str,
        display_code: str | None,
        customer_name: str | None,
        status: str,
        source_payload: dict | None = None,
    ) -> None:
        """Update mapping fields on sync."""
        mapping.display_name = display_name
        mapping.display_code = display_code
        mapping.customer_name = customer_name
        mapping.source_status = SOURCE_STATUS_MAP.get(
            status.lower(), SourceCorrelationStatus.ACTIVE
        )
        mapping.synced_at = datetime.now(UTC)
        if source_payload is not None:
            mapping.source_payload = source_payload

    def _generate_unique_code(self, prefix: str, source_reference: str, max_len: int = 20) -> str:
        """Generate a unique code from Sub ID using hash to avoid collisions."""
        # Use hash of full Sub ID for uniqueness, take enough chars to fit max_len
        hash_suffix = hashlib.sha256(source_reference.encode()).hexdigest()[
            : max_len - len(prefix) - 1
        ]
        return f"{prefix}-{hash_suffix.upper()}"

    def _resolve_project_id(self, org_id: UUID, source_reference: str | None) -> UUID | None:
        """Resolve Sub project ID to local project ID."""
        if not source_reference:
            return None
        mapping = self._get_mapping(org_id, SourceEntityType.PROJECT, source_reference)
        return mapping.local_entity_id if mapping else None

    def _resolve_ticket_id(self, org_id: UUID, source_reference: str | None) -> UUID | None:
        """Resolve Sub ticket ID to local ticket ID."""
        if not source_reference:
            return None
        mapping = self._get_mapping(org_id, SourceEntityType.TICKET, source_reference)
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
            Project.project_code == "Sub-DEFAULT",
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
                project_code="Sub-DEFAULT",
                project_name="Sub Work Orders (Unassigned)",
                description="Default project for Sub work orders without a project assignment",
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
