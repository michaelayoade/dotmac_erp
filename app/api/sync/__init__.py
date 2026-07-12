"""
Sync API - External system synchronization endpoints.
"""

from .dotmac_crm import router as crm_router
from .dotmac_sub import router as sub_router

__all__ = [
    "crm_router",
    "sub_router",
]
