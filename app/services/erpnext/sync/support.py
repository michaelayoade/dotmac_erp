"""
Support/Ticket Sync Service - ERPNext to DotMac ERP.

Syncs ERPNext Issue (or HD Ticket) DocType to DotMac support.ticket,
including Comment and Communication records as TicketComment entries.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.support.comment import CommentType, TicketComment
from app.models.support.ticket import Ticket, TicketPriority, TicketStatus
from app.services.erpnext.client import ERPNextError
from app.services.erpnext.mappings.support import (
    CommunicationToCommentMapping,
    TicketMapping,
)

from .base import BaseSyncService

logger = logging.getLogger(__name__)


class TicketSyncService(BaseSyncService[Ticket]):
    """Sync Tickets/Issues from ERPNext, including comments and communications."""

    source_doctype = "Issue"  # May change to "HD Ticket" for v14+
    target_table = "support.ticket"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        use_hd_ticket: bool = False,
    ):
        """
        Initialize ticket sync service.

        Args:
            db: Database session
            organization_id: Organization UUID
            user_id: User UUID for audit
            use_hd_ticket: Use "HD Ticket" DocType instead of "Issue"
        """
        super().__init__(db, organization_id, user_id)
        self.source_doctype = "HD Ticket" if use_hd_ticket else "Issue"
        self._mapping = TicketMapping(doctype=self.source_doctype)
        self._ticket_cache: dict[str, Ticket] = {}
        self._person_cache: dict[str, uuid.UUID | None] = {}
        self._category_cache: dict[str, uuid.UUID] = {}
        self._comment_mapping = CommunicationToCommentMapping()

    # Fields to fetch from ERPNext for consistent sync.
    # Include both ticket_type (HD Ticket) and issue_type (Issue) —
    # ERPNext API silently ignores fields that don't exist on the DocType.
    SYNC_FIELDS = [
        "name",
        "subject",
        "description",
        "status",
        "priority",
        "ticket_type",  # HD Ticket (v14+) type field
        "issue_type",  # Issue (v13) type field
        "raised_by",
        "owner",
        "opening_date",
        "resolution_date",
        "resolution_details",
        "project",  # Only in Issue, not HD Ticket
        "customer",  # Customer linked to the ticket
        "modified",
    ]

    def fetch_records(self, client: Any, since: datetime | None = None):
        """
        Fetch issues/tickets from ERPNext with comments and communications.

        For each ticket, also fetches Comment and Communication child records
        and attaches them to the record for downstream processing.
        """
        try:
            if since:
                records = client.get_modified_since(
                    doctype=self.source_doctype,
                    since=since,
                    fields=self.SYNC_FIELDS,
                )
            else:
                records = client.get_issues(include_closed=True)
        except ERPNextError as e:
            if e.status_code == 404 and self.source_doctype == "HD Ticket":
                logger.warning("HD Ticket DocType not found, falling back to Issue")
                self._switch_to_issue_doctype()
                if since:
                    records = client.get_modified_since(
                        doctype="Issue",
                        since=since,
                        fields=self.SYNC_FIELDS,
                    )
                else:
                    records = client.get_issues(include_closed=True)
            else:
                raise

        for record in records:
            ticket_name = str(record.get("name", ""))
            if ticket_name:
                # Fetch comments and communications for this ticket
                comments = client.get_comments_for_doc(self.source_doctype, ticket_name)
                communications = client.get_communications_for_doc(
                    self.source_doctype, ticket_name
                )
                record["_comments_raw"] = comments
                record["_communications_raw"] = communications
            else:
                record["_comments_raw"] = []
                record["_communications_raw"] = []

            yield record

    def _switch_to_issue_doctype(self) -> None:
        """Switch from HD Ticket to Issue DocType."""
        self.source_doctype = "Issue"
        self._mapping = TicketMapping(doctype="Issue")

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        result = self._mapping.transform_record(record)
        # Pass through comment/communication data for downstream processing
        result["_comments_raw"] = record.get("_comments_raw", [])
        result["_communications_raw"] = record.get("_communications_raw", [])
        # Ensure _issue_type is populated regardless of which DocType field
        # was present — HD Ticket uses "ticket_type", Issue uses "issue_type"
        if not result.get("_issue_type"):
            result["_issue_type"] = record.get("ticket_type") or record.get(
                "issue_type"
            )
        return result

    def _resolve_project_id(self, source_name: str | None) -> uuid.UUID | None:
        """Resolve project ID from ERPNext project name."""
        if not source_name:
            return None

        from app.models.sync import SyncEntity

        sync_entity = self.db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Project",
                SyncEntity.source_name == source_name,
            )
        ).scalar_one_or_none()

        if sync_entity and sync_entity.target_id:
            return sync_entity.target_id
        return None

    def _resolve_customer_id(self, source_name: str | None) -> uuid.UUID | None:
        """Resolve customer ID from ERPNext customer name."""
        if not source_name:
            return None

        from app.models.sync import SyncEntity

        sync_entity = self.db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Customer",
                SyncEntity.source_name == source_name,
            )
        ).scalar_one_or_none()

        if sync_entity and sync_entity.target_id:
            return sync_entity.target_id
        return None

    def _resolve_employee_by_email(self, email: str | None) -> uuid.UUID | None:
        """
        Resolve employee ID from email address.

        Checks multiple email fields:
        - Person.email (work email)
        - Employee.personal_email
        """
        if not email:
            return None

        from app.models.people.hr import Employee
        from app.models.person import Person

        email_lower = email.strip().lower()

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

        if result:
            return result.employee_id
        return None

    def _resolve_person_by_email(self, email: str | None) -> uuid.UUID | None:
        """
        Resolve Person ID from email address.

        TicketComment.author_id is FK to Person (not Employee), so we need
        to resolve via Person.email, then fall back to Employee.personal_email
        → Employee.person_id.

        Results are cached to avoid repeated lookups for the same sender.
        """
        if not email:
            return None

        email_lower = email.strip().lower()

        if email_lower in self._person_cache:
            return self._person_cache[email_lower]

        from app.models.people.hr import Employee
        from app.models.person import Person

        # First try direct Person.email match
        person = self.db.execute(
            select(Person).where(
                Person.organization_id == self.organization_id,
                func.lower(Person.email) == email_lower,
            )
        ).scalar_one_or_none()

        if person:
            self._person_cache[email_lower] = person.id
            return person.id

        # Fall back to Employee.personal_email → Employee.person_id
        employee = self.db.execute(
            select(Employee).where(
                Employee.organization_id == self.organization_id,
                func.lower(Employee.personal_email) == email_lower,
            )
        ).scalar_one_or_none()

        if employee and employee.person_id:
            self._person_cache[email_lower] = employee.person_id
            return employee.person_id

        self._person_cache[email_lower] = None
        return None

    def _resolve_or_create_category(self, issue_type: str | None) -> uuid.UUID | None:
        """
        Resolve or auto-create a TicketCategory from ERPNext issue_type.

        Slugifies the issue_type to a category_code (e.g. "Application Issue" → "APPLICATION-ISSUE")
        and looks up or creates the corresponding TicketCategory.

        Results are cached per issue_type string within the sync run.
        """
        if not issue_type:
            return None

        # Check cache first
        if issue_type in self._category_cache:
            return self._category_cache[issue_type]

        from app.models.support.category import TicketCategory

        # Slugify to code: "Application Issue" → "APPLICATION-ISSUE"
        code = issue_type.strip().upper().replace(" ", "-")[:20]

        cat = self.db.execute(
            select(TicketCategory).where(
                TicketCategory.organization_id == self.organization_id,
                TicketCategory.category_code == code,
            )
        ).scalar_one_or_none()

        if cat:
            self._category_cache[issue_type] = cat.category_id
            return cat.category_id

        # Auto-create from ERPNext issue_type
        cat = TicketCategory(
            organization_id=self.organization_id,
            category_code=code,
            category_name=issue_type.strip(),  # Preserve original casing
        )
        self.db.add(cat)
        self.db.flush()

        logger.info(
            "Auto-created TicketCategory '%s' (code=%s) from ERPNext issue_type",
            issue_type,
            code,
        )

        self._category_cache[issue_type] = cat.category_id
        return cat.category_id

    def create_entity(self, data: dict[str, Any]) -> Ticket:
        # Extract temporary fields used for resolution
        project_source = data.pop("_project_source_name", None)
        customer_source = data.pop("_customer_source_name", None)
        owner_email = data.pop("_owner_email", None)
        issue_type = data.pop("_issue_type", None)
        data.pop("_source_modified", None)
        data.pop("_source_name", None)
        ticket_number = data["ticket_number"]

        # Extract comment data before creating the ticket
        comments_raw = data.pop("_comments_raw", [])
        communications_raw = data.pop("_communications_raw", [])

        # Resolve project reference
        project_id = self._resolve_project_id(project_source)
        if project_source and not project_id:
            logger.warning(
                "Could not resolve project '%s' for ticket '%s' - project may not be synced yet",
                project_source,
                ticket_number,
            )

        # Resolve customer reference
        customer_id = self._resolve_customer_id(customer_source)
        if customer_source and not customer_id:
            logger.warning(
                "Could not resolve customer '%s' for ticket '%s' - customer may not be synced yet",
                customer_source,
                ticket_number,
            )

        # Resolve raised_by employee from email
        raised_by_email = data.get("raised_by_email")
        raised_by_id = self._resolve_employee_by_email(raised_by_email)
        if raised_by_email and not raised_by_id:
            logger.debug(
                "Could not resolve raised_by employee for email '%s' on ticket '%s'",
                raised_by_email,
                ticket_number,
            )

        # Resolve assigned_to employee from owner email
        assigned_to_id = self._resolve_employee_by_email(owner_email)
        if owner_email and not assigned_to_id:
            logger.debug(
                "Could not resolve assigned_to employee for email '%s' on ticket '%s'",
                owner_email,
                ticket_number,
            )

        # Resolve category from issue_type
        category_id = self._resolve_or_create_category(issue_type)

        # Map status
        status_str = data.get("status", "OPEN")
        try:
            status = TicketStatus(status_str)
        except ValueError:
            status = TicketStatus.OPEN

        # Map priority
        priority_str = data.get("priority", "MEDIUM")
        try:
            priority = TicketPriority(priority_str)
        except ValueError:
            priority = TicketPriority.MEDIUM

        ticket = Ticket(
            organization_id=self.organization_id,
            ticket_number=ticket_number[:50],
            subject=data["subject"][:255],
            description=data.get("description"),
            status=status,
            priority=priority,
            raised_by_email=raised_by_email,
            raised_by_id=raised_by_id,
            assigned_to_id=assigned_to_id,
            project_id=project_id,
            customer_id=customer_id,
            category_id=category_id,
            resolution=data.get("resolution"),
            opening_date=data["opening_date"],
            resolution_date=data.get("resolution_date"),
            # created_by_id not set for synced records
        )

        # Flush to get ticket_id
        self.db.add(ticket)
        self.db.flush()

        # Sync comments and communications
        self._sync_comments(ticket, comments_raw, communications_raw)

        return ticket

    def update_entity(self, entity: Ticket, data: dict[str, Any]) -> Ticket:
        # Extract temporary fields used for resolution
        project_source = data.pop("_project_source_name", None)
        customer_source = data.pop("_customer_source_name", None)
        owner_email = data.pop("_owner_email", None)
        issue_type = data.pop("_issue_type", None)
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        # Extract comment data
        comments_raw = data.pop("_comments_raw", [])
        communications_raw = data.pop("_communications_raw", [])

        # Resolve project if provided
        if project_source:
            project_id = self._resolve_project_id(project_source)
            if project_id:
                entity.project_id = project_id
            else:
                logger.warning(
                    "Could not resolve project '%s' for ticket '%s'",
                    project_source,
                    entity.ticket_number,
                )

        # Resolve customer if provided
        if customer_source:
            customer_id = self._resolve_customer_id(customer_source)
            if customer_id:
                entity.customer_id = customer_id
            else:
                logger.warning(
                    "Could not resolve customer '%s' for ticket '%s'",
                    customer_source,
                    entity.ticket_number,
                )
        elif entity.customer_id:
            # Customer was removed in ERPNext
            entity.customer_id = None

        entity.subject = data["subject"][:255]
        entity.description = data.get("description")

        # Update raised_by and try to resolve employee if not already set
        raised_by_email = data.get("raised_by_email")
        entity.raised_by_email = raised_by_email
        if raised_by_email and not entity.raised_by_id:
            entity.raised_by_id = self._resolve_employee_by_email(raised_by_email)

        # Update assigned_to from owner email if not already set
        if owner_email and not entity.assigned_to_id:
            assigned_to_id = self._resolve_employee_by_email(owner_email)
            if assigned_to_id:
                entity.assigned_to_id = assigned_to_id

        entity.resolution = data.get("resolution")
        entity.resolution_date = data.get("resolution_date")

        # Resolve category from issue_type
        category_id = self._resolve_or_create_category(issue_type)
        if category_id:
            entity.category_id = category_id

        # Map status
        status_str = data.get("status", "OPEN")
        try:
            entity.status = TicketStatus(status_str)
        except ValueError:
            pass

        # Map priority
        priority_str = data.get("priority", "MEDIUM")
        try:
            entity.priority = TicketPriority(priority_str)
        except ValueError:
            pass

        # Sync new comments (deduplication happens inside)
        self._sync_comments(entity, comments_raw, communications_raw)

        # Don't set updated_by_id for synced records
        return entity

    def get_entity_id(self, entity: Ticket) -> uuid.UUID:
        return entity.ticket_id

    def find_existing_entity(self, source_name: str) -> Ticket | None:
        # Ensure source_name is always a string
        source_name_str = str(source_name) if source_name is not None else ""

        if source_name_str in self._ticket_cache:
            return self._ticket_cache[source_name_str]

        sync_entity = self.get_sync_entity(source_name_str)
        if sync_entity and sync_entity.target_id:
            ticket = self.db.get(Ticket, sync_entity.target_id)
            if ticket:
                self._ticket_cache[source_name_str] = ticket
                return ticket

        # Fallback: try to find by ticket number
        result = self.db.execute(
            select(Ticket).where(
                Ticket.organization_id == self.organization_id,
                Ticket.ticket_number == source_name_str[:50],
            )
        ).scalar_one_or_none()

        if result:
            self._ticket_cache[source_name_str] = result
            return result

        return None

    def _sync_comments(
        self,
        ticket: Ticket,
        comments_raw: list[dict[str, Any]],
        communications_raw: list[dict[str, Any]],
    ) -> int:
        """
        Sync ERPNext Comment and Communication records as TicketComment entries.

        Uses SyncEntity for deduplication: each ERPNext Comment/Communication
        is tracked with source_doctype="Comment"/"Communication" so re-running
        sync only creates new comments.

        Args:
            ticket: The DotMac ticket to attach comments to
            comments_raw: Raw ERPNext Comment records
            communications_raw: Raw ERPNext Communication records

        Returns:
            Number of comments created
        """

        created = 0

        # Process Comments
        for raw in comments_raw:
            mapped = self._comment_mapping.map_comment(raw)
            if self._create_comment_if_new(ticket, mapped):
                created += 1

        # Process Communications
        for raw in communications_raw:
            mapped = self._comment_mapping.map_communication(raw)
            if self._create_comment_if_new(ticket, mapped):
                created += 1

        if created:
            self.db.flush()
            logger.debug(
                "Created %d comments for ticket %s", created, ticket.ticket_number
            )

        return created

    def _create_comment_if_new(
        self,
        ticket: Ticket,
        mapped: dict[str, Any],
    ) -> bool:
        """
        Create a TicketComment if it hasn't been synced before.

        Uses SyncEntity with source_doctype="Comment"/"Communication"
        for dedup tracking.

        Returns True if a new comment was created.
        """
        from app.models.sync import SyncEntity, SyncStatus

        source_doctype = mapped["source_doctype"]
        source_name = str(mapped["source_name"])

        if not source_name:
            return False

        # Check if already synced
        existing = self.db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == source_doctype,
                SyncEntity.source_name == source_name,
            )
        ).scalar_one_or_none()

        if existing and existing.sync_status == SyncStatus.SYNCED:
            return False

        # Resolve author from sender email
        author_id = self._resolve_person_by_email(mapped.get("sender_email"))

        # Map comment type
        comment_type_str = mapped.get("comment_type", "COMMENT")
        try:
            comment_type = CommentType(comment_type_str)
        except ValueError:
            comment_type = CommentType.COMMENT

        comment = TicketComment(
            ticket_id=ticket.ticket_id,
            comment_type=comment_type,
            content=mapped["content"],
            author_id=author_id,
            is_internal=comment_type == CommentType.INTERNAL_NOTE,
        )

        # Preserve original creation timestamp from ERPNext
        created_at = mapped.get("created_at")
        if created_at and isinstance(created_at, str):
            try:
                comment.created_at = datetime.fromisoformat(created_at)
            except ValueError:
                pass  # Use server default

        self.db.add(comment)
        self.db.flush()

        # Create or update sync entity for dedup tracking
        if existing:
            existing.target_id = comment.comment_id
            existing.target_table = "support.ticket_comment"
            existing.mark_synced(comment.comment_id)
        else:
            sync_entity = SyncEntity(
                organization_id=self.organization_id,
                source_system="erpnext",
                source_doctype=source_doctype,
                source_name=source_name,
                target_table="support.ticket_comment",
                target_id=comment.comment_id,
                sync_status=SyncStatus.SYNCED,
            )
            self.db.add(sync_entity)

        return True
