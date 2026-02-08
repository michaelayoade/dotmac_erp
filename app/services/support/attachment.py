"""
Ticket Attachment Service.

Handles file uploads and attachments for support tickets.
"""

import logging
import uuid
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.support.attachment import TicketAttachment
from app.services.file_upload import (
    FileUploadConfig,
    FileUploadError,
    FileUploadService,
    get_support_attachment_upload,
    resolve_safe_path,
)

logger = logging.getLogger(__name__)


class AttachmentService:
    """Service for managing ticket attachments."""

    def __init__(self, upload_dir: str = "/app/uploads/support"):
        """
        Initialize attachment service.

        Args:
            upload_dir: Base directory for file uploads
        """
        base_service = get_support_attachment_upload()
        base_path = base_service.base_path
        if upload_dir and Path(upload_dir).resolve() != base_path:
            cfg = base_service.config
            self.upload_service = FileUploadService(
                FileUploadConfig(
                    base_dir=str(Path(upload_dir).resolve()),
                    allowed_content_types=cfg.allowed_content_types,
                    max_size_bytes=cfg.max_size_bytes,
                    allowed_extensions=cfg.allowed_extensions,
                    require_magic_bytes=cfg.require_magic_bytes,
                    compute_checksum=cfg.compute_checksum,
                )
            )
        else:
            self.upload_service = base_service

    def list_attachments(
        self,
        db: Session,
        ticket_id: uuid.UUID,
        include_deleted: bool = False,
    ) -> list[TicketAttachment]:
        """
        List attachments for a ticket.

        Args:
            db: Database session
            ticket_id: Ticket UUID
            include_deleted: Include soft-deleted attachments

        Returns:
            List of attachments
        """
        query = select(TicketAttachment).where(TicketAttachment.ticket_id == ticket_id)

        if not include_deleted:
            query = query.where(TicketAttachment.is_deleted == False)  # noqa: E712

        query = query.order_by(TicketAttachment.created_at.desc())

        return list(db.execute(query).scalars().all())

    def get_attachment(
        self,
        db: Session,
        organization_id: uuid.UUID,
        attachment_id: uuid.UUID,
    ) -> TicketAttachment | None:
        """Get an attachment by ID, scoped to organization via ticket."""
        from app.models.support.ticket import Ticket

        return db.execute(
            select(TicketAttachment)
            .join(Ticket, TicketAttachment.ticket_id == Ticket.ticket_id)
            .where(
                TicketAttachment.attachment_id == attachment_id,
                Ticket.organization_id == organization_id,
            )
        ).scalar_one_or_none()

    def validate_file(
        self,
        filename: str,
        file_size: int,
        content_type: str | None = None,
    ) -> tuple[bool, str | None]:
        """
        Validate a file for upload.

        Args:
            filename: Original filename
            file_size: File size in bytes
            content_type: MIME type (optional)

        Returns:
            (is_valid, error_message)
        """
        try:
            self.upload_service.validate(content_type, filename, file_size, None)
        except FileUploadError as exc:
            return False, str(exc)
        return True, None

    def save_file(
        self,
        db: Session,
        ticket_id: uuid.UUID,
        filename: str,
        file_data: BinaryIO,
        content_type: str,
        uploaded_by_id: uuid.UUID | None = None,
        comment_id: uuid.UUID | None = None,
    ) -> tuple[TicketAttachment | None, str | None]:
        """
        Save an uploaded file.

        Args:
            db: Database session
            ticket_id: Ticket UUID
            filename: Original filename
            file_data: File content as binary stream
            content_type: MIME type
            uploaded_by_id: Uploader's person UUID
            comment_id: Optional comment to attach to

        Returns:
            (attachment, error_message)
        """
        content = file_data.read()
        file_size = len(content)

        is_valid, error = self.validate_file(filename, file_size, content_type)
        if not is_valid:
            return None, error

        try:
            upload_result = self.upload_service.save(
                content,
                content_type=content_type,
                subdirs=[str(ticket_id)],
                original_filename=filename,
            )
        except FileUploadError as exc:
            return None, str(exc)

        # Create database record
        attachment = TicketAttachment(
            ticket_id=ticket_id,
            comment_id=comment_id,
            filename=filename,
            storage_path=upload_result.relative_path,
            content_type=content_type,
            file_size=file_size,
            uploaded_by_id=uploaded_by_id,
        )
        db.add(attachment)
        db.flush()

        logger.info(
            "Saved attachment %s for ticket %s: %s (%d bytes)",
            attachment.attachment_id,
            ticket_id,
            filename,
            file_size,
        )

        return attachment, None

    def delete_attachment(
        self,
        db: Session,
        organization_id: uuid.UUID,
        attachment_id: uuid.UUID,
        hard_delete: bool = False,
    ) -> tuple[bool, str | None]:
        """
        Delete an attachment.

        Args:
            db: Database session
            organization_id: Organization UUID for tenant scoping
            attachment_id: Attachment UUID
            hard_delete: If true, delete file from disk too

        Returns:
            (success, error_message)
        """
        attachment = self.get_attachment(db, organization_id, attachment_id)
        if not attachment:
            return False, "Attachment not found"

        if hard_delete:
            try:
                if Path(attachment.storage_path).is_absolute():
                    validated_path = resolve_safe_path(
                        self.upload_service.base_path,
                        str(
                            Path(attachment.storage_path).relative_to(
                                self.upload_service.base_path
                            )
                        ),
                    )
                    if validated_path.exists():
                        validated_path.unlink()
                else:
                    self.upload_service.delete(attachment.storage_path)
            except ValueError as e:
                logger.error("Path validation failed during delete: %s", e)
            except Exception as e:
                logger.warning("Failed to delete file: %s", e)

            db.delete(attachment)
        else:
            attachment.is_deleted = True
            db.flush()

        logger.info("Deleted attachment %s", attachment_id)

        return True, None

    def get_file_path(
        self,
        db: Session,
        organization_id: uuid.UUID,
        attachment_id: uuid.UUID,
    ) -> str | None:
        """
        Get the file path for an attachment.

        Args:
            db: Database session
            organization_id: Organization UUID for tenant scoping
            attachment_id: Attachment UUID

        Returns:
            File path or None if not found
        """
        attachment = self.get_attachment(db, organization_id, attachment_id)
        if not attachment or attachment.is_deleted:
            return None

        try:
            if Path(attachment.storage_path).is_absolute():
                validated_path = Path(attachment.storage_path).resolve()
                validated_path.relative_to(self.upload_service.base_path)
            else:
                validated_path = resolve_safe_path(
                    self.upload_service.base_path, attachment.storage_path
                )
        except ValueError as e:
            logger.error("Path validation failed: %s", e)
            return None

        if not validated_path.exists():
            logger.warning("Attachment file not found: %s", validated_path)
            return None

        return str(validated_path)

    def get_stats(
        self,
        db: Session,
        ticket_id: uuid.UUID,
    ) -> dict:
        """
        Get attachment statistics for a ticket.

        Args:
            db: Database session
            ticket_id: Ticket UUID

        Returns:
            Dictionary with count and total size
        """
        attachments = self.list_attachments(db, ticket_id)

        return {
            "count": len(attachments),
            "total_size": sum(a.file_size for a in attachments),
            "image_count": sum(1 for a in attachments if a.is_image),
        }


# Singleton instance
attachment_service = AttachmentService()
