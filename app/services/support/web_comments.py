"""
Support Web Service - Comments Module.

Handles comment-related template responses.
"""

import logging
from typing import TYPE_CHECKING

from fastapi import Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.services.common import coerce_uuid
from app.services.support.attachment import attachment_service
from app.services.support.comment import comment_service
from app.services.support.ticket import ticket_service

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

logger = logging.getLogger(__name__)


class CommentWebService:
    """Web service for comment-related operations."""

    def add_comment_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        ticket_id: str,
        content: str,
        is_internal: bool = False,
        files: list[UploadFile] | None = None,
    ) -> RedirectResponse:
        """Add a comment to a ticket."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)
        tid = coerce_uuid(ticket_id)

        # Verify ticket exists
        ticket = ticket_service.get_ticket(db, org_id, tid)
        if not ticket:
            return RedirectResponse(
                url="/support/tickets?error=Ticket+not+found",
                status_code=303,
            )

        try:
            comment = comment_service.add_comment(
                db,
                ticket_id=tid,
                author_id=user_id,
                content=content,
                is_internal=is_internal,
            )
            upload_files = [f for f in (files or []) if getattr(f, "filename", None)]
            for file in upload_files:
                attachment, error = attachment_service.save_file(
                    db,
                    ticket_id=tid,
                    filename=file.filename or "unnamed",
                    file_data=file.file,
                    content_type=file.content_type or "application/octet-stream",
                    uploaded_by_id=user_id,
                    comment_id=comment.comment_id,
                )
                if error or not attachment:
                    logger.warning(
                        "Failed to attach file to comment: %s", error or "unknown error"
                    )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to add comment")

        return RedirectResponse(
            url=f"/support/tickets/{ticket.ticket_number}#comments?saved=1",
            status_code=303,
        )

    def delete_comment_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        ticket_id: str,
        comment_id: str,
    ) -> RedirectResponse:
        """Delete a comment."""
        org_id = coerce_uuid(auth.organization_id)
        cid = coerce_uuid(comment_id)

        try:
            comment_service.delete_comment(db, org_id, cid)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to delete comment")

        return RedirectResponse(
            url=f"/support/tickets/{ticket_id}#comments?saved=1",
            status_code=303,
        )


# Singleton instance
comment_web_service = CommentWebService()
