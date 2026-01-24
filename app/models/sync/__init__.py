"""
Sync Models - External system sync state tracking.

Tracks migration state from external systems (ERPNext) to DotMac ERP.
"""
from .integration_config import IntegrationConfig, IntegrationType
from .sync_entity import SyncEntity, SyncStatus
from .sync_history import SyncHistory, SyncJobStatus, SyncType
from .staging import (
    StagingDepartment,
    StagingDesignation,
    StagingEmployee,
    StagingEmployeeGrade,
    StagingEmploymentType,
    StagingStatus,
    StagingSyncBatch,
)

__all__ = [
    "IntegrationConfig",
    "IntegrationType",
    "SyncEntity",
    "SyncStatus",
    "SyncHistory",
    "SyncJobStatus",
    "SyncType",
    # Staging models
    "StagingDepartment",
    "StagingDesignation",
    "StagingEmployee",
    "StagingEmployeeGrade",
    "StagingEmploymentType",
    "StagingStatus",
    "StagingSyncBatch",
]
