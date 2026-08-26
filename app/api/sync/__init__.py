"""
Sync API - External system synchronization endpoints.
"""

from .dotmac_sub import router as sub_router

__all__ = [
    "sub_router",
]
