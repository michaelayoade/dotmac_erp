"""
Sync API - External system synchronization endpoints.
"""
from .dotmac_crm import router as crm_router

__all__ = [
    "crm_router",
]
