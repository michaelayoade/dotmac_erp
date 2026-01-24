"""
Project Attachment Service.

Handles file uploads and attachments for projects and tasks.
Uses the common.attachment model with polymorphic association.
"""

import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import BinaryIO, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.common.attachment import Attachment, AttachmentCategory

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


class ProjectAttachmentService:
    """Service for managing project and task attachments."""

    def __init__(self, upload_dir: str = "/app/uploads/projects"):
        """
        Initialize attachment service.

        Args:
            upload_dir: Base directory for file uploads
        """
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def list_attachments(
        self,
        db: Session,
        organization_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
    ) -> List[Attachment]:
        """
        List attachments for a project or task.

        Args:
            db: Database session
            organization_id: Organization UUID
            entity_type: 'PROJECT' or 'TASK'
            entity_id: Project or Task UUID

        Returns:
            List of attachments
        """
        query = select(Attachment).where(
            Attachment.organization_id == organization_id,
            Attachment.entity_type == entity_type,
            Attachment.entity_id == entity_id,
        ).order_by(Attachment.created_at.desc())

        return list(db.execute(query).scalars().all())

    def get_attachment(
        self,
        db: Session,
        organization_id: uuid.UUID,
        attachment_id: uuid.UUID,
    ) -> Optional[Attachment]:
        """Get an attachment by ID with org check."""
        attachment = db.get(Attachment, attachment_id)
        if attachment and attachment.organization_id == organization_id:
            return attachment
        return None

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
            return False, f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB"

        # Check extension
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return False, f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS.keys())}"

        return True, None

    def save_file(
        self,
        db: Session,
        organization_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        filename: str,
        file_data: BinaryIO,
        content_type: str,
        uploaded_by_id: uuid.UUID,
        description: Optional[str] = None,
    ) -> Tuple[Optional[Attachment], Optional[str]]:
        """
        Save an uploaded file.

        Args:
            db: Database session
            organization_id: Organization UUID
            entity_type: 'PROJECT' or 'TASK'
            entity_id: Project or Task UUID
            filename: Original filename
            file_data: File content as binary stream
            content_type: MIME type
            uploaded_by_id: Uploader's person UUID
            description: Optional description

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

        # Organize by entity type and ID
        entity_dir = self.upload_dir / entity_type.lower() / str(entity_id)
        entity_dir.mkdir(parents=True, exist_ok=True)

        storage_path = entity_dir / storage_filename

        # Save file
        try:
            with open(storage_path, "wb") as f:
                f.write(content)
        except Exception as e:
            logger.exception("Failed to save file: %s", e)
            return None, "Failed to save file"

        # Determine category
        category = AttachmentCategory.PROJECT if entity_type == "PROJECT" else AttachmentCategory.TASK

        # Create database record
        attachment = Attachment(
            organization_id=organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            file_name=filename,
            file_path=str(storage_path),
            content_type=content_type,
            file_size=file_size,
            category=category,
            description=description,
            storage_provider="LOCAL",
            checksum=hashlib.sha256(content).hexdigest(),
            uploaded_by=uploaded_by_id,
        )
        db.add(attachment)
        db.flush()

        logger.info(
            "Saved attachment %s for %s %s: %s (%d bytes)",
            attachment.attachment_id,
            entity_type,
            entity_id,
            filename,
            file_size,
        )

        return attachment, None

    def delete_attachment(
        self,
        db: Session,
        organization_id: uuid.UUID,
        attachment_id: uuid.UUID,
        hard_delete: bool = True,
    ) -> Tuple[bool, Optional[str]]:
        """
        Delete an attachment.

        Args:
            db: Database session
            organization_id: Organization UUID
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
                if os.path.exists(attachment.file_path):
                    os.remove(attachment.file_path)
            except Exception as e:
                logger.warning("Failed to delete file: %s", e)

        db.delete(attachment)
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
            organization_id: Organization UUID
            attachment_id: Attachment UUID

        Returns:
            File path or None if not found
        """
        attachment = self.get_attachment(db, organization_id, attachment_id)
        if not attachment:
            return None

        if not os.path.exists(attachment.file_path):
            logger.warning("Attachment file not found: %s", attachment.file_path)
            return None

        return attachment.file_path


# Singleton instance
project_attachment_service = ProjectAttachmentService()
