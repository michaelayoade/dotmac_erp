"""
Support Module Models.

This module provides models for helpdesk/support ticket tracking,
synced from ERPNext's Issue or HD Ticket DocTypes.
"""

from app.models.support.ticket import Ticket, TicketPriority, TicketStatus
from app.models.support.comment import TicketComment, CommentType
from app.models.support.attachment import TicketAttachment
from app.models.support.team import SupportTeam, SupportTeamMember
from app.models.support.category import TicketCategory

__all__ = [
    # Ticket
    "Ticket",
    "TicketStatus",
    "TicketPriority",
    # Comments
    "TicketComment",
    "CommentType",
    # Attachments
    "TicketAttachment",
    # Teams
    "SupportTeam",
    "SupportTeamMember",
    # Categories
    "TicketCategory",
]
