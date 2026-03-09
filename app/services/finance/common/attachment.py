"""
Attachment Service - File upload and management.

Handles file storage, retrieval, and metadata management for document attachments.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.common.attachment import Attachment, AttachmentCategory
from app.services.common import coerce_uuid
from app.services.file_upload import (
    FileUploadError,
    FileUploadService,
    get_finance_attachment_upload,
    resolve_safe_path,
    safe_entity_segment,
)

logger = logging.getLogger(__name__)


@dataclass
class AttachmentInput:
    """Input for creating an attachment."""

    entity_type: str
    entity_id: str
    file_name: str
    content_type: str
    category: AttachmentCategory = AttachmentCategory.OTHER
    description: str | None = None


@dataclass
class AttachmentView:
    """View model for attachment display."""

    attachment_id: str
    file_name: str
    file_size: int
    content_type: str
    category: str
    description: str | None
    uploaded_at: datetime
    download_url: str


def _upload_service() -> FileUploadService:
    return get_finance_attachment_upload()


class AttachmentService:
    """Service for managing document attachments."""

    @staticmethod
    def get_upload_path(organization_id: uuid.UUID, entity_type: str) -> Path:
        """Get the upload directory path for an organization and entity type."""
        safe_entity_type = safe_entity_segment(entity_type)
        path = (
            _upload_service().base_path
            / str(organization_id)
            / safe_entity_type.lower()
        )
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
        org_id = coerce_uuid(organization_id)
        entity_id = coerce_uuid(input.entity_id)
        user_id = coerce_uuid(uploaded_by)

        safe_entity_type = safe_entity_segment(input.entity_type)
        file_bytes = file_content.read()
        upload_service = _upload_service()

        try:
            upload_result = upload_service.save(
                file_bytes,
                content_type=input.content_type,
                subdirs=[str(org_id), safe_entity_type.lower()],
                original_filename=input.file_name,
            )
        except FileUploadError as exc:
            raise ValueError(str(exc)) from exc

        # Create attachment record
        attachment = Attachment(
            organization_id=org_id,
            entity_type=input.entity_type,
            entity_id=entity_id,
            file_name=input.file_name,
            file_path=upload_result.relative_path,
            file_size=upload_result.file_size,
            content_type=input.content_type,
            category=input.category,
            description=input.description,
            storage_provider="S3",
            checksum=upload_result.checksum,
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
    ) -> Attachment | None:
        """Get attachment by ID."""
        org_id = coerce_uuid(organization_id)
        att_id = coerce_uuid(attachment_id)
        attachment = db.get(Attachment, att_id)
        if not attachment or attachment.organization_id != org_id:
            return None
        return attachment

    @staticmethod
    def get_file_path(attachment: Attachment) -> Path:
        """Get the full file path for an attachment."""
        return resolve_safe_path(_upload_service().base_path, attachment.file_path)

    @staticmethod
    def list_for_entity(
        db: Session,
        organization_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
    ) -> list[Attachment]:
        """List all attachments for a specific entity."""
        org_id = coerce_uuid(organization_id)
        ent_id = coerce_uuid(entity_id)

        return list(
            db.scalars(
                select(Attachment)
                .where(
                    Attachment.organization_id == org_id,
                    Attachment.entity_type == entity_type,
                    Attachment.entity_id == ent_id,
                )
                .order_by(Attachment.uploaded_at.desc())
            ).all()
        )

    @staticmethod
    def delete(db: Session, attachment_id: str, organization_id: uuid.UUID) -> bool:
        """
        Delete an attachment and its file.

        Returns True if deleted, False if not found.
        """
        att_id = coerce_uuid(attachment_id)
        org_id = coerce_uuid(organization_id)

        attachment = db.scalars(
            select(Attachment).where(
                Attachment.attachment_id == att_id,
                Attachment.organization_id == org_id,
            )
        ).first()

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
        org_id = coerce_uuid(organization_id)
        ent_id = coerce_uuid(entity_id)

        return (
            db.scalar(
                select(func.count(Attachment.attachment_id)).where(
                    Attachment.organization_id == org_id,
                    Attachment.entity_type == entity_type,
                    Attachment.entity_id == ent_id,
                )
            )
            or 0
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
