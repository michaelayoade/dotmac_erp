"""
Sync Models - External system sync state tracking.

Tracks typed observations and migration state from external systems.
"""

from .integration_config import IntegrationConfig, IntegrationType
from .source_correlation import (
    SourceCorrelation,
    SourceCorrelationStatus,
    SourceEntityType,
)
from .staging import (
    StagingDepartment,
    StagingDesignation,
    StagingEmployee,
    StagingEmployeeGrade,
    StagingEmploymentType,
    StagingStatus,
    StagingSyncBatch,
)
from .sync_entity import SyncEntity, SyncStatus
from .sync_history import SyncHistory, SyncJobStatus, SyncType

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
    "SourceCorrelation",
    "SourceCorrelationStatus",
    "SourceEntityType",
]
