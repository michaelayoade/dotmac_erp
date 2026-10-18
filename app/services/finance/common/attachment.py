"""
Attachment Service - File upload and management.

Handles file storage, retrieval, and metadata management for document
attachments. New uploads are ``dotmac-files`` tenant records staged in
the caller's transaction; ``Attachment.file_path`` then holds the
canonical tenant storage key (``tenants/<org>/files/<id>``).
Pre-cutover relative paths keep their legacy serving branch until
``scripts/backfill_finance_attachment_files.py`` rewrites them.
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


def staged_file_id(attachment: Attachment) -> uuid.UUID | None:
    """The staged file id for a post-cutover attachment, else None."""
    file_path = attachment.file_path or ""
    if not file_path.startswith("tenants/"):
        return None
    try:
        return uuid.UUID(file_path.rsplit("/", 1)[-1])
    except ValueError:
        return None


def _stage_attachment_file(
    db: Session,
    organization_id: uuid.UUID,
    *,
    file_data: bytes,
    content_type: str | None,
    original_filename: str | None,
    config=None,
):
    """Upload and stage one finance attachment on the tenant files plane."""
    from dotmac_files import FileError, FilePolicy, prepare_upload
    from dotmac_files.providers import StorageError

    from app.models.finance.core_org.organization import Organization
    from app.services.file_object_cleanup import (
        CleanupUnsafeReferenceView,
        stage_tenant_file_if_unreserved,
    )
    from app.services.file_upload import FileStorageError
    from app.services.storage import get_dotmac_files_provider
    from app.services.tenant_projection import reconcile_organization_tenant
    from app.tenancy import OrganizationTenantContext

    config = config or _upload_service().config
    policy = FilePolicy(
        max_bytes=config.max_size_bytes,
        allowed_extensions=frozenset(config.allowed_extensions),
        allowed_media_types=frozenset(config.allowed_content_types),
    )
    scope = OrganizationTenantContext.for_organization(organization_id).tenant_scope
    try:
        prepared = prepare_upload(
            get_dotmac_files_provider(),
            scope=scope,
            policy=policy,
            original_filename=original_filename or "attachment",
            declared_media_type=content_type or "",
            chunks=iter([file_data]),
        )
    except FileError as exc:
        raise FileUploadError(str(exc)) from exc
    except StorageError as exc:
        raise FileStorageError(str(exc)) from exc
    if db.get_bind().dialect.name == "postgresql":
        organization = db.get(Organization, organization_id)
        if organization is not None:
            reconcile_organization_tenant(db, organization)
            db.flush()
    try:
        stage_tenant_file_if_unreserved(db, prepared=prepared)
    except CleanupUnsafeReferenceView as exc:
        raise FileUploadError(str(exc)) from exc
    return prepared


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

        file_bytes = file_content.read()
        try:
            prepared = _stage_attachment_file(
                db,
                org_id,
                file_data=file_bytes,
                content_type=input.content_type,
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
            file_path=prepared.storage_key,
            file_size=prepared.size_bytes,
            content_type=input.content_type,
            category=input.category,
            description=input.description,
            storage_provider="S3",
            checksum=prepared.checksum_sha256.removeprefix("sha256:"),
            uploaded_by=user_id,
            uploaded_at=datetime.utcnow(),
        )

        db.add(attachment)
        db.flush()
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

        # Retire the staged file record, or delete the legacy local file.
        staged_id = staged_file_id(attachment)
        if staged_id is not None:
            from datetime import timezone

            from dotmac_files import FileError, request_deletion

            from app.tenancy import OrganizationTenantContext

            try:
                request_deletion(
                    db,
                    scope=OrganizationTenantContext.for_organization(
                        org_id
                    ).tenant_scope,
                    file_id=staged_id,
                    now=datetime.now(timezone.utc),
                )
            except FileError:
                logger.warning(
                    "attachment file %s could not be marked for deletion",
                    staged_id,
                )
        else:
            try:
                legacy_path = AttachmentService.get_file_path(attachment)
            except ValueError:
                legacy_path = None
            if legacy_path and legacy_path.exists():
                legacy_path.unlink()

        # Delete database record
        db.delete(attachment)
        db.flush()

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
