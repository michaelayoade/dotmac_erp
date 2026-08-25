"""Shared primitives for Dotmac Sub observations.

``sync.sync_entity`` is the sole correlation ledger for Sub-owned operational
projections. Domain-owned intake records keep their own correlation columns.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_kernel.db import conflict_savepoint

from app.models.finance.core_org.project import Project, ProjectStatus, ProjectType
from app.models.people.hr.employee import Employee
from app.models.person import Person
from app.models.sync.sync_entity import SyncEntity, SyncStatus

SUB_SOURCE_SYSTEM = "sub"
SUB_PROJECT = "project"
SUB_PROJECT_TASK = "project_task"
SUB_WORK_ORDER = "work_order"


class _SubSyncBase:
    def __init__(self, db: Session):
        self.db = db

    def _get_sync(
        self,
        organization_id: UUID,
        source_doctype: str,
        source_id: str,
    ) -> SyncEntity | None:
        return self.db.scalar(
            select(SyncEntity).where(
                SyncEntity.organization_id == organization_id,
                SyncEntity.source_system == SUB_SOURCE_SYSTEM,
                SyncEntity.source_doctype == source_doctype,
                SyncEntity.source_name == source_id,
            )
        )

    def _record_sync(
        self,
        organization_id: UUID,
        source_doctype: str,
        source_id: str,
        *,
        target_table: str,
        target_id: UUID,
    ) -> SyncEntity:
        sync = self._get_sync(organization_id, source_doctype, source_id)
        if sync is None:
            sync = SyncEntity(
                organization_id=organization_id,
                source_system=SUB_SOURCE_SYSTEM,
                source_doctype=source_doctype,
                source_name=source_id,
                target_table=target_table,
                target_id=target_id,
                sync_status=SyncStatus.SYNCED,
            )
            self.db.add(sync)
        else:
            sync.target_table = target_table
            sync.mark_synced(target_id)
        return sync

    def _resolve_project_id(
        self, organization_id: UUID, source_id: str | None
    ) -> UUID | None:
        if not source_id:
            return None
        sync = self._get_sync(organization_id, SUB_PROJECT, source_id)
        return sync.target_id if sync else None

    def _resolve_project_task_id(
        self, organization_id: UUID, source_id: str | None
    ) -> UUID | None:
        if not source_id:
            return None
        sync = self._get_sync(organization_id, SUB_PROJECT_TASK, source_id)
        return sync.target_id if sync else None

    def _resolve_employee_id(
        self, organization_id: UUID, email: str | None
    ) -> UUID | None:
        if not email:
            return None
        normalized = email.lower()
        employee_id = self.db.scalar(
            select(Employee.employee_id)
            .join(Person, Employee.person_id == Person.id)
            .where(
                Employee.organization_id == organization_id,
                func.lower(Person.email) == normalized,
            )
        )
        if employee_id:
            return employee_id
        return self.db.scalar(
            select(Employee.employee_id).where(
                Employee.organization_id == organization_id,
                func.lower(Employee.personal_email) == normalized,
            )
        )

    @staticmethod
    def _generate_unique_code(prefix: str, source_id: str, max_len: int = 20) -> str:
        suffix = hashlib.sha256(source_id.encode()).hexdigest()[
            : max_len - len(prefix) - 1
        ]
        return f"{prefix}-{suffix.upper()}"

    def _get_or_create_default_project(self, organization_id: UUID) -> UUID:
        stmt = select(Project).where(
            Project.organization_id == organization_id,
            Project.project_code == "SUB-DEFAULT",
        )
        project = self.db.scalar(stmt)
        if project:
            return project.project_id

        try:
            with conflict_savepoint(self.db):
                project = Project(
                    organization_id=organization_id,
                    project_code="SUB-DEFAULT",
                    project_name="Sub work orders (unassigned)",
                    description=(
                        "Default project for Sub work orders without a project"
                    ),
                    status=ProjectStatus.ACTIVE,
                    project_type=ProjectType.INTERNAL,
                )
                self.db.add(project)
                self.db.flush()
            return project.project_id
        except IntegrityError:
            project = self.db.scalar(stmt)
            if project:
                return project.project_id
            raise
