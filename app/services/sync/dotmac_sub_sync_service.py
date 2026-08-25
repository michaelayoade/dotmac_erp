"""Service facade for the explicit Dotmac Sub intake contract."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Protocol, TypeVar
from uuid import UUID

from dotmac_kernel.db import conflict_savepoint

from app.schemas.sync.dotmac_sub import BulkSyncRequest, BulkSyncResponse, SyncError
from app.services.sync.sub.expenses import _ExpenseIntakeMixin
from app.services.sync.sub.inventory import _InventoryMixin
from app.services.sync.sub.procurement import _ProcurementMixin
from app.services.sync.sub.projects import _ProjectSyncMixin
from app.services.sync.sub_mappings import PROJECT_STATUS_MAP, TASK_STATUS_MAP

logger = logging.getLogger(__name__)


class _SourcePayload(Protocol):
    source_id: str


_PayloadT = TypeVar("_PayloadT", bound=_SourcePayload)

__all__ = [
    "DotmacSubSyncService",
    "PROJECT_STATUS_MAP",
    "TASK_STATUS_MAP",
]


class DotmacSubSyncService(
    _ProjectSyncMixin,
    _InventoryMixin,
    _ExpenseIntakeMixin,
    _ProcurementMixin,
):
    """Service for syncing entities from Dotmac Sub.

    Composes the surviving Sub-specific adapters over one correlation owner.
    """

    def bulk_sync(
        self, organization_id: UUID, payload: BulkSyncRequest
    ) -> BulkSyncResponse:
        """Project a dependency-ordered batch while isolating item failures."""
        errors: list[SyncError] = []
        projects_synced = self._sync_batch_items(
            organization_id,
            "project",
            payload.projects,
            self.sync_project,
            errors,
        )
        project_tasks_synced = self._sync_batch_items(
            organization_id,
            "project_task",
            payload.project_tasks,
            self.sync_project_task,
            errors,
        )
        work_orders_synced = self._sync_batch_items(
            organization_id,
            "work_order",
            payload.work_orders,
            self.sync_work_order,
            errors,
        )
        return BulkSyncResponse(
            projects_synced=projects_synced,
            project_tasks_synced=project_tasks_synced,
            work_orders_synced=work_orders_synced,
            errors=errors,
        )

    def _sync_batch_items(
        self,
        organization_id: UUID,
        entity_type: str,
        items: Iterable[_PayloadT],
        handler: Callable[[UUID, _PayloadT], object],
        errors: list[SyncError],
    ) -> int:
        synced = 0
        for item in items:
            try:
                with conflict_savepoint(self.db):
                    handler(organization_id, item)
                synced += 1
            except Exception as exc:
                logger.exception(
                    "Failed to project Sub %s %s", entity_type, item.source_id
                )
                errors.append(
                    SyncError(
                        entity_type=entity_type,
                        source_id=item.source_id,
                        error=str(exc)[:200],
                    )
                )
        return synced
