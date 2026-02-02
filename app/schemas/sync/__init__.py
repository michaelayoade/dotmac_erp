"""
Sync Schemas - Pydantic models for sync operations.
"""
from .dotmac_crm import (
    BulkSyncRequest,
    BulkSyncResponse,
    CRMProjectPayload,
    CRMProjectRead,
    CRMSyncMappingRead,
    CRMTicketPayload,
    CRMTicketRead,
    CRMWorkOrderPayload,
    CRMWorkOrderRead,
    ExpenseTotals,
    ExpenseTotalsRequest,
    ExpenseTotalsResponse,
    SyncError,
)

__all__ = [
    "BulkSyncRequest",
    "BulkSyncResponse",
    "CRMProjectPayload",
    "CRMProjectRead",
    "CRMSyncMappingRead",
    "CRMTicketPayload",
    "CRMTicketRead",
    "CRMWorkOrderPayload",
    "CRMWorkOrderRead",
    "ExpenseTotals",
    "ExpenseTotalsRequest",
    "ExpenseTotalsResponse",
    "SyncError",
]
