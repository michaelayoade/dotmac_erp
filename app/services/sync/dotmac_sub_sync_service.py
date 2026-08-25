"""
Dotmac Sub Sync Service - Business logic for Sub entity synchronization.

The implementation is split into cohesive mixins under ``app.services.sync.sub``
(project/ticket/task sync, inventory, expense totals, directory, procurement)
over a shared ``_SubSyncBase`` (session handle + mapping-store + resolvers).
``DotMacSubSyncService`` composes them, preserving the original public API and
this import path.

Handles:
- Syncing projects, tickets, and work orders from Dotmac Sub
- Mapping Sub entities to local ERP entities
- Providing expense totals for Sub entities
- Workforce/department, company/person contacts for Sub
- Material request creation from Sub
"""

from __future__ import annotations

from app.services.sync.sub.expenses import _ExpenseTotalsMixin
from app.services.sync.sub.inventory import _InventoryMixin
from app.services.sync.sub.procurement import _ProcurementMixin
from app.services.sync.sub.projects import _ProjectSyncMixin
from app.services.sync.sub_mappings import (
    SOURCE_STATUS_MAP,
    PROJECT_STATUS_MAP,
    TASK_STATUS_MAP,
    TICKET_STATUS_MAP,
)

__all__ = [
    "DotMacSubSyncService",
    "PROJECT_STATUS_MAP",
    "TICKET_STATUS_MAP",
    "TASK_STATUS_MAP",
    "SOURCE_STATUS_MAP",
    "VALID_LOCAL_ENTITY_TYPES",
]

# Valid local_entity_type values for SourceCorrelation
VALID_LOCAL_ENTITY_TYPES = frozenset({"project", "ticket", "task", "purchase_order"})


class DotMacSubSyncService(
    _ProjectSyncMixin,
    _InventoryMixin,
    _ExpenseTotalsMixin,
    _ProcurementMixin,
):
    """Service for syncing entities from Dotmac Sub.

    Composes the per-domain mixins; all behavior lives in
    ``app.services.sync.sub.*`` over the shared ``_SubSyncBase``.
    """
