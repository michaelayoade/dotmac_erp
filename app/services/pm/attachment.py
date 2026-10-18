"""
Project Attachment Service.

Handles file uploads and attachments for projects and tasks.
Uses the common.attachment model with polymorphic association. New
uploads are ``dotmac-files`` tenant records staged in the caller's
transaction (shared seam with finance attachments); ``file_path``
then holds the canonical tenant storage key. Pre-cutover relative
paths keep their legacy serving branch until
``scripts/backfill_pm_attachment_files.py`` rewrites them.
"""

import logging
import uuid
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.common.attachment import Attachment, AttachmentCategory
from app.services.file_upload import (
    FileUploadConfig,
    FileUploadError,
    FileUploadService,
    get_pm_attachment_upload,
    resolve_safe_path,
)

logger = logging.getLogger(__name__)


class ProjectAttachmentService:
    """Service for managing project and task attachments."""

    def __init__(self, upload_dir: str = "/app/uploads/projects"):
        """
        Initialize attachment service.

        Args:
            upload_dir: Base directory for file uploads
        """
        base_service = get_pm_attachment_upload()
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
        organization_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
    ) -> list[Attachment]:
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
        query = (
            select(Attachment)
            .where(
                Attachment.organization_id == organization_id,
                Attachment.entity_type == entity_type,
                Attachment.entity_id == entity_id,
            )
            .order_by(Attachment.created_at.desc())
        )

        return list(db.execute(query).scalars().all())

    def get_attachment(
        self,
        db: Session,
        organization_id: uuid.UUID,
        attachment_id: uuid.UUID,
    ) -> Attachment | None:
        """Get an attachment by ID with org check."""
        attachment = db.get(Attachment, attachment_id)
        if attachment and attachment.organization_id == organization_id:
            return attachment
        return None

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
        organization_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        filename: str,
        file_data: BinaryIO,
        content_type: str,
        uploaded_by_id: uuid.UUID,
        description: str | None = None,
    ) -> tuple[Attachment | None, str | None]:
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
        content = file_data.read()

        from app.services.finance.common.attachment import _stage_attachment_file

        try:
            prepared = _stage_attachment_file(
                db,
                organization_id,
                file_data=content,
                content_type=content_type,
                original_filename=filename,
                config=self.upload_service.config,
            )
        except FileUploadError as exc:
            return None, str(exc)
        file_size = prepared.size_bytes

        # Determine category
        category = (
            AttachmentCategory.PROJECT
            if entity_type == "PROJECT"
            else AttachmentCategory.TASK
        )

        # Create database record
        attachment = Attachment(
            organization_id=organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            file_name=filename,
            file_path=prepared.storage_key,
            content_type=content_type,
            file_size=file_size,
            category=category,
            description=description,
            storage_provider="LOCAL",
            checksum=prepared.checksum_sha256.removeprefix("sha256:"),
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
    ) -> tuple[bool, str | None]:
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
            # Retire the staged file record, or delete the legacy object.
            from app.services.finance.common.attachment import staged_file_id

            staged_id = staged_file_id(attachment)
            if staged_id is not None:
                from datetime import datetime, timezone

                from dotmac_files import FileError, request_deletion

                from app.tenancy import OrganizationTenantContext

                try:
                    request_deletion(
                        db,
                        scope=OrganizationTenantContext.for_organization(
                            organization_id
                        ).tenant_scope,
                        file_id=staged_id,
                        now=datetime.now(timezone.utc),
                    )
                except FileError:
                    logger.warning(
                        "attachment file %s could not be marked for deletion",
                        staged_id,
                    )
                db.delete(attachment)
                db.flush()
                logger.info("Deleted attachment %s", attachment_id)
                return True, None
            # Delete file from disk
            try:
                if Path(attachment.file_path).is_absolute():
                    resolved = Path(attachment.file_path).resolve()
                    resolved.relative_to(self.upload_service.base_path)
                    if resolved.exists():
                        resolved.unlink()
                else:
                    self.upload_service.delete(attachment.file_path)
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
    ) -> str | None:
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

        from app.services.finance.common.attachment import staged_file_id

        if staged_file_id(attachment) is not None:
            # Post-cutover attachments have no local path; callers stream
            # them via staged_download_response below.
            return None

        try:
            if Path(attachment.file_path).is_absolute():
                resolved = Path(attachment.file_path).resolve()
                resolved.relative_to(self.upload_service.base_path)
            else:
                resolved = resolve_safe_path(
                    self.upload_service.base_path, attachment.file_path
                )
        except ValueError:
            logger.warning("Attachment file not found: %s", attachment.file_path)
            return None

        if not resolved.exists():
            logger.warning("Attachment file not found: %s", resolved)
            return None

        return str(resolved)


def staged_download_response(db: Session, organization_id: uuid.UUID, attachment):
    """Stream a post-cutover attachment, or None when it is not staged."""
    from app.services.finance.common.attachment import staged_file_id

    file_id = staged_file_id(attachment)
    if file_id is None:
        return None

    from dotmac_files import FileError, download_target, open_object
    from fastapi.responses import StreamingResponse

    from app.services.storage import get_dotmac_files_provider
    from app.tenancy import OrganizationTenantContext

    scope = OrganizationTenantContext.for_organization(organization_id).tenant_scope
    try:
        target = download_target(db, scope=scope, file_id=file_id)
        readable = open_object(get_dotmac_files_provider(), target=target)
    except FileError:
        return None

    def _chunks():
        with readable:
            while chunk := readable.read(64 * 1024):
                yield chunk

    return StreamingResponse(
        _chunks(),
        media_type=target.detected_media_type
        or attachment.content_type
        or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{attachment.file_name}"',
            "Content-Length": str(target.size_bytes),
        },
    )


# Singleton instance
project_attachment_service = ProjectAttachmentService()
