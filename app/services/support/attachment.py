"""
Ticket Attachment Service.

Handles file uploads and attachments for support tickets.
"""

import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import BinaryIO, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.support.attachment import TicketAttachment

logger = logging.getLogger(__name__)

# Allowed file extensions and MIME types
ALLOWED_EXTENSIONS = {
    # Images
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    # Documents
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".txt": "text/plain",
    # Archives
    ".zip": "application/zip",
}

# Maximum file size (10MB)
MAX_FILE_SIZE = 10 * 1024 * 1024


class AttachmentService:
    """Service for managing ticket attachments."""

    def __init__(self, upload_dir: str = "/app/uploads/support"):
        """
        Initialize attachment service.

        Args:
            upload_dir: Base directory for file uploads
        """
        self.upload_dir = Path(upload_dir).resolve()
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def _validate_path_within_upload_dir(self, path: Path) -> Path:
        """
        Validate that a path is within the upload directory.

        Prevents path traversal attacks via symlinks or malicious paths.

        Args:
            path: Path to validate

        Returns:
            Resolved absolute path

        Raises:
            ValueError: If path is outside upload directory
        """
        resolved = path.resolve()
        try:
            resolved.relative_to(self.upload_dir)
        except ValueError:
            raise ValueError(
                f"Path traversal attempt detected: {path} is outside upload directory"
            )
        return resolved

    def list_attachments(
        self,
        db: Session,
        ticket_id: uuid.UUID,
        include_deleted: bool = False,
    ) -> List[TicketAttachment]:
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
    ) -> Optional[TicketAttachment]:
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
        content_type: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate a file for upload.

        Args:
            filename: Original filename
            file_size: File size in bytes
            content_type: MIME type (optional)

        Returns:
            (is_valid, error_message)
        """
        # Check file size
        if file_size > MAX_FILE_SIZE:
            return (
                False,
                f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)}MB",
            )

        # Check extension
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return (
                False,
                f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS.keys())}",
            )

        return True, None

    def save_file(
        self,
        db: Session,
        ticket_id: uuid.UUID,
        filename: str,
        file_data: BinaryIO,
        content_type: str,
        uploaded_by_id: Optional[uuid.UUID] = None,
        comment_id: Optional[uuid.UUID] = None,
    ) -> Tuple[Optional[TicketAttachment], Optional[str]]:
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
        # Read file content
        content = file_data.read()
        file_size = len(content)

        # Validate
        is_valid, error = self.validate_file(filename, file_size, content_type)
        if not is_valid:
            return None, error

        # Generate unique storage path
        file_hash = hashlib.sha256(content).hexdigest()[:16]
        ext = os.path.splitext(filename)[1].lower()
        storage_filename = f"{uuid.uuid4().hex}_{file_hash}{ext}"

        # Organize by ticket
        ticket_dir = self.upload_dir / str(ticket_id)
        ticket_dir.mkdir(parents=True, exist_ok=True)

        storage_path = ticket_dir / storage_filename

        # Validate path is within upload directory (prevent path traversal)
        try:
            storage_path = self._validate_path_within_upload_dir(storage_path)
        except ValueError as e:
            logger.error("Path validation failed: %s", e)
            return None, "Invalid file path"

        # Save file
        try:
            with open(storage_path, "wb") as f:
                f.write(content)
        except Exception as e:
            logger.exception("Failed to save file: %s", e)
            return None, "Failed to save file"

        # Create database record
        attachment = TicketAttachment(
            ticket_id=ticket_id,
            comment_id=comment_id,
            filename=filename,
            storage_path=str(storage_path),
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
    ) -> Tuple[bool, Optional[str]]:
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
            # Delete file from disk
            try:
                storage_path = Path(attachment.storage_path)
                # Validate path is within upload directory (prevent path traversal)
                validated_path = self._validate_path_within_upload_dir(storage_path)
                if validated_path.exists():
                    validated_path.unlink()
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
    ) -> Optional[str]:
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

        # Validate path is within upload directory (prevent path traversal)
        try:
            validated_path = self._validate_path_within_upload_dir(
                Path(attachment.storage_path)
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
