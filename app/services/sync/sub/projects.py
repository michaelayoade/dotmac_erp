"""Project / ticket / work-order (task) synchronization from Dotmac Sub.

Extracted from the former monolithic dotmac_sub_sync_service.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select


if TYPE_CHECKING:
    from app.models.finance.ap.supplier import Supplier  # noqa: F401

from app.models.finance.core_org.project import Project, ProjectStatus, ProjectType
from app.models.people.hr.employee import Employee
from app.models.person import Person
from app.models.pm.task import Task, TaskPriority, TaskStatus
from app.models.support.comment import CommentType, TicketComment
from app.models.support.ticket import Ticket, TicketPriority, TicketStatus
from app.models.sync.source_correlation import (
    SourceEntityType,
    SourceCorrelation,
    SourceCorrelationStatus,
)
from app.models.sync.sync_entity import SyncEntity, SyncStatus
from app.schemas.sync.sub_operational import (
    SubProjectPayload,
    SubProjectTaskPayload,
    SubProjectRead,
    SubTicketActivityEntry,
    SubTicketCommentItem,
    SubTicketPayload,
    SubTicketRead,
    SubWorkOrderPayload,
    SubWorkOrderRead,
)

# Sub → ERP translation policy lives in sub_mappings (pure, side-effect-free).
# Re-imported here so the canonical import sites
# (`from ...dotmac_sub_sync_service import PROJECT_STATUS_MAP`) and the in-class
# references keep resolving against this module's namespace.
from app.services.sync.sub_mappings import (  # noqa: E402
    SOURCE_STATUS_MAP,
    PROJECT_STATUS_MAP,
    TASK_STATUS_MAP,
    TICKET_STATUS_MAP,
    map_project_type,
    map_task_priority,
    map_ticket_priority,
)

from app.services.sync.sub.base import _SubSyncBase

logger = logging.getLogger(__name__)

# Orphan-reconciliation guards — the exact rails proven in dotmac_sub
# ``app/services/selfcare.py::_reconcile_selfcare_orphans``: skip when the
# reported id set looks suspiciously small (likely a partial fetch/outage on
# the Sub side) or when too large a fraction of the known mappings would be
# closed in one run — failing safe rather than mass-closing live entities.
_ORPHAN_MIN_FETCH_RATIO = 0.5
_ORPHAN_MAX_TERMINATE_RATIO = 0.2
# Small absolute floor for tiny bases (so a single legitimate deletion isn't
# blocked) — kept low so it can't bypass the ratio guard and wipe a small base.
_ORPHAN_MAX_TERMINATE_FLOOR = 3

# Reconcile entity_type strings (Sub wire values) -> mapping entity types.
_RECONCILE_ENTITY_TYPES = {
    "project": SourceEntityType.PROJECT,
    "ticket": SourceEntityType.TICKET,
    "work_order": SourceEntityType.WORK_ORDER,
}


class _ProjectSyncMixin(_SubSyncBase):
    _SUB_PROJECT_TASK_SOURCE = "sub_project_task"

    def sync_project_task(
        self, org_id: UUID, data: SubProjectTaskPayload
    ) -> SyncEntity:
        """Idempotently project one Sub project task into ERP PM."""
        project_id = self._resolve_project_id(org_id, data.project_source_id)
        if project_id is None:
            raise ValueError(
                f"project source mapping not found: {data.project_source_id}"
            )
        parent_task_id = self._resolve_sub_project_task_id(
            org_id, data.parent_task_source_id
        )
        ticket_id = self._resolve_ticket_id(org_id, data.ticket_source_id)
        sync = self.db.scalar(
            select(SyncEntity).where(
                SyncEntity.organization_id == org_id,
                SyncEntity.source_system == "dotmac_sub",
                SyncEntity.source_doctype == self._SUB_PROJECT_TASK_SOURCE,
                SyncEntity.source_name == data.source_id,
            )
        )
        task = self.db.get(Task, sync.target_id) if sync and sync.target_id else None
        if task is None:
            task = Task(
                organization_id=org_id,
                project_id=project_id,
                task_code=self._generate_unique_code("PT", data.source_id, max_len=30),
                task_name=data.title,
            )
            self.db.add(task)
            self.db.flush()
        task.project_id = project_id
        task.parent_task_id = parent_task_id
        task.ticket_id = ticket_id
        task.task_name = data.title
        task.description = data.description
        task.status = TASK_STATUS_MAP.get(data.status.lower(), TaskStatus.OPEN)
        task.priority = self._map_task_priority(data.priority)
        task.start_date = data.start_at.date() if data.start_at else None
        task.due_date = data.due_at.date() if data.due_at else None
        task.actual_end_date = data.completed_at.date() if data.completed_at else None
        task.estimated_hours = data.effort_hours
        task.progress_percent = 100 if task.status == TaskStatus.COMPLETED else 0
        if sync is None:
            sync = SyncEntity(
                organization_id=org_id,
                source_system="dotmac_sub",
                source_doctype=self._SUB_PROJECT_TASK_SOURCE,
                source_name=data.source_id,
                target_table="pm.task",
                target_id=task.task_id,
                sync_status=SyncStatus.SYNCED,
            )
            self.db.add(sync)
        else:
            sync.target_table = "pm.task"
            sync.mark_synced(task.task_id)
        logger.info("Synced Sub project task %s -> %s", data.source_id, task.task_id)
        return sync

    def _resolve_sub_project_task_id(
        self, org_id: UUID, source_id: str | None
    ) -> UUID | None:
        if not source_id:
            return None
        sync = self.db.scalar(
            select(SyncEntity).where(
                SyncEntity.organization_id == org_id,
                SyncEntity.source_system == "dotmac_sub",
                SyncEntity.source_doctype == self._SUB_PROJECT_TASK_SOURCE,
                SyncEntity.source_name == source_id,
            )
        )
        if sync is None or sync.target_id is None:
            raise ValueError(f"parent task source mapping not found: {source_id}")
        return sync.target_id

    def sync_project(
        self,
        org_id: UUID,
        data: SubProjectPayload,
    ) -> SourceCorrelation:
        """
        Sync a project from Sub to ERP.

        Creates or updates both the local Project and the SourceCorrelation.
        """
        # Check if mapping exists
        mapping = self._get_mapping(org_id, SourceEntityType.PROJECT, data.source_reference)

        if mapping:
            # Update existing project
            project = self.db.get(Project, mapping.local_entity_id)
            if project:
                self._update_project(project, data)
            else:
                project = self._create_project(org_id, data)
                self.db.flush()  # Get project_id
                mapping.local_entity_id = project.project_id
                mapping.local_entity_type = "project"
            self._update_mapping(
                mapping,
                data.name,
                data.code,
                data.customer_name,
                data.status,
                data.metadata,
            )
        else:
            # Create new project
            project = self._create_project(org_id, data)
            self.db.flush()  # Get project_id

            mapping = SourceCorrelation(
                organization_id=org_id,
                source_application=self._SOURCE_APPLICATION,
                source_entity_type=SourceEntityType.PROJECT,
                source_reference=data.source_reference,
                local_entity_type="project",
                local_entity_id=project.project_id,
                source_status=SOURCE_STATUS_MAP.get(
                    data.status.lower(), SourceCorrelationStatus.ACTIVE
                ),
                display_name=data.name,
                display_code=data.code,
                customer_name=data.customer_name,
                source_payload=data.metadata,
                synced_at=datetime.now(UTC),
            )
            self.db.add(mapping)

        logger.info("Synced Sub project %s -> %s", data.source_reference, mapping.local_entity_id)
        return mapping

    def sync_ticket(
        self,
        org_id: UUID,
        data: SubTicketPayload,
        item_errors: list[str] | None = None,
    ) -> SourceCorrelation:
        """
        Sync a ticket from Sub to ERP.

        Creates or updates both the local Ticket and the SourceCorrelation.
        """
        ticket_source_payload = self._build_ticket_source_payload(data)
        mapping = self._get_mapping(org_id, SourceEntityType.TICKET, data.source_reference)

        if mapping:
            ticket = self.db.get(Ticket, mapping.local_entity_id)
            if ticket:
                self._update_ticket(ticket, data)
            else:
                ticket = self._create_ticket(org_id, data)
                self.db.flush()
                mapping.local_entity_id = ticket.ticket_id
                mapping.local_entity_type = "ticket"
            self._update_mapping(
                mapping,
                data.subject,
                data.ticket_number,
                data.customer_name,
                data.status,
                ticket_source_payload,
            )
        else:
            ticket = self._create_ticket(org_id, data)
            self.db.flush()

            mapping = SourceCorrelation(
                organization_id=org_id,
                source_application=self._SOURCE_APPLICATION,
                source_entity_type=SourceEntityType.TICKET,
                source_reference=data.source_reference,
                local_entity_type="ticket",
                local_entity_id=ticket.ticket_id,
                source_status=SOURCE_STATUS_MAP.get(
                    data.status.lower(), SourceCorrelationStatus.ACTIVE
                ),
                display_name=data.subject,
                display_code=data.ticket_number,
                customer_name=data.customer_name,
                source_payload=ticket_source_payload,
                synced_at=datetime.now(UTC),
            )
            self.db.add(mapping)

        comments_processed, comment_dedupe_hits, comment_errors = (
            self._sync_ticket_comments(org_id, ticket, data.comments)
        )
        activity_processed, activity_dedupe_hits, activity_errors = (
            self._sync_ticket_activity(org_id, ticket, data.activity_log, data.comments)
        )

        if item_errors is not None:
            item_errors.extend(comment_errors)
            item_errors.extend(activity_errors)

        logger.info(
            "Synced Sub ticket %s -> %s comments_processed=%d activity_processed=%d "
            "dedupe_hits=%d",
            data.source_reference,
            mapping.local_entity_id,
            comments_processed,
            activity_processed,
            comment_dedupe_hits + activity_dedupe_hits,
        )
        return mapping

    def sync_work_order(
        self,
        org_id: UUID,
        data: SubWorkOrderPayload,
    ) -> SourceCorrelation:
        """
        Sync a work order from Sub to ERP as a Task.

        Creates or updates both the local Task and the SourceCorrelation.
        """
        mapping = self._get_mapping(org_id, SourceEntityType.WORK_ORDER, data.source_reference)

        # Resolve project reference if provided
        project_id = self._resolve_project_id(org_id, data.project_source_reference)

        # Resolve ticket reference if provided
        ticket_id = self._resolve_ticket_id(org_id, data.ticket_source_reference)

        # Resolve employee by email
        employee_id = self._resolve_employee_id(org_id, data.assigned_employee_email)

        if mapping:
            task = self.db.get(Task, mapping.local_entity_id)
            if task:
                self._update_task(task, data, project_id, ticket_id, employee_id)
            else:
                if not project_id:
                    project_id = self._get_or_create_default_project(org_id)
                task = self._create_task(
                    org_id, data, project_id, ticket_id, employee_id
                )
                self.db.flush()
                mapping.local_entity_id = task.task_id
                mapping.local_entity_type = "task"
            self._update_mapping(
                mapping, data.title, None, None, data.status, data.metadata
            )
        else:
            # Work orders require a project - create a default one if needed
            if not project_id:
                project_id = self._get_or_create_default_project(org_id)

            task = self._create_task(org_id, data, project_id, ticket_id, employee_id)
            self.db.flush()

            mapping = SourceCorrelation(
                organization_id=org_id,
                source_application=self._SOURCE_APPLICATION,
                source_entity_type=SourceEntityType.WORK_ORDER,
                source_reference=data.source_reference,
                local_entity_type="task",
                local_entity_id=task.task_id,
                source_status=SOURCE_STATUS_MAP.get(
                    data.status.lower(), SourceCorrelationStatus.ACTIVE
                ),
                display_name=data.title,
                source_payload=data.metadata,
                synced_at=datetime.now(UTC),
            )
            self.db.add(mapping)

        logger.info(
            "Synced Sub work order %s -> %s", data.source_reference, mapping.local_entity_id
        )
        return mapping

    def reconcile_orphans(
        self,
        org_id: UUID,
        *,
        entity_type: str,
        seen_source_references: list[str],
        active_count: int = 0,
    ) -> dict[str, Any]:
        """Soft-close ERP entities whose Sub source vanished from a full push.

        Sub's ``sync_all_active`` only pushes active entities, so a canceled/
        soft-deleted Sub entity simply stops appearing — no tombstone ever
        arrives and the upsert-only sync leaves the ERP copy live forever.
        After a clean FULL run, Sub reports the complete set of ids it saw;
        ACTIVE mappings for that entity type not in the set are orphans.

        Applies the reference safety rails (see module constants) before
        touching anything, then per-row soft-closes the local entity via each
        model's terminal status and marks the mapping CANCELLED. Rows are
        isolated in savepoints so one failure doesn't abort the batch.
        """
        source_entity_type = _RECONCILE_ENTITY_TYPES.get(entity_type)
        if source_entity_type is None:
            raise ValueError(f"Unknown entity type: {entity_type}")

        result: dict[str, Any] = {
            "entity_type": entity_type,
            "examined": 0,
            "orphaned": 0,
            "closed": 0,
            "skipped_reason": None,
            "errors": [],
        }

        seen = {str(source_reference).strip() for source_reference in seen_source_references if str(source_reference).strip()}
        stmt = select(SourceCorrelation).where(
            SourceCorrelation.organization_id == org_id,
            SourceCorrelation.source_application == self._SOURCE_APPLICATION,
            SourceCorrelation.source_entity_type == source_entity_type,
            SourceCorrelation.source_status == SourceCorrelationStatus.ACTIVE,
        )
        mappings = list(self.db.scalars(stmt).all())
        examined = len(mappings)
        result["examined"] = examined

        orphans = [m for m in mappings if str(m.source_reference).strip() not in seen]
        result["orphaned"] = len(orphans)
        if not orphans:
            return result

        if len(seen) < examined * _ORPHAN_MIN_FETCH_RATIO:
            logger.warning(
                "Sub_ORPHAN_RECONCILE_SKIPPED reason=small_fetch entity_type=%s "
                "seen=%d active_count=%d examined=%d orphans=%d",
                entity_type,
                len(seen),
                active_count,
                examined,
                len(orphans),
            )
            result["skipped_reason"] = "small_fetch"
            return result

        if len(orphans) > max(
            _ORPHAN_MAX_TERMINATE_FLOOR, int(examined * _ORPHAN_MAX_TERMINATE_RATIO)
        ):
            logger.warning(
                "Sub_ORPHAN_RECONCILE_SKIPPED reason=too_many entity_type=%s "
                "examined=%d orphans=%d",
                entity_type,
                examined,
                len(orphans),
            )
            result["skipped_reason"] = "too_many"
            return result

        now = datetime.now(UTC)
        for mapping in orphans:
            savepoint = self.db.begin_nested()
            try:
                self._soft_close_local_entity(mapping)
                mapping.source_status = SourceCorrelationStatus.CANCELLED
                mapping.synced_at = now
                self.db.flush()
                savepoint.commit()
                result["closed"] += 1
            except Exception as exc:
                savepoint.rollback()
                logger.exception(
                    "Sub_ORPHAN_CLOSE_FAILED entity_type=%s source_reference=%s",
                    entity_type,
                    mapping.source_reference,
                )
                result["errors"].append(f"{mapping.source_reference}: {exc}"[:200])

        logger.info(
            "Sub_ORPHAN_RECONCILE_COMPLETE entity_type=%s examined=%d orphaned=%d closed=%d",
            entity_type,
            examined,
            len(orphans),
            result["closed"],
        )
        return result

    def _soft_close_local_entity(self, mapping: SourceCorrelation) -> None:
        """Soft-close the local ERP entity behind an orphaned mapping.

        Uses each model's terminal status (none of Project/Ticket/Task has an
        ``is_active`` soft-delete column): Project -> CANCELLED, Ticket ->
        CLOSED, Task -> CANCELLED — the same targets the Sub status maps use
        for a canceled upstream entity. A missing local row is a no-op; the
        mapping still gets marked so it stops surfacing as active.
        """
        if mapping.local_entity_type == "project":
            project = self.db.get(Project, mapping.local_entity_id)
            if project is not None:
                project.status = ProjectStatus.CANCELLED
        elif mapping.local_entity_type == "ticket":
            ticket = self.db.get(Ticket, mapping.local_entity_id)
            if ticket is not None:
                ticket.status = TicketStatus.CLOSED
        elif mapping.local_entity_type == "task":
            task = self.db.get(Task, mapping.local_entity_id)
            if task is not None:
                task.status = TaskStatus.CANCELLED
        else:
            logger.warning(
                "Sub_ORPHAN_UNKNOWN_LOCAL_TYPE local_entity_type=%s source_reference=%s",
                mapping.local_entity_type,
                mapping.source_reference,
            )

    def list_projects(
        self,
        org_id: UUID,
        search: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[SubProjectRead]:
        """List Sub projects for expense claim dropdown."""
        stmt = (
            select(SourceCorrelation)
            .where(SourceCorrelation.organization_id == org_id)
            .where(
                SourceCorrelation.source_application == self._SOURCE_APPLICATION
            )
            .where(SourceCorrelation.source_entity_type == SourceEntityType.PROJECT)
        )

        if search:
            search_filter = f"%{search}%"
            stmt = stmt.where(
                (SourceCorrelation.display_name.ilike(search_filter))
                | (SourceCorrelation.display_code.ilike(search_filter))
            )

        if status:
            stmt = stmt.where(
                SourceCorrelation.source_status
                == SOURCE_STATUS_MAP.get(status.lower(), SourceCorrelationStatus.ACTIVE)
            )
        else:
            # Hide cancelled/archived Sub entities so a canceled project isn't
            # selectable for new expense claims. Sub sends the cancellation via
            # the recently-updated delta (canceling keeps is_active=True), which
            # marks the mapping CANCELLED — it just wasn't being filtered here.
            stmt = stmt.where(
                SourceCorrelation.source_status.notin_(
                    [SourceCorrelationStatus.CANCELLED, SourceCorrelationStatus.ARCHIVED]
                )
            )

        stmt = stmt.order_by(SourceCorrelation.display_name).limit(limit)
        mappings = list(self.db.scalars(stmt).all())

        return [
            SubProjectRead(
                mapping_id=m.mapping_id,
                source_reference=m.source_reference,
                local_entity_id=m.local_entity_id,
                name=m.display_name,
                code=m.display_code,
                status=m.source_status.value,
                customer_name=m.customer_name,
            )
            for m in mappings
        ]

    def list_tickets(
        self,
        org_id: UUID,
        search: str | None = None,
        limit: int = 50,
    ) -> list[SubTicketRead]:
        """List Sub tickets for expense claim dropdown."""
        stmt = (
            select(SourceCorrelation)
            .where(SourceCorrelation.organization_id == org_id)
            .where(
                SourceCorrelation.source_application == self._SOURCE_APPLICATION
            )
            .where(SourceCorrelation.source_entity_type == SourceEntityType.TICKET)
        )

        if search:
            search_filter = f"%{search}%"
            stmt = stmt.where(
                (SourceCorrelation.display_name.ilike(search_filter))
                | (SourceCorrelation.display_code.ilike(search_filter))
            )

        # Hide cancelled/archived tickets from the expense-claim picker.
        stmt = stmt.where(
            SourceCorrelation.source_status.notin_(
                [SourceCorrelationStatus.CANCELLED, SourceCorrelationStatus.ARCHIVED]
            )
        )
        stmt = stmt.order_by(SourceCorrelation.created_at.desc()).limit(limit)
        mappings = list(self.db.scalars(stmt).all())

        return [
            SubTicketRead(
                mapping_id=m.mapping_id,
                source_reference=m.source_reference,
                local_entity_id=m.local_entity_id,
                subject=m.display_name,
                ticket_number=m.display_code,
                status=m.source_status.value,
                customer_name=m.customer_name,
            )
            for m in mappings
        ]

    def list_work_orders(
        self,
        org_id: UUID,
        search: str | None = None,
        employee_id: UUID | None = None,
        limit: int = 50,
    ) -> list[SubWorkOrderRead]:
        """List Sub work orders for expense claim dropdown."""
        stmt = (
            select(SourceCorrelation)
            .where(SourceCorrelation.organization_id == org_id)
            .where(
                SourceCorrelation.source_application == self._SOURCE_APPLICATION
            )
            .where(SourceCorrelation.source_entity_type == SourceEntityType.WORK_ORDER)
        )

        if search:
            stmt = stmt.where(SourceCorrelation.display_name.ilike(f"%{search}%"))

        # If employee_id filter, join to Task and filter by assigned_to
        if employee_id:
            stmt = stmt.join(
                Task,
                (SourceCorrelation.local_entity_id == Task.task_id)
                & (SourceCorrelation.local_entity_type == "task"),
            ).where(Task.assigned_to_id == employee_id)

        # Hide cancelled/archived work orders from the expense-claim picker.
        stmt = stmt.where(
            SourceCorrelation.source_status.notin_(
                [SourceCorrelationStatus.CANCELLED, SourceCorrelationStatus.ARCHIVED]
            )
        )
        stmt = stmt.order_by(SourceCorrelation.created_at.desc()).limit(limit)
        mappings = list(self.db.scalars(stmt).all())

        return [
            SubWorkOrderRead(
                mapping_id=m.mapping_id,
                source_reference=m.source_reference,
                local_entity_id=m.local_entity_id,
                title=m.display_name,
                status=m.source_status.value,
                project_name=None,  # Could be enriched if needed
                ticket_subject=None,
            )
            for m in mappings
        ]

    def _create_project(self, org_id: UUID, data: SubProjectPayload) -> Project:
        """Create a local Project from Sub data."""
        source_code = (data.code or "").strip()
        source_code_in_use = (
            self.db.scalar(
                select(Project.project_id).where(
                    Project.organization_id == org_id,
                    Project.project_code == source_code,
                )
            )
            if source_code and len(source_code) <= 20
            else None
        )
        project_code = (
            source_code
            if source_code and len(source_code) <= 20 and source_code_in_use is None
            else self._generate_unique_code("Sub", data.source_reference, max_len=20)
        )

        project = Project(
            organization_id=org_id,
            project_code=project_code,
            project_name=data.name,
            description=data.description,
            status=PROJECT_STATUS_MAP.get(data.status.lower(), ProjectStatus.ACTIVE),
            project_type=self._map_project_type(data.project_type),
            start_date=data.start_at.date() if data.start_at else None,
            end_date=data.due_at.date() if data.due_at else None,
        )
        self.db.add(project)
        return project

    def _update_project(self, project: Project, data: SubProjectPayload) -> None:
        """Update existing project from Sub data."""
        project.project_name = data.name
        project.description = data.description
        project.status = PROJECT_STATUS_MAP.get(
            data.status.lower(), ProjectStatus.ACTIVE
        )
        project.start_date = (
            data.start_at.date() if data.start_at else project.start_date
        )
        project.end_date = data.due_at.date() if data.due_at else project.end_date

    def _create_ticket(self, org_id: UUID, data: SubTicketPayload) -> Ticket:
        """Create a local Ticket from Sub data."""
        ticket_number = data.ticket_number or self._generate_unique_code(
            "Sub", data.source_reference, max_len=50
        )

        ticket = Ticket(
            organization_id=org_id,
            ticket_number=ticket_number,
            subject=data.subject,
            description=data.description,
            status=TICKET_STATUS_MAP.get(data.status.lower(), TicketStatus.OPEN),
            priority=self._map_ticket_priority(data.priority),
        )
        self.db.add(ticket)
        return ticket

    def _update_ticket(self, ticket: Ticket, data: SubTicketPayload) -> None:
        """Update existing ticket from Sub data."""
        ticket.subject = data.subject
        if data.description is not None:
            ticket.description = data.description
        ticket.status = TICKET_STATUS_MAP.get(data.status.lower(), TicketStatus.OPEN)
        ticket.priority = self._map_ticket_priority(data.priority)

    def _build_ticket_source_payload(self, data: SubTicketPayload) -> dict | None:
        """Build mapping source_payload for ticket payload, preserving backward compatibility."""
        source_payload: dict = dict(data.metadata or {})
        if data.description is not None:
            source_payload["description"] = data.description
        if data.comments:
            source_payload["comments"] = data.comments
        if data.activity_log:
            source_payload["activity_log"] = data.activity_log
        return source_payload or None

    def _sync_ticket_comments(
        self,
        org_id: UUID,
        ticket: Ticket,
        raw_comments: list[dict[str, Any]],
    ) -> tuple[int, int, list[str]]:
        """Sync Sub comment items to support.ticket_comment with idempotent dedupe."""
        processed = 0
        dedupe_hits = 0
        errors: list[str] = []

        for idx, raw in enumerate(raw_comments or []):
            try:
                item = SubTicketCommentItem.model_validate(raw)
            except ValidationError as exc:
                errors.append(
                    f"comments[{idx}] validation failed: {exc.errors()[0].get('msg', 'invalid')}"
                )
                continue

            _, dedupe = self._upsert_sub_comment_item(org_id, ticket, item)
            processed += 1
            dedupe_hits += dedupe

        return processed, dedupe_hits, errors

    def _sync_ticket_activity(
        self,
        org_id: UUID,
        ticket: Ticket,
        raw_activity: list[dict[str, Any]],
        raw_comments: list[dict[str, Any]],
    ) -> tuple[int, int, list[str]]:
        """Sync Sub activity_log entries to support.ticket_comment (activity timeline)."""
        processed = 0
        dedupe_hits = 0
        errors: list[str] = []

        comment_ids = {
            str(item.get("id"))
            for item in (raw_comments or [])
            if isinstance(item, dict) and item.get("id")
        }

        for idx, raw in enumerate(raw_activity or []):
            try:
                entry = SubTicketActivityEntry.model_validate(raw)
            except ValidationError as exc:
                errors.append(
                    f"activity_log[{idx}] validation failed: {exc.errors()[0].get('msg', 'invalid')}"
                )
                continue

            # Avoid double insert when comment appears in both comments[] and activity_log[].
            if entry.kind == "comment" and entry.id in comment_ids:
                dedupe_hits += 1
                logger.debug(
                    "Sub ticket activity dedupe hit: kind=comment id=%s ticket=%s",
                    entry.id,
                    ticket.ticket_number,
                )
                continue

            _, dedupe = self._upsert_sub_activity_item(org_id, ticket, entry)
            processed += 1
            dedupe_hits += dedupe

        return processed, dedupe_hits, errors

    def _upsert_sub_comment_item(
        self,
        org_id: UUID,
        ticket: Ticket,
        item: SubTicketCommentItem,
    ) -> tuple[TicketComment, int]:
        """Upsert a Sub comment item by SyncEntity(source='sub', doctype, id)."""
        sync = self.db.scalar(
            select(SyncEntity).where(
                SyncEntity.organization_id == org_id,
                SyncEntity.source_system == "sub",
                SyncEntity.source_doctype == "ticket_comment",
                SyncEntity.source_name == item.id,
            )
        )

        author_id = self._resolve_sub_person_id(org_id, item.author_person_id)
        comment_type = (
            CommentType.INTERNAL_NOTE if item.is_internal else CommentType.COMMENT
        )

        if sync and sync.target_id:
            existing = self.db.get(TicketComment, sync.target_id)
            if existing:
                existing.ticket_id = ticket.ticket_id
                existing.comment_type = comment_type
                existing.is_internal = item.is_internal
                existing.author_id = author_id
                existing.content = item.body or ""
                if item.timestamp:
                    existing.created_at = item.timestamp
                sync.mark_synced(existing.comment_id)
                logger.debug(
                    "Sub ticket comment dedupe hit: id=%s ticket=%s",
                    item.id,
                    ticket.ticket_number,
                )
                return existing, 1

        comment = TicketComment(
            ticket_id=ticket.ticket_id,
            comment_type=comment_type,
            content=item.body or "",
            author_id=author_id,
            is_internal=item.is_internal,
        )
        if item.timestamp:
            comment.created_at = item.timestamp
        self.db.add(comment)
        self.db.flush()

        if sync:
            sync.target_table = "support.ticket_comment"
            sync.target_id = comment.comment_id
            sync.mark_synced(comment.comment_id)
        else:
            self.db.add(
                SyncEntity(
                    organization_id=org_id,
                    source_system="sub",
                    source_doctype="ticket_comment",
                    source_name=item.id,
                    target_table="support.ticket_comment",
                    target_id=comment.comment_id,
                    sync_status=SyncStatus.SYNCED,
                )
            )
        return comment, 0

    def _upsert_sub_activity_item(
        self,
        org_id: UUID,
        ticket: Ticket,
        entry: SubTicketActivityEntry,
    ) -> tuple[TicketComment, int]:
        """Upsert a Sub activity item by SyncEntity(source='sub', kind, id)."""
        doctype = f"ticket_activity_{entry.kind}"
        sync = self.db.scalar(
            select(SyncEntity).where(
                SyncEntity.organization_id == org_id,
                SyncEntity.source_system == "sub",
                SyncEntity.source_doctype == doctype,
                SyncEntity.source_name == entry.id,
            )
        )
        author_id = self._resolve_sub_person_id(org_id, entry.author_person_id)

        if entry.kind == "comment":
            comment_type = (
                CommentType.INTERNAL_NOTE if entry.is_internal else CommentType.COMMENT
            )
            content = entry.body or ""
            action = None
            old_value = None
            new_value = None
            is_internal = entry.is_internal
        else:
            comment_type = CommentType.SYSTEM
            details_str = (
                json.dumps(entry.details, sort_keys=True) if entry.details else ""
            )
            content = (
                entry.body or f"{entry.event_type or 'event'} {details_str}".strip()
            )
            action = entry.event_type or "sub_event"
            old_value = None
            new_value = entry.status
            is_internal = True

        if sync and sync.target_id:
            existing = self.db.get(TicketComment, sync.target_id)
            if existing:
                existing.ticket_id = ticket.ticket_id
                existing.comment_type = comment_type
                existing.content = content
                existing.author_id = author_id
                existing.is_internal = is_internal
                existing.action = action
                existing.old_value = old_value
                existing.new_value = new_value
                if entry.timestamp:
                    existing.created_at = entry.timestamp
                sync.mark_synced(existing.comment_id)
                logger.debug(
                    "Sub ticket activity dedupe hit: kind=%s id=%s ticket=%s",
                    entry.kind,
                    entry.id,
                    ticket.ticket_number,
                )
                return existing, 1

        comment = TicketComment(
            ticket_id=ticket.ticket_id,
            comment_type=comment_type,
            content=content,
            author_id=author_id,
            is_internal=is_internal,
            action=action,
            old_value=old_value,
            new_value=new_value,
        )
        if entry.timestamp:
            comment.created_at = entry.timestamp
        self.db.add(comment)
        self.db.flush()

        if sync:
            sync.target_table = "support.ticket_comment"
            sync.target_id = comment.comment_id
            sync.mark_synced(comment.comment_id)
        else:
            self.db.add(
                SyncEntity(
                    organization_id=org_id,
                    source_system="sub",
                    source_doctype=doctype,
                    source_name=entry.id,
                    target_table="support.ticket_comment",
                    target_id=comment.comment_id,
                    sync_status=SyncStatus.SYNCED,
                )
            )
        return comment, 0

    def _resolve_sub_person_id(
        self, org_id: UUID, author_person_id: str | None
    ) -> UUID | None:
        """Resolve Sub author to local Person ID.

        Accepts either:
        - Person UUID (people.id)
        - Employee UUID (hr.employee.employee_id), then maps to person_id.
        """
        if not author_person_id:
            return None
        try:
            external_id = UUID(author_person_id)
        except (TypeError, ValueError):
            return None

        # 1) Direct Person UUID
        person = self.db.get(Person, external_id)
        if not person or person.organization_id != org_id:
            # 2) Employee UUID -> Person UUID fallback
            employee = self.db.get(Employee, external_id)
            if (
                employee
                and employee.organization_id == org_id
                and employee.person_id is not None
            ):
                return employee.person_id
            return None
        return external_id

    def _create_task(
        self,
        org_id: UUID,
        data: SubWorkOrderPayload,
        project_id: UUID,
        ticket_id: UUID | None,
        employee_id: UUID | None,
    ) -> Task:
        """Create a local Task from Sub work order data."""
        task_code = self._generate_unique_code("WO", data.source_reference, max_len=30)

        task = Task(
            organization_id=org_id,
            project_id=project_id,
            task_code=task_code,
            task_name=data.title,
            status=TASK_STATUS_MAP.get(data.status.lower(), TaskStatus.OPEN),
            priority=self._map_task_priority(data.priority),
            assigned_to_id=employee_id,
            ticket_id=ticket_id,
            start_date=data.scheduled_start.date() if data.scheduled_start else None,
            due_date=data.scheduled_end.date() if data.scheduled_end else None,
        )
        self.db.add(task)
        return task

    def _update_task(
        self,
        task: Task,
        data: SubWorkOrderPayload,
        project_id: UUID | None,
        ticket_id: UUID | None,
        employee_id: UUID | None,
    ) -> None:
        """Update existing task from Sub work order data."""
        task.task_name = data.title
        task.status = TASK_STATUS_MAP.get(data.status.lower(), TaskStatus.OPEN)
        task.priority = self._map_task_priority(data.priority)
        if project_id:
            task.project_id = project_id
        if ticket_id:
            task.ticket_id = ticket_id
        if employee_id:
            task.assigned_to_id = employee_id
        if data.scheduled_start:
            task.start_date = data.scheduled_start.date()
        if data.scheduled_end:
            task.due_date = data.scheduled_end.date()

    def _map_project_type(self, type_str: str | None) -> ProjectType:
        """Map Sub project type to local enum (delegates to sub_mappings)."""
        return map_project_type(type_str)

    def _map_ticket_priority(self, priority_str: str | None) -> TicketPriority:
        """Map Sub priority to local enum (delegates to sub_mappings)."""
        return map_ticket_priority(priority_str)

    def _map_task_priority(self, priority_str: str | None) -> TaskPriority:
        """Map Sub priority to local enum (delegates to sub_mappings)."""
        return map_task_priority(priority_str)
