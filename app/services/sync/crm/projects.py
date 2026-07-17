"""Project / ticket / work-order (task) synchronization from DotMac CRM.

Extracted from the former monolithic dotmac_crm_sync_service.
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
from app.models.sync.dotmac_crm_sync import (
    CRMEntityType,
    CRMSyncMapping,
    CRMSyncStatus,
)
from app.models.sync.sync_entity import SyncEntity, SyncStatus
from app.schemas.sync.dotmac_crm import (
    CRMProjectPayload,
    CRMProjectRead,
    CRMTicketActivityEntry,
    CRMTicketCommentItem,
    CRMTicketPayload,
    CRMTicketRead,
    CRMWorkOrderPayload,
    CRMWorkOrderRead,
)

# CRM → ERP translation policy lives in crm_mappings (pure, side-effect-free).
# Re-imported here so the canonical import sites
# (`from ...dotmac_crm_sync_service import PROJECT_STATUS_MAP`) and the in-class
# references keep resolving against this module's namespace.
from app.services.sync.crm_mappings import (  # noqa: E402
    CRM_SYNC_STATUS_MAP,
    PROJECT_STATUS_MAP,
    TASK_STATUS_MAP,
    TICKET_STATUS_MAP,
    map_project_type,
    map_task_priority,
    map_ticket_priority,
)

from app.services.sync.crm.base import _CRMSyncBase

logger = logging.getLogger(__name__)

# Orphan-reconciliation guards — the exact rails proven in dotmac_crm
# ``app/services/selfcare.py::_reconcile_selfcare_orphans``: skip when the
# reported id set looks suspiciously small (likely a partial fetch/outage on
# the CRM side) or when too large a fraction of the known mappings would be
# closed in one run — failing safe rather than mass-closing live entities.
_ORPHAN_MIN_FETCH_RATIO = 0.5
_ORPHAN_MAX_TERMINATE_RATIO = 0.2
# Small absolute floor for tiny bases (so a single legitimate deletion isn't
# blocked) — kept low so it can't bypass the ratio guard and wipe a small base.
_ORPHAN_MAX_TERMINATE_FLOOR = 3

# Reconcile entity_type strings (CRM wire values) -> mapping entity types.
_RECONCILE_ENTITY_TYPES = {
    "project": CRMEntityType.PROJECT,
    "ticket": CRMEntityType.TICKET,
    "work_order": CRMEntityType.WORK_ORDER,
}


class _ProjectSyncMixin(_CRMSyncBase):
    def sync_project(
        self,
        org_id: UUID,
        data: CRMProjectPayload,
    ) -> CRMSyncMapping:
        """
        Sync a project from CRM to ERP.

        Creates or updates both the local Project and the CRMSyncMapping.
        """
        # Check if mapping exists
        mapping = self._get_mapping(org_id, CRMEntityType.PROJECT, data.crm_id)

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

            mapping = CRMSyncMapping(
                organization_id=org_id,
                crm_entity_type=CRMEntityType.PROJECT,
                crm_id=data.crm_id,
                local_entity_type="project",
                local_entity_id=project.project_id,
                crm_status=CRM_SYNC_STATUS_MAP.get(
                    data.status.lower(), CRMSyncStatus.ACTIVE
                ),
                display_name=data.name,
                display_code=data.code,
                customer_name=data.customer_name,
                crm_data=data.metadata,
                synced_at=datetime.now(UTC),
            )
            self.db.add(mapping)

        logger.info("Synced CRM project %s -> %s", data.crm_id, mapping.local_entity_id)
        return mapping

    def sync_ticket(
        self,
        org_id: UUID,
        data: CRMTicketPayload,
        item_errors: list[str] | None = None,
    ) -> CRMSyncMapping:
        """
        Sync a ticket from CRM to ERP.

        Creates or updates both the local Ticket and the CRMSyncMapping.
        """
        ticket_crm_data = self._build_ticket_crm_data(data)
        mapping = self._get_mapping(org_id, CRMEntityType.TICKET, data.crm_id)

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
                ticket_crm_data,
            )
        else:
            ticket = self._create_ticket(org_id, data)
            self.db.flush()

            mapping = CRMSyncMapping(
                organization_id=org_id,
                crm_entity_type=CRMEntityType.TICKET,
                crm_id=data.crm_id,
                local_entity_type="ticket",
                local_entity_id=ticket.ticket_id,
                crm_status=CRM_SYNC_STATUS_MAP.get(
                    data.status.lower(), CRMSyncStatus.ACTIVE
                ),
                display_name=data.subject,
                display_code=data.ticket_number,
                customer_name=data.customer_name,
                crm_data=ticket_crm_data,
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
            "Synced CRM ticket %s -> %s comments_processed=%d activity_processed=%d "
            "dedupe_hits=%d",
            data.crm_id,
            mapping.local_entity_id,
            comments_processed,
            activity_processed,
            comment_dedupe_hits + activity_dedupe_hits,
        )
        return mapping

    def sync_work_order(
        self,
        org_id: UUID,
        data: CRMWorkOrderPayload,
    ) -> CRMSyncMapping:
        """
        Sync a work order from CRM to ERP as a Task.

        Creates or updates both the local Task and the CRMSyncMapping.
        """
        mapping = self._get_mapping(org_id, CRMEntityType.WORK_ORDER, data.crm_id)

        # Resolve project reference if provided
        project_id = self._resolve_project_id(org_id, data.project_crm_id)

        # Resolve ticket reference if provided
        ticket_id = self._resolve_ticket_id(org_id, data.ticket_crm_id)

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

            mapping = CRMSyncMapping(
                organization_id=org_id,
                crm_entity_type=CRMEntityType.WORK_ORDER,
                crm_id=data.crm_id,
                local_entity_type="task",
                local_entity_id=task.task_id,
                crm_status=CRM_SYNC_STATUS_MAP.get(
                    data.status.lower(), CRMSyncStatus.ACTIVE
                ),
                display_name=data.title,
                crm_data=data.metadata,
                synced_at=datetime.now(UTC),
            )
            self.db.add(mapping)

        logger.info(
            "Synced CRM work order %s -> %s", data.crm_id, mapping.local_entity_id
        )
        return mapping

    def reconcile_orphans(
        self,
        org_id: UUID,
        *,
        entity_type: str,
        seen_crm_ids: list[str],
        active_count: int = 0,
    ) -> dict[str, Any]:
        """Soft-close ERP entities whose CRM source vanished from a full push.

        CRM's ``sync_all_active`` only pushes active entities, so a canceled/
        soft-deleted CRM entity simply stops appearing — no tombstone ever
        arrives and the upsert-only sync leaves the ERP copy live forever.
        After a clean FULL run, CRM reports the complete set of ids it saw;
        ACTIVE mappings for that entity type not in the set are orphans.

        Applies the reference safety rails (see module constants) before
        touching anything, then per-row soft-closes the local entity via each
        model's terminal status and marks the mapping CANCELLED. Rows are
        isolated in savepoints so one failure doesn't abort the batch.
        """
        crm_entity_type = _RECONCILE_ENTITY_TYPES.get(entity_type)
        if crm_entity_type is None:
            raise ValueError(f"Unknown entity type: {entity_type}")

        result: dict[str, Any] = {
            "entity_type": entity_type,
            "examined": 0,
            "orphaned": 0,
            "closed": 0,
            "skipped_reason": None,
            "errors": [],
        }

        seen = {str(crm_id).strip() for crm_id in seen_crm_ids if str(crm_id).strip()}
        stmt = select(CRMSyncMapping).where(
            CRMSyncMapping.organization_id == org_id,
            CRMSyncMapping.crm_entity_type == crm_entity_type,
            CRMSyncMapping.crm_status == CRMSyncStatus.ACTIVE,
        )
        mappings = list(self.db.scalars(stmt).all())
        examined = len(mappings)
        result["examined"] = examined

        orphans = [m for m in mappings if str(m.crm_id).strip() not in seen]
        result["orphaned"] = len(orphans)
        if not orphans:
            return result

        if len(seen) < examined * _ORPHAN_MIN_FETCH_RATIO:
            logger.warning(
                "CRM_ORPHAN_RECONCILE_SKIPPED reason=small_fetch entity_type=%s "
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
                "CRM_ORPHAN_RECONCILE_SKIPPED reason=too_many entity_type=%s "
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
                mapping.crm_status = CRMSyncStatus.CANCELLED
                mapping.synced_at = now
                self.db.flush()
                savepoint.commit()
                result["closed"] += 1
            except Exception as exc:
                savepoint.rollback()
                logger.exception(
                    "CRM_ORPHAN_CLOSE_FAILED entity_type=%s crm_id=%s",
                    entity_type,
                    mapping.crm_id,
                )
                result["errors"].append(f"{mapping.crm_id}: {exc}"[:200])

        logger.info(
            "CRM_ORPHAN_RECONCILE_COMPLETE entity_type=%s examined=%d orphaned=%d closed=%d",
            entity_type,
            examined,
            len(orphans),
            result["closed"],
        )
        return result

    def _soft_close_local_entity(self, mapping: CRMSyncMapping) -> None:
        """Soft-close the local ERP entity behind an orphaned mapping.

        Uses each model's terminal status (none of Project/Ticket/Task has an
        ``is_active`` soft-delete column): Project -> CANCELLED, Ticket ->
        CLOSED, Task -> CANCELLED — the same targets the CRM status maps use
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
                "CRM_ORPHAN_UNKNOWN_LOCAL_TYPE local_entity_type=%s crm_id=%s",
                mapping.local_entity_type,
                mapping.crm_id,
            )

    def list_projects(
        self,
        org_id: UUID,
        search: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[CRMProjectRead]:
        """List CRM projects for expense claim dropdown."""
        stmt = (
            select(CRMSyncMapping)
            .where(CRMSyncMapping.organization_id == org_id)
            .where(CRMSyncMapping.crm_entity_type == CRMEntityType.PROJECT)
        )

        if search:
            search_filter = f"%{search}%"
            stmt = stmt.where(
                (CRMSyncMapping.display_name.ilike(search_filter))
                | (CRMSyncMapping.display_code.ilike(search_filter))
            )

        if status:
            stmt = stmt.where(
                CRMSyncMapping.crm_status
                == CRM_SYNC_STATUS_MAP.get(status.lower(), CRMSyncStatus.ACTIVE)
            )
        else:
            # Hide cancelled/archived CRM entities so a canceled project isn't
            # selectable for new expense claims. CRM sends the cancellation via
            # the recently-updated delta (canceling keeps is_active=True), which
            # marks the mapping CANCELLED — it just wasn't being filtered here.
            stmt = stmt.where(
                CRMSyncMapping.crm_status.notin_(
                    [CRMSyncStatus.CANCELLED, CRMSyncStatus.ARCHIVED]
                )
            )

        stmt = stmt.order_by(CRMSyncMapping.display_name).limit(limit)
        mappings = list(self.db.scalars(stmt).all())

        return [
            CRMProjectRead(
                mapping_id=m.mapping_id,
                crm_id=m.crm_id,
                local_entity_id=m.local_entity_id,
                name=m.display_name,
                code=m.display_code,
                status=m.crm_status.value,
                customer_name=m.customer_name,
            )
            for m in mappings
        ]

    def list_tickets(
        self,
        org_id: UUID,
        search: str | None = None,
        limit: int = 50,
    ) -> list[CRMTicketRead]:
        """List CRM tickets for expense claim dropdown."""
        stmt = (
            select(CRMSyncMapping)
            .where(CRMSyncMapping.organization_id == org_id)
            .where(CRMSyncMapping.crm_entity_type == CRMEntityType.TICKET)
        )

        if search:
            search_filter = f"%{search}%"
            stmt = stmt.where(
                (CRMSyncMapping.display_name.ilike(search_filter))
                | (CRMSyncMapping.display_code.ilike(search_filter))
            )

        # Hide cancelled/archived tickets from the expense-claim picker.
        stmt = stmt.where(
            CRMSyncMapping.crm_status.notin_(
                [CRMSyncStatus.CANCELLED, CRMSyncStatus.ARCHIVED]
            )
        )
        stmt = stmt.order_by(CRMSyncMapping.created_at.desc()).limit(limit)
        mappings = list(self.db.scalars(stmt).all())

        return [
            CRMTicketRead(
                mapping_id=m.mapping_id,
                crm_id=m.crm_id,
                local_entity_id=m.local_entity_id,
                subject=m.display_name,
                ticket_number=m.display_code,
                status=m.crm_status.value,
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
    ) -> list[CRMWorkOrderRead]:
        """List CRM work orders for expense claim dropdown."""
        stmt = (
            select(CRMSyncMapping)
            .where(CRMSyncMapping.organization_id == org_id)
            .where(CRMSyncMapping.crm_entity_type == CRMEntityType.WORK_ORDER)
        )

        if search:
            stmt = stmt.where(CRMSyncMapping.display_name.ilike(f"%{search}%"))

        # If employee_id filter, join to Task and filter by assigned_to
        if employee_id:
            stmt = stmt.join(
                Task,
                (CRMSyncMapping.local_entity_id == Task.task_id)
                & (CRMSyncMapping.local_entity_type == "task"),
            ).where(Task.assigned_to_id == employee_id)

        # Hide cancelled/archived work orders from the expense-claim picker.
        stmt = stmt.where(
            CRMSyncMapping.crm_status.notin_(
                [CRMSyncStatus.CANCELLED, CRMSyncStatus.ARCHIVED]
            )
        )
        stmt = stmt.order_by(CRMSyncMapping.created_at.desc()).limit(limit)
        mappings = list(self.db.scalars(stmt).all())

        return [
            CRMWorkOrderRead(
                mapping_id=m.mapping_id,
                crm_id=m.crm_id,
                local_entity_id=m.local_entity_id,
                title=m.display_name,
                status=m.crm_status.value,
                project_name=None,  # Could be enriched if needed
                ticket_subject=None,
            )
            for m in mappings
        ]

    def _create_project(self, org_id: UUID, data: CRMProjectPayload) -> Project:
        """Create a local Project from CRM data."""
        # Generate unique project code from CRM ID hash
        project_code = self._generate_unique_code("CRM", data.crm_id, max_len=20)

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

    def _update_project(self, project: Project, data: CRMProjectPayload) -> None:
        """Update existing project from CRM data."""
        project.project_name = data.name
        project.description = data.description
        project.status = PROJECT_STATUS_MAP.get(
            data.status.lower(), ProjectStatus.ACTIVE
        )
        project.start_date = (
            data.start_at.date() if data.start_at else project.start_date
        )
        project.end_date = data.due_at.date() if data.due_at else project.end_date

    def _create_ticket(self, org_id: UUID, data: CRMTicketPayload) -> Ticket:
        """Create a local Ticket from CRM data."""
        ticket_number = data.ticket_number or self._generate_unique_code(
            "CRM", data.crm_id, max_len=50
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

    def _update_ticket(self, ticket: Ticket, data: CRMTicketPayload) -> None:
        """Update existing ticket from CRM data."""
        ticket.subject = data.subject
        if data.description is not None:
            ticket.description = data.description
        ticket.status = TICKET_STATUS_MAP.get(data.status.lower(), TicketStatus.OPEN)
        ticket.priority = self._map_ticket_priority(data.priority)

    def _build_ticket_crm_data(self, data: CRMTicketPayload) -> dict | None:
        """Build mapping crm_data for ticket payload, preserving backward compatibility."""
        crm_data: dict = dict(data.metadata or {})
        if data.description is not None:
            crm_data["description"] = data.description
        if data.comments:
            crm_data["comments"] = data.comments
        if data.activity_log:
            crm_data["activity_log"] = data.activity_log
        return crm_data or None

    def _sync_ticket_comments(
        self,
        org_id: UUID,
        ticket: Ticket,
        raw_comments: list[dict[str, Any]],
    ) -> tuple[int, int, list[str]]:
        """Sync CRM comment items to support.ticket_comment with idempotent dedupe."""
        processed = 0
        dedupe_hits = 0
        errors: list[str] = []

        for idx, raw in enumerate(raw_comments or []):
            try:
                item = CRMTicketCommentItem.model_validate(raw)
            except ValidationError as exc:
                errors.append(
                    f"comments[{idx}] validation failed: {exc.errors()[0].get('msg', 'invalid')}"
                )
                continue

            _, dedupe = self._upsert_crm_comment_item(org_id, ticket, item)
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
        """Sync CRM activity_log entries to support.ticket_comment (activity timeline)."""
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
                entry = CRMTicketActivityEntry.model_validate(raw)
            except ValidationError as exc:
                errors.append(
                    f"activity_log[{idx}] validation failed: {exc.errors()[0].get('msg', 'invalid')}"
                )
                continue

            # Avoid double insert when comment appears in both comments[] and activity_log[].
            if entry.kind == "comment" and entry.id in comment_ids:
                dedupe_hits += 1
                logger.debug(
                    "CRM ticket activity dedupe hit: kind=comment id=%s ticket=%s",
                    entry.id,
                    ticket.ticket_number,
                )
                continue

            _, dedupe = self._upsert_crm_activity_item(org_id, ticket, entry)
            processed += 1
            dedupe_hits += dedupe

        return processed, dedupe_hits, errors

    def _upsert_crm_comment_item(
        self,
        org_id: UUID,
        ticket: Ticket,
        item: CRMTicketCommentItem,
    ) -> tuple[TicketComment, int]:
        """Upsert a CRM comment item by SyncEntity(source='crm', doctype, id)."""
        sync = self.db.scalar(
            select(SyncEntity).where(
                SyncEntity.organization_id == org_id,
                SyncEntity.source_system == "crm",
                SyncEntity.source_doctype == "ticket_comment",
                SyncEntity.source_name == item.id,
            )
        )

        author_id = self._resolve_crm_person_id(org_id, item.author_person_id)
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
                    "CRM ticket comment dedupe hit: id=%s ticket=%s",
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
                    source_system="crm",
                    source_doctype="ticket_comment",
                    source_name=item.id,
                    target_table="support.ticket_comment",
                    target_id=comment.comment_id,
                    sync_status=SyncStatus.SYNCED,
                )
            )
        return comment, 0

    def _upsert_crm_activity_item(
        self,
        org_id: UUID,
        ticket: Ticket,
        entry: CRMTicketActivityEntry,
    ) -> tuple[TicketComment, int]:
        """Upsert a CRM activity item by SyncEntity(source='crm', kind, id)."""
        doctype = f"ticket_activity_{entry.kind}"
        sync = self.db.scalar(
            select(SyncEntity).where(
                SyncEntity.organization_id == org_id,
                SyncEntity.source_system == "crm",
                SyncEntity.source_doctype == doctype,
                SyncEntity.source_name == entry.id,
            )
        )
        author_id = self._resolve_crm_person_id(org_id, entry.author_person_id)

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
            action = entry.event_type or "crm_event"
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
                    "CRM ticket activity dedupe hit: kind=%s id=%s ticket=%s",
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
                    source_system="crm",
                    source_doctype=doctype,
                    source_name=entry.id,
                    target_table="support.ticket_comment",
                    target_id=comment.comment_id,
                    sync_status=SyncStatus.SYNCED,
                )
            )
        return comment, 0

    def _resolve_crm_person_id(
        self, org_id: UUID, author_person_id: str | None
    ) -> UUID | None:
        """Resolve CRM author to local Person ID.

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
        data: CRMWorkOrderPayload,
        project_id: UUID,
        ticket_id: UUID | None,
        employee_id: UUID | None,
    ) -> Task:
        """Create a local Task from CRM work order data."""
        task_code = self._generate_unique_code("WO", data.crm_id, max_len=30)

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
        data: CRMWorkOrderPayload,
        project_id: UUID | None,
        ticket_id: UUID | None,
        employee_id: UUID | None,
    ) -> None:
        """Update existing task from CRM work order data."""
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
        """Map CRM project type to local enum (delegates to crm_mappings)."""
        return map_project_type(type_str)

    def _map_ticket_priority(self, priority_str: str | None) -> TicketPriority:
        """Map CRM priority to local enum (delegates to crm_mappings)."""
        return map_ticket_priority(priority_str)

    def _map_task_priority(self, priority_str: str | None) -> TaskPriority:
        """Map CRM priority to local enum (delegates to crm_mappings)."""
        return map_task_priority(priority_str)
