"""
CRM Sync Services.

Services for syncing data from CRM to ERP:
- Tickets (support.ticket)
- Projects (core_org.project)
- (Future: Tasks, Field Services)
"""

from .base import CRM_SOURCE_SYSTEM, BaseCRMSyncService, SyncResult
from .projects import ProjectSyncService
from .tickets import TicketSyncService

__all__ = [
    "BaseCRMSyncService",
    "CRM_SOURCE_SYSTEM",
    "ProjectSyncService",
    "SyncResult",
    "TicketSyncService",
]
