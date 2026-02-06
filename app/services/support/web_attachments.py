"""
Support Web Service - Attachments Module.

Handles file upload and attachment-related template responses.
"""

import logging
from typing import TYPE_CHECKING

from fastapi import Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.common import coerce_uuid
from app.services.support.attachment import attachment_service
from app.services.support.ticket import ticket_service

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

logger = logging.getLogger(__name__)


class AttachmentWebService:
    """Web service for attachment-related operations."""

    async def upload_attachment_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        ticket_id: str,
        file: UploadFile,
    ) -> RedirectResponse:
        """Upload an attachment to a ticket."""
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
            attachment, error = attachment_service.save_file(
                db,
                ticket_id=tid,
                filename=file.filename or "unnamed",
                file_data=file.file,
                content_type=file.content_type or "application/octet-stream",
                uploaded_by_id=user_id,
            )

            if error:
                return RedirectResponse(
                    url=f"/support/tickets/{ticket_id}?error={error.replace(' ', '+')}",
                    status_code=303,
                )

            db.commit()

        except Exception:
            db.rollback()
            logger.exception("Failed to upload attachment")
            return RedirectResponse(
                url=f"/support/tickets/{ticket_id}?error=Upload+failed",
                status_code=303,
            )

        return RedirectResponse(
            url=f"/support/tickets/{ticket_id}#attachments",
            status_code=303,
        )

    def download_attachment_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        ticket_id: str,
        attachment_id: str,
    ) -> FileResponse | RedirectResponse:
        """Download an attachment."""
        org_id = coerce_uuid(auth.organization_id)
        aid = coerce_uuid(attachment_id)

        attachment = attachment_service.get_attachment(db, org_id, aid)
        if not attachment:
            return RedirectResponse(
                url=f"/support/tickets/{ticket_id}?error=Attachment+not+found",
                status_code=303,
            )

        file_path = attachment_service.get_file_path(db, org_id, aid)
        if not file_path:
            return RedirectResponse(
                url=f"/support/tickets/{ticket_id}?error=File+not+found",
                status_code=303,
            )

        return FileResponse(
            path=file_path,
            filename=attachment.filename,
            media_type=attachment.content_type,
        )

    def delete_attachment_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        ticket_id: str,
        attachment_id: str,
    ) -> RedirectResponse:
        """Delete an attachment."""
        org_id = coerce_uuid(auth.organization_id)
        aid = coerce_uuid(attachment_id)

        try:
            attachment_service.delete_attachment(db, org_id, aid)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to delete attachment")

        return RedirectResponse(
            url=f"/support/tickets/{ticket_id}#attachments",
            status_code=303,
        )


# Singleton instance
attachment_web_service = AttachmentWebService()
