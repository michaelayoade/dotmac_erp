"""
Common services shared across IFRS modules.
"""

from app.services.ifrs.common.attachment import attachment_service, AttachmentService
from app.services.ifrs.common.numbering import NumberingService, SyncNumberingService

__all__ = [
    "attachment_service",
    "AttachmentService",
    "NumberingService",
    "SyncNumberingService",
]
