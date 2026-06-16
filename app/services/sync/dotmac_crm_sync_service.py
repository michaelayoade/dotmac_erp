"""
DotMac CRM Sync Service - Business logic for CRM entity synchronization.

The implementation is split into cohesive mixins under ``app.services.sync.crm``
(project/ticket/task sync, inventory, expense totals, directory, procurement)
over a shared ``_CRMSyncBase`` (session handle + mapping-store + resolvers).
``DotMacCRMSyncService`` composes them, preserving the original public API and
this import path.

Handles:
- Syncing projects, tickets, and work orders from DotMac CRM
- Mapping CRM entities to local ERP entities
- Providing expense totals for CRM entities
- Workforce/department, company/person contacts for CRM
- Material request creation from CRM
"""

from __future__ import annotations

# Re-exported so existing import sites keep resolving against this module.
from app.services.sync.crm.directory import _DirectoryMixin
from app.services.sync.crm.expenses import _ExpenseTotalsMixin
from app.services.sync.crm.inventory import _InventoryMixin
from app.services.sync.crm.procurement import _ProcurementMixin
from app.services.sync.crm.projects import _ProjectSyncMixin
from app.services.sync.crm_mappings import (
    CRM_SYNC_STATUS_MAP,
    PROJECT_STATUS_MAP,
    TASK_STATUS_MAP,
    TICKET_STATUS_MAP,
)

__all__ = [
    "DotMacCRMSyncService",
    "PROJECT_STATUS_MAP",
    "TICKET_STATUS_MAP",
    "TASK_STATUS_MAP",
    "CRM_SYNC_STATUS_MAP",
    "VALID_LOCAL_ENTITY_TYPES",
]

# Valid local_entity_type values for CRMSyncMapping
VALID_LOCAL_ENTITY_TYPES = frozenset({"project", "ticket", "task", "purchase_order"})


class DotMacCRMSyncService(
    _ProjectSyncMixin,
    _InventoryMixin,
    _ExpenseTotalsMixin,
    _DirectoryMixin,
    _ProcurementMixin,
):
    """Service for syncing entities from DotMac CRM.

    Composes the per-domain mixins; all behaviour lives in
    ``app.services.sync.crm.*`` over the shared ``_CRMSyncBase``.
    """
