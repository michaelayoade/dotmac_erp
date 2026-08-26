"""
Sub → ERP translation layer for Dotmac Sub sync.

Pure, side-effect-free mapping policy extracted from
``dotmac_sub_sync_service``: the Sub-status → ERP-enum lookup tables, the
enum translators, and the integrity-conflict classifier. Nothing here touches
the database or the ORM session, so it is independently unit-testable and can
evolve without dragging the sync-orchestration class along.

``dotmac_sub_sync_service`` re-imports these names into its own namespace, so
the canonical import sites (``from app.services.sync.dotmac_sub_sync_service
import PROJECT_STATUS_MAP``) and the ``DotMacSubSyncService._map_*`` instance
methods that delegate here continue to behave identically.
"""

from __future__ import annotations

from app.models.finance.core_org.project import ProjectStatus, ProjectType
from app.models.pm.task import TaskPriority, TaskStatus
from app.models.support.ticket import TicketPriority, TicketStatus
from app.models.sync.source_correlation import SourceCorrelationStatus

# ---------------------------------------------------------------------------
# Status lookup tables: Sub status strings → ERP enums
# ---------------------------------------------------------------------------

PROJECT_STATUS_MAP = {
    "planned": ProjectStatus.PLANNING,
    "active": ProjectStatus.ACTIVE,
    "on_hold": ProjectStatus.ON_HOLD,
    "completed": ProjectStatus.COMPLETED,
    "cancelled": ProjectStatus.CANCELLED,
    "canceled": ProjectStatus.CANCELLED,
}

TICKET_STATUS_MAP = {
    "open": TicketStatus.OPEN,
    "active": TicketStatus.OPEN,
    "in_progress": TicketStatus.REPLIED,
    "resolved": TicketStatus.RESOLVED,
    "closed": TicketStatus.CLOSED,
    "completed": TicketStatus.CLOSED,
    "cancelled": TicketStatus.CLOSED,
    "canceled": TicketStatus.CLOSED,
}

TASK_STATUS_MAP = {
    "backlog": TaskStatus.OPEN,
    "todo": TaskStatus.OPEN,
    "draft": TaskStatus.OPEN,
    "scheduled": TaskStatus.OPEN,
    "active": TaskStatus.IN_PROGRESS,
    "in_progress": TaskStatus.IN_PROGRESS,
    "blocked": TaskStatus.ON_HOLD,
    "on_hold": TaskStatus.ON_HOLD,
    "done": TaskStatus.COMPLETED,
    "completed": TaskStatus.COMPLETED,
    "cancelled": TaskStatus.CANCELLED,
    "canceled": TaskStatus.CANCELLED,
}

SOURCE_STATUS_MAP = {
    "active": SourceCorrelationStatus.ACTIVE,
    "planned": SourceCorrelationStatus.ACTIVE,
    "in_progress": SourceCorrelationStatus.ACTIVE,
    "open": SourceCorrelationStatus.ACTIVE,
    "completed": SourceCorrelationStatus.COMPLETED,
    "resolved": SourceCorrelationStatus.COMPLETED,
    "closed": SourceCorrelationStatus.COMPLETED,
    "cancelled": SourceCorrelationStatus.CANCELLED,
    "canceled": SourceCorrelationStatus.CANCELLED,
    "archived": SourceCorrelationStatus.ARCHIVED,
}


# ---------------------------------------------------------------------------
# Enum translators (pure)
# ---------------------------------------------------------------------------


def map_project_type(type_str: str | None) -> ProjectType:
    """Map Sub project type to local enum."""
    if not type_str:
        return ProjectType.CLIENT
    type_map = {
        "internal": ProjectType.INTERNAL,
        "client": ProjectType.CLIENT,
        "fiber": ProjectType.FIBER_OPTICS_INSTALLATION,
        "airfiber": ProjectType.AIR_FIBER_INSTALLATION,
    }
    return type_map.get(type_str.lower(), ProjectType.CLIENT)


def map_ticket_priority(priority_str: str | None) -> TicketPriority:
    """Map Sub priority to local enum."""
    if not priority_str:
        return TicketPriority.MEDIUM
    priority_map = {
        "low": TicketPriority.LOW,
        "medium": TicketPriority.MEDIUM,
        "high": TicketPriority.HIGH,
        "urgent": TicketPriority.URGENT,
        "critical": TicketPriority.URGENT,
    }
    return priority_map.get(priority_str.lower(), TicketPriority.MEDIUM)


def map_task_priority(priority_str: str | None) -> TaskPriority:
    """Map Sub priority to local enum."""
    if not priority_str:
        return TaskPriority.MEDIUM
    priority_map = {
        "lower": TaskPriority.LOW,
        "low": TaskPriority.LOW,
        "normal": TaskPriority.MEDIUM,
        "medium": TaskPriority.MEDIUM,
        "high": TaskPriority.HIGH,
        "urgent": TaskPriority.URGENT,
        "critical": TaskPriority.URGENT,
    }
    return priority_map.get(priority_str.lower(), TaskPriority.MEDIUM)


def map_sub_material_request_type(request_type: str):
    """Map Sub request type to local MaterialRequestType."""
    from app.models.inventory.material_request import MaterialRequestType

    request_type_map = {
        "PURCHASE": MaterialRequestType.PURCHASE,
        "TRANSFER": MaterialRequestType.TRANSFER,
        "ISSUE": MaterialRequestType.ISSUE,
        "MANUFACTURE": MaterialRequestType.MANUFACTURE,
    }
    value = request_type_map.get((request_type or "").strip().upper())
    if not value:
        raise ValueError(
            f"Invalid request_type: {request_type}. "
            f"Must be one of: {', '.join(request_type_map)}"
        )
    return value


def map_sub_material_request_status(status: str):
    """Map Sub status string to local MaterialRequestStatus."""
    from app.models.inventory.material_request import MaterialRequestStatus

    status_map = {
        "draft": MaterialRequestStatus.DRAFT,
        "submitted": MaterialRequestStatus.SUBMITTED,
        "pending_stock": MaterialRequestStatus.PENDING_STOCK,
        "pending stock": MaterialRequestStatus.PENDING_STOCK,
        "partially_ordered": MaterialRequestStatus.PARTIALLY_ORDERED,
        "ordered": MaterialRequestStatus.ORDERED,
        "issued": MaterialRequestStatus.ISSUED,
        "cancelled": MaterialRequestStatus.CANCELLED,
        "canceled": MaterialRequestStatus.CANCELLED,
    }
    mapped = status_map.get((status or "").strip().lower())
    if not mapped:
        raise ValueError(
            f"Invalid status: {status}. Must be one of: {', '.join(status_map)}"
        )
    return mapped
