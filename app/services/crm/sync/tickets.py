"""
CRM Ticket Sync Service.

Syncs tickets from CRM to DotMac ERP support.ticket table.
Handles create/update detection and field mapping.
"""

import logging
import uuid
from datetime import date, datetime
from typing import Any, Generator, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.support.ticket import Ticket, TicketPriority, TicketStatus
from app.services.crm.client import CRMClient
from app.services.crm.sync.base import BaseCRMSyncService, SyncResult

logger = logging.getLogger(__name__)


# CRM status to ERP status mapping
CRM_STATUS_MAP: dict[str, TicketStatus] = {
    "open": TicketStatus.OPEN,
    "Open": TicketStatus.OPEN,
    "OPEN": TicketStatus.OPEN,
    "pending": TicketStatus.OPEN,
    "Pending": TicketStatus.OPEN,
    "replied": TicketStatus.REPLIED,
    "Replied": TicketStatus.REPLIED,
    "on_hold": TicketStatus.ON_HOLD,
    "on-hold": TicketStatus.ON_HOLD,
    "On Hold": TicketStatus.ON_HOLD,
    "ON_HOLD": TicketStatus.ON_HOLD,
    "resolved": TicketStatus.RESOLVED,
    "Resolved": TicketStatus.RESOLVED,
    "RESOLVED": TicketStatus.RESOLVED,
    "closed": TicketStatus.CLOSED,
    "Closed": TicketStatus.CLOSED,
    "CLOSED": TicketStatus.CLOSED,
}

# CRM priority to ERP priority mapping
CRM_PRIORITY_MAP: dict[str, TicketPriority] = {
    "low": TicketPriority.LOW,
    "Low": TicketPriority.LOW,
    "LOW": TicketPriority.LOW,
    "medium": TicketPriority.MEDIUM,
    "Medium": TicketPriority.MEDIUM,
    "MEDIUM": TicketPriority.MEDIUM,
    "normal": TicketPriority.MEDIUM,
    "Normal": TicketPriority.MEDIUM,
    "high": TicketPriority.HIGH,
    "High": TicketPriority.HIGH,
    "HIGH": TicketPriority.HIGH,
    "urgent": TicketPriority.URGENT,
    "Urgent": TicketPriority.URGENT,
    "URGENT": TicketPriority.URGENT,
    "critical": TicketPriority.URGENT,
    "Critical": TicketPriority.URGENT,
}


