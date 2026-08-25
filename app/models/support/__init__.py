"""
Support Module Models.

This module provides models for ERP-owned internal ticket tracking.
"""

from app.models.support.attachment import TicketAttachment
from app.models.support.category import TicketCategory
from app.models.support.comment import CommentType, TicketComment
from app.models.support.team import SupportTeam, SupportTeamMember
from app.models.support.ticket import Ticket, TicketPriority, TicketStatus

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
