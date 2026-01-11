"""
Attachment Service - File upload and management.

Handles file storage, retrieval, and metadata management for document attachments.
"""

import hashlib
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ifrs.common.attachment import Attachment, AttachmentCategory


# Configuration
UPLOAD_BASE_DIR = os.getenv("ATTACHMENT_UPLOAD_DIR", "uploads/attachments")
MAX_FILE_SIZE = int(os.getenv("MAX_ATTACHMENT_SIZE", 10 * 1024 * 1024))  # 10MB default

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "text/csv",
}

SAFE_ENTITY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class AttachmentInput:
    """Input for creating an attachment."""
    entity_type: str
    entity_id: str
    file_name: str
    content_type: str
    category: AttachmentCategory = AttachmentCategory.OTHER
    description: Optional[str] = None


@dataclass
class AttachmentView:
    """View model for attachment display."""
    attachment_id: str
    file_name: str
    file_size: int
    content_type: str
    category: str
    description: Optional[str]
    uploaded_at: datetime
    download_url: str


def _coerce_uuid(value) -> uuid.UUID:
    """Convert string to UUID if needed."""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _safe_entity_segment(entity_type: str) -> str:
    """Validate entity type for filesystem paths."""
    if not entity_type:
        raise ValueError("Entity type is required")
    if not SAFE_ENTITY_PATTERN.match(entity_type):
        raise ValueError("Invalid entity type")
    if Path(entity_type).name != entity_type:
        raise ValueError("Invalid entity type")
    return entity_type


def _resolve_attachment_path(relative_path: str) -> Path:
    """Resolve attachment path safely within the upload root."""
    base_dir = Path(UPLOAD_BASE_DIR).resolve()
    full_path = (base_dir / relative_path).resolve()
    if base_dir != full_path and base_dir not in full_path.parents:
        raise ValueError("Invalid attachment path")
    return full_path


def _compute_checksum(file_path: str) -> str:
    """Compute SHA-256 checksum of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _format_file_size(size: int) -> str:
    """Format file size for display."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


class AttachmentService:
    """Service for managing document attachments."""

    @staticmethod
    def get_upload_path(organization_id: uuid.UUID, entity_type: str) -> Path:
        """Get the upload directory path for an organization and entity type."""
        safe_entity_type = _safe_entity_segment(entity_type)
        path = Path(UPLOAD_BASE_DIR) / str(organization_id) / safe_entity_type.lower()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def save_file(
        db: Session,
        organization_id: uuid.UUID,
        input: AttachmentInput,
        file_content: BinaryIO,
        uploaded_by: uuid.UUID,
    ) -> Attachment:
        """
        Save an uploaded file and create attachment record.

        Args:
            db: Database session
            organization_id: Organization UUID
            input: Attachment metadata
            file_content: File binary content
            uploaded_by: User who uploaded the file

        Returns:
            Created Attachment record
        """
        org_id = _coerce_uuid(organization_id)
        entity_id = _coerce_uuid(input.entity_id)
        user_id = _coerce_uuid(uploaded_by)

        # Validate content type
        if input.content_type not in ALLOWED_CONTENT_TYPES:
            raise ValueError(f"Content type '{input.content_type}' is not allowed")

        # Generate unique filename
        file_ext = Path(input.file_name).suffix.lower()
        unique_name = f"{uuid.uuid4()}{file_ext}"

        # Get upload directory
        upload_dir = AttachmentService.get_upload_path(org_id, input.entity_type)
        file_path = upload_dir / unique_name

        # Save file
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file_content, f)

        # Get file size and validate
        file_size = file_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            file_path.unlink()  # Delete the file
            raise ValueError(f"File size exceeds maximum allowed ({_format_file_size(MAX_FILE_SIZE)})")

        # Compute checksum
        checksum = _compute_checksum(str(file_path))

        # Create attachment record
        attachment = Attachment(
            organization_id=org_id,
            entity_type=input.entity_type,
            entity_id=entity_id,
            file_name=input.file_name,
            file_path=str(file_path.relative_to(UPLOAD_BASE_DIR)),
            file_size=file_size,
            content_type=input.content_type,
            category=input.category,
            description=input.description,
            storage_provider="LOCAL",
            checksum=checksum,
            uploaded_by=user_id,
            uploaded_at=datetime.utcnow(),
        )

        db.add(attachment)
        db.commit()
        db.refresh(attachment)

        return attachment

    @staticmethod
    def get(
        db: Session,
        organization_id: uuid.UUID,
        attachment_id: str,
    ) -> Optional[Attachment]:
        """Get attachment by ID."""
        org_id = _coerce_uuid(organization_id)
        att_id = _coerce_uuid(attachment_id)
        attachment = db.get(Attachment, att_id)
        if not attachment or attachment.organization_id != org_id:
            return None
        return attachment

    @staticmethod
    def get_file_path(attachment: Attachment) -> Path:
        """Get the full file path for an attachment."""
        return _resolve_attachment_path(attachment.file_path)

    @staticmethod
    def list_for_entity(
        db: Session,
        organization_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
    ) -> List[Attachment]:
        """List all attachments for a specific entity."""
        org_id = _coerce_uuid(organization_id)
        ent_id = _coerce_uuid(entity_id)

        return (
            db.query(Attachment)
            .filter(
                Attachment.organization_id == org_id,
                Attachment.entity_type == entity_type,
                Attachment.entity_id == ent_id,
            )
            .order_by(Attachment.uploaded_at.desc())
            .all()
        )

    @staticmethod
    def delete(db: Session, attachment_id: str, organization_id: uuid.UUID) -> bool:
        """
        Delete an attachment and its file.

        Returns True if deleted, False if not found.
        """
        att_id = _coerce_uuid(attachment_id)
        org_id = _coerce_uuid(organization_id)

        attachment = (
            db.query(Attachment)
            .filter(
                Attachment.attachment_id == att_id,
                Attachment.organization_id == org_id,
            )
            .first()
        )

        if not attachment:
            return False

        # Delete file from storage
        try:
            file_path = AttachmentService.get_file_path(attachment)
        except ValueError:
            file_path = None

        if file_path and file_path.exists():
            file_path.unlink()

        # Delete database record
        db.delete(attachment)
        db.commit()

        return True

    @staticmethod
    def count_for_entity(
        db: Session,
        organization_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
    ) -> int:
        """Count attachments for an entity."""
        org_id = _coerce_uuid(organization_id)
        ent_id = _coerce_uuid(entity_id)

        return (
            db.query(func.count(Attachment.attachment_id))
            .filter(
                Attachment.organization_id == org_id,
                Attachment.entity_type == entity_type,
                Attachment.entity_id == ent_id,
            )
            .scalar() or 0
        )

    @staticmethod
    def to_view(attachment: Attachment, base_url: str = "/ap") -> AttachmentView:
        """Convert attachment to view model."""
        return AttachmentView(
            attachment_id=str(attachment.attachment_id),
            file_name=attachment.file_name,
            file_size=attachment.file_size,
            content_type=attachment.content_type,
            category=attachment.category.value,
            description=attachment.description,
            uploaded_at=attachment.uploaded_at,
            download_url=f"{base_url}/attachments/{attachment.attachment_id}/download",
        )


# Singleton instance
attachment_service = AttachmentService()
