"""
Support/Ticket Mapping from ERPNext to DotMac ERP.

Maps ERPNext Issue (or HD Ticket in v14+) DocType to DotMac support.ticket.
"""
from typing import Any

from .base import (
    DocTypeMapping,
    FieldMapping,
    clean_string,
    parse_date,
    parse_datetime,
)

from app.models.support.ticket import (
    TicketStatus,
    TicketPriority,
    ERPNEXT_STATUS_MAP,
    ERPNEXT_PRIORITY_MAP,
)


def map_ticket_status(value: Any) -> str:
    """Map ERPNext Issue status to DotMac TicketStatus."""
    if not value:
        return TicketStatus.OPEN.value
    mapped = ERPNEXT_STATUS_MAP.get(str(value), TicketStatus.OPEN)
    return mapped.value


def map_ticket_priority(value: Any) -> str:
    """Map ERPNext Issue priority to DotMac TicketPriority."""
    if not value:
        return TicketPriority.MEDIUM.value
    mapped = ERPNEXT_PRIORITY_MAP.get(str(value), TicketPriority.MEDIUM)
    return mapped.value


class TicketMapping(DocTypeMapping):
    """Map ERPNext Issue/HD Ticket to DotMac ERP support.ticket."""

    def __init__(self, doctype: str = "Issue"):
        """
        Initialize ticket mapping.

        Args:
            doctype: ERPNext DocType ("Issue" for v13, "HD Ticket" for v14+)
        """
        super().__init__(
            source_doctype=doctype,
            target_table="support.ticket",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                    # ERPNext may return numeric ticket names as integers
                    transformer=lambda v: str(v) if v is not None else "",
                ),
                FieldMapping(
                    source="subject",
                    target="subject",
                    required=True,
                    transformer=lambda v: clean_string(v, 255),
                ),
                FieldMapping(
                    source="description",
                    target="description",
                    required=False,
                ),
                # Status and Priority
                FieldMapping(
                    source="status",
                    target="status",
                    required=False,
                    default=TicketStatus.OPEN.value,
                    transformer=map_ticket_status,
                ),
                FieldMapping(
                    source="priority",
                    target="priority",
                    required=False,
                    default=TicketPriority.MEDIUM.value,
                    transformer=map_ticket_priority,
                ),
                # Raised by (email or user)
                FieldMapping(
                    source="raised_by",
                    target="raised_by_email",
                    required=False,
                    transformer=lambda v: clean_string(v, 255),
                ),
                # Owner/assigned-to (ERPNext user who owns the ticket)
                FieldMapping(
                    source="owner",
                    target="_owner_email",
                    required=False,
                    transformer=lambda v: clean_string(v, 255),
                ),
                # Project reference (if from Issue DocType)
                FieldMapping(
                    source="project",
                    target="_project_source_name",
                    required=False,
                ),
                # Customer reference (for customer support tickets)
                FieldMapping(
                    source="customer",
                    target="_customer_source_name",
                    required=False,
                ),
                # Dates
                FieldMapping(
                    source="opening_date",
                    target="opening_date",
                    required=True,
                    transformer=parse_date,
                ),
                FieldMapping(
                    source="resolution_date",
                    target="resolution_date",
                    required=False,
                    transformer=parse_date,
                ),
                # Resolution details
                FieldMapping(
                    source="resolution_details",
                    target="resolution",
                    required=False,
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                    required=False,
                    transformer=parse_datetime,
                ),
            ],
            unique_key="name",
        )

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform with ticket number generation."""
        result = super().transform_record(record)

        # Generate ticket number from ERPNext name
        # ERPNext Issue names are like "ISS-2025-00001" or "HD-2025-00001"
        erpnext_name = record.get("name", "")
        result["ticket_number"] = clean_string(erpnext_name, 50)

        return result


class HDTicketMapping(TicketMapping):
    """Map ERPNext HD Ticket (v14+) to DotMac ERP support.ticket."""

    def __init__(self):
        super().__init__(doctype="HD Ticket")
