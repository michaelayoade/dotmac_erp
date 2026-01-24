"""
Support Module Services.

Business logic for helpdesk/support ticket management.
"""

from app.services.support.ticket import TicketService, ticket_service
from app.services.support.web import SupportWebService, support_web_service
from app.services.support.comment import CommentService, comment_service
from app.services.support.attachment import AttachmentService, attachment_service
from app.services.support.team import TeamService, team_service
from app.services.support.category import CategoryService, category_service
from app.services.support.web_comments import CommentWebService, comment_web_service
from app.services.support.web_attachments import AttachmentWebService, attachment_web_service
from app.services.support.web_teams import TeamWebService, team_web_service
from app.services.support.web_categories import CategoryWebService, category_web_service
from app.services.support.sla import SLAService, sla_service

__all__ = [
    # Ticket
    "TicketService",
    "ticket_service",
    # Web - Main
    "SupportWebService",
    "support_web_service",
    # Web - Comments
    "CommentWebService",
    "comment_web_service",
    # Web - Attachments
    "AttachmentWebService",
    "attachment_web_service",
    # Web - Teams
    "TeamWebService",
    "team_web_service",
    # Web - Categories
    "CategoryWebService",
    "category_web_service",
    # Comments
    "CommentService",
    "comment_service",
    # Attachments
    "AttachmentService",
    "attachment_service",
    # Teams
    "TeamService",
    "team_service",
    # Categories
    "CategoryService",
    "category_service",
    # SLA
    "SLAService",
    "sla_service",
]
