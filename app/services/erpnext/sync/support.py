"""
Support/Ticket Sync Service - ERPNext to DotMac ERP.

Syncs ERPNext Issue (or HD Ticket) DocType to DotMac support.ticket.
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.support.ticket import Ticket, TicketPriority, TicketStatus
from app.services.erpnext.client import ERPNextError
from app.services.erpnext.mappings.support import TicketMapping

from .base import BaseSyncService

logger = logging.getLogger(__name__)


class TicketSyncService(BaseSyncService[Ticket]):
    """Sync Tickets/Issues from ERPNext."""

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

    # Fields to fetch from ERPNext for consistent sync
    SYNC_FIELDS = [
        "name",
        "subject",
        "description",
        "status",
        "priority",
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
        Fetch issues/tickets from ERPNext.

        Uses consistent field selection for both full and incremental sync
        to ensure all required data is always fetched.

        Args:
            client: ERPNext client instance
            since: If provided, only fetch records modified since this time

        Yields:
            Issue/HD Ticket documents from ERPNext
        """
        try:
            if since:
                # Incremental sync with explicit fields
                yield from client.get_modified_since(
                    doctype=self.source_doctype,
                    since=since,
                    fields=self.SYNC_FIELDS,
                )
            else:
                # Full sync - use get_issues which handles HD Ticket fallback
                yield from client.get_issues(include_closed=True)
        except ERPNextError as e:
            # If HD Ticket not found, fall back to Issue
            if e.status_code == 404 and self.source_doctype == "HD Ticket":
                logger.warning("HD Ticket DocType not found, falling back to Issue")
                self._switch_to_issue_doctype()
                # Retry with Issue DocType
                if since:
                    yield from client.get_modified_since(
                        doctype="Issue",
                        since=since,
                        fields=self.SYNC_FIELDS,
                    )
                else:
                    yield from client.get_issues(include_closed=True)
            else:
                raise

    def _switch_to_issue_doctype(self):
        """Switch from HD Ticket to Issue DocType."""
        self.source_doctype = "Issue"
        self._mapping = TicketMapping(doctype="Issue")

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._mapping.transform_record(record)

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

        Args:
            email: Email address to search for

        Returns:
            Employee UUID if found, None otherwise
        """
        if not email:
            return None

        from app.models.people.hr import Employee
        from app.models.person import Person

        # Normalize email for comparison
        email_lower = email.strip().lower()

        # Try to find employee by work email (Person.email) or personal email
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

    def create_entity(self, data: dict[str, Any]) -> Ticket:
        # Extract temporary fields used for resolution
        project_source = data.pop("_project_source_name", None)
        customer_source = data.pop("_customer_source_name", None)
        owner_email = data.pop("_owner_email", None)
        data.pop("_source_modified", None)
        data.pop("_source_name", None)
        ticket_number = data["ticket_number"]

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
            resolution=data.get("resolution"),
            opening_date=data["opening_date"],
            resolution_date=data.get("resolution_date"),
            # created_by_id not set for synced records
        )
        return ticket

    def update_entity(self, entity: Ticket, data: dict[str, Any]) -> Ticket:
        # Extract temporary fields used for resolution
        project_source = data.pop("_project_source_name", None)
        customer_source = data.pop("_customer_source_name", None)
        owner_email = data.pop("_owner_email", None)
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

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

        # Don't set updated_by_id for synced records - the user_id may not exist in people table
        return entity

    def get_entity_id(self, entity: Ticket) -> uuid.UUID:
        return entity.ticket_id

    def find_existing_entity(self, source_name: str) -> Ticket | None:
        # Ensure source_name is always a string (ERPNext may return numeric IDs as int)
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