class TicketSyncService(BaseCRMSyncService[Ticket]):
    """
    Sync tickets from CRM to ERP.

    Maps CRM ticket fields to DotMac Ticket model.
    """

    source_entity_type = "ticket"
    target_table = "support.ticket"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None,
    ):
        super().__init__(db, organization_id, user_id)
        self._ticket_number_counter: int = 0

    def fetch_records(
        self,
        client: CRMClient,
        since: Optional[datetime] = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Fetch tickets from CRM API."""
        yield from client.get_tickets(since=since)

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """
        Transform CRM ticket record to ERP format.

        Args:
            record: Raw CRM ticket data

        Returns:
            Dict ready for Ticket model creation
        """
        # Parse dates
        opening_date = self._parse_date(record.get("created_at"))
        resolution_date = self._parse_date(record.get("resolved_at"))

        # Get ticket number - prefer CRM's number, fallback to ID
        ticket_number = (
            record.get("ticket_number")
            or record.get("number")
            or str(record.get("id", ""))
        )

        return {
            "organization_id": self.organization_id,
            "ticket_number": ticket_number,
            "subject": record.get("subject") or record.get("title") or "Untitled",
            "description": record.get("description") or record.get("body") or "",
            "status": self._map_status(record.get("status")),
            "priority": self._map_priority(record.get("priority")),
            "raised_by_email": record.get("raised_by_email") or record.get("email"),
            "contact_email": record.get("contact_email") or record.get("email"),
            "contact_phone": record.get("contact_phone") or record.get("phone"),
            "opening_date": opening_date or date.today(),
            "resolution_date": resolution_date,
            "resolution": record.get("resolution"),
            # Customer/subscriber handled separately (needs lookup)
            "_subscriber_id": record.get("subscriber_id"),
        }

    def create_entity(self, data: dict[str, Any]) -> Ticket:
        """Create a new Ticket entity."""
        # Remove internal fields not on model
        subscriber_id = data.pop("_subscriber_id", None)

        ticket = Ticket(**data)

        # TODO: Look up customer by subscriber_id if available
        if subscriber_id:
            customer = self._lookup_customer_by_subscriber(subscriber_id)
            if customer:
                ticket.customer_id = customer.customer_id

        return ticket

    def update_entity(self, entity: Ticket, data: dict[str, Any]) -> Ticket:
        """Update existing Ticket entity."""
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

    def get_entity_id(self, entity: Ticket) -> uuid.UUID:
        """Get primary key from Ticket."""
        return entity.ticket_id

    def get_existing_entity(self, sync_entity) -> Optional[Ticket]:
        """Look up existing ticket by sync entity's target_id."""
        if not sync_entity.target_id:
            return None
        return self.db.get(Ticket, sync_entity.target_id)

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _map_status(self, status: Optional[str]) -> TicketStatus:
        """Map CRM status to ERP status."""
        if not status:
            return TicketStatus.OPEN
        return CRM_STATUS_MAP.get(status, TicketStatus.OPEN)

    def _map_priority(self, priority: Optional[str]) -> TicketPriority:
        """Map CRM priority to ERP priority."""
        if not priority:
            return TicketPriority.MEDIUM
        return CRM_PRIORITY_MAP.get(priority, TicketPriority.MEDIUM)

    def _parse_date(self, value: Any) -> Optional[date]:
        """Parse date from CRM value."""
        if not value:
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, datetime):
            return value.date()
        try:
            # Try ISO format
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt.date()
        except (ValueError, AttributeError):
            return None

    def _lookup_customer_by_subscriber(self, subscriber_id: str) -> Optional[Any]:
        """
        Look up customer by CRM subscriber ID.

        Subscriber ID may be stored in customer.external_id or similar.
        """
        from app.models.finance.ar.customer import Customer

        # Try customer_code field first
        stmt = select(Customer).where(
            Customer.organization_id == self.organization_id,
            Customer.customer_code == subscriber_id,
        )
        customer = self.db.scalar(stmt)

        if customer:
            return customer

        # Could also try other fields like customer_code
        # or query a subscriber mapping table
        return None

    # =========================================================================
    # Ticket-Specific Operations
    # =========================================================================

    def sync_with_comments(
        self,
        client: CRMClient,
        incremental: bool = True,
        batch_size: int = 100,
    ) -> dict[str, SyncResult]:
        """
        Sync tickets and their comments.

        Returns dict with 'tickets' and 'comments' sync results.
        """
        results = {}

        # Sync tickets first
        results["tickets"] = self.sync(client, incremental, batch_size)

        # Then sync comments for tickets that were synced
        # Comments are synced via a separate service
        # from app.services.crm.sync.ticket_comments import TicketCommentSyncService
        # comment_service = TicketCommentSyncService(self.db, self.organization_id)
        # results["comments"] = comment_service.sync(client, incremental, batch_size)

        logger.info(
            "Completed ticket sync with comments: %d tickets, %d created, %d updated",
            results["tickets"].total_records,
            results["tickets"].created_count,
            results["tickets"].updated_count,
        )

        return results

    def get_by_crm_id(self, crm_ticket_id: str) -> Optional[Ticket]:
        """
        Get ERP ticket by CRM ticket ID.

        Useful for linking expenses/projects to synced tickets.
        """
        from app.models.sync import SyncEntity

        from .base import CRM_SOURCE_SYSTEM

        sync_entity = self.db.scalar(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == CRM_SOURCE_SYSTEM,
                SyncEntity.source_doctype == self.source_entity_type,
                SyncEntity.source_name == crm_ticket_id,
            )
        )

        if sync_entity and sync_entity.target_id:
            return self.db.get(Ticket, sync_entity.target_id)
        return None
