"""
Resume upload and validation service.

Handles secure file uploads for job applications with:
- File type validation (extension + magic bytes)
- Size limits
- Secure file storage

New resumes are ``dotmac-files`` tenant records: ``save_resume``
stages and commits the file (the application row is created later)
and returns the staged file id. Legacy object keys keep their
existing lookup/delete/download branches until
``scripts/backfill_resume_files.py`` rewrites stored resume URLs.
"""

import logging
import uuid
from dataclasses import replace
from pathlib import Path

from app.config import settings
from app.services.file_upload import (
    FileUploadError,
    FileUploadService,
    get_resume_upload,
)
from app.services.storage import get_storage

logger = logging.getLogger(__name__)


class ResumeServiceError(Exception):
    """Base exception for resume service errors."""

    pass


class InvalidFileTypeError(ResumeServiceError):
    """File type is not allowed."""

    pass


class FileTooLargeError(ResumeServiceError):
    """File exceeds size limit."""

    pass


def _stage_resume_file(
    db,
    organization_id: uuid.UUID,
    *,
    file_data: bytes,
    original_filename: str | None,
    config,
):
    """Upload and stage one resume on the tenant files plane."""
    from dotmac_files import FileError, prepare_upload
    from dotmac_files.providers import StorageError

    from app.models.finance.core_org.organization import Organization
    from app.services.file_object_cleanup import (
        CleanupUnsafeReferenceView,
        stage_tenant_file_if_unreserved,
    )
    from app.services.file_upload import (
        FileStorageError,
        policy_from_upload_config,
        resolve_declared_media_type,
    )
    from app.services.storage import get_dotmac_files_provider
    from app.services.tenant_projection import reconcile_organization_tenant
    from app.tenancy import OrganizationTenantContext

    policy = policy_from_upload_config(config)
    scope = OrganizationTenantContext.for_organization(organization_id).tenant_scope
    try:
        prepared = prepare_upload(
            get_dotmac_files_provider(),
            scope=scope,
            policy=policy,
            original_filename=original_filename or "resume",
            declared_media_type=resolve_declared_media_type(original_filename, None),
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


class ResumeService:
    """
    Service for handling resume file uploads.

    Provides secure file validation and storage for job applications.
    """

    _upload_dir: Path
    max_size: int
    allowed_extensions: set[str]

    def __init__(self):
        self._upload_dir = Path(settings.resume_upload_dir)
        self.max_size = settings.resume_max_size_bytes
        self.allowed_extensions = {
            ext.strip().lower() for ext in settings.resume_allowed_extensions.split(",")
        }
        self.upload_service = get_resume_upload()
        self._sync_upload_service_base()

    @property
    def upload_dir(self) -> Path:
        return self._upload_dir

    @upload_dir.setter
    def upload_dir(self, value: Path) -> None:
        self._upload_dir = Path(value)
        self._sync_upload_service_base()

    def _sync_upload_service_base(self) -> None:
        resolved = self._upload_dir.resolve()
        if self.upload_service.base_path != resolved:
            self.upload_service = FileUploadService(
                replace(self.upload_service.config, base_dir=str(resolved))
            )

    def validate_file(
        self, filename: str, file_size: int, file_data: bytes | None = None
    ) -> tuple[bool, str | None]:
        """
        Validate a file for upload.

        Args:
            filename: Original filename
            file_size: Size in bytes
            file_data: Optional file content for magic byte validation

        Returns:
            Tuple of (is_valid, error_message)
        """
        if self.max_size and file_size > self.max_size:
            return False, "File too large"
        try:
            self.upload_service.validate(
                content_type=None,
                filename=filename,
                file_size=file_size,
                file_data=file_data,
            )
        except FileUploadError as exc:
            return False, str(exc)
        return True, None

    def save_resume(
        self, db, org_id: uuid.UUID, filename: str, file_data: bytes
    ) -> tuple[str, str]:
        """
        Save a resume file securely.

        The staged tenant file record commits here: the application row
        is created by a later submission step, so the file must be
        durable before it exists.

        Args:
            db: Database session
            org_id: Organization UUID for tenant isolation
            filename: Original filename
            file_data: File content

        Returns:
            Tuple of (file_id, storage_key)

        Raises:
            InvalidFileTypeError: If file type is not allowed
            FileTooLargeError: If file exceeds size limit
        """
        # Validate first
        is_valid, error = self.validate_file(filename, len(file_data), file_data)
        if not is_valid:
            if "too large" in (error or "").lower():
                raise FileTooLargeError(error)
            raise InvalidFileTypeError(error or "Invalid file")

        try:
            prepared = _stage_resume_file(
                db,
                org_id,
                file_data=file_data,
                original_filename=filename,
                config=self.upload_service.config,
            )
            db.commit()
        except FileUploadError as exc:
            db.rollback()
            raise InvalidFileTypeError(str(exc)) from exc

        file_id = str(prepared.id)
        logger.info("Resume saved: %s for org %s", file_id, org_id)

        return file_id, prepared.storage_key

    def _find_s3_key(self, org_id: uuid.UUID, file_id: str) -> str | None:
        """Find the S3 key for a resume by checking each allowed extension.

        Returns:
            The S3 key string if found, None otherwise.
        """

        storage = get_storage()
        s3_prefix = self.upload_service.config.effective_s3_prefix
        for ext in self.allowed_extensions:
            s3_key = f"{s3_prefix}/{org_id}/{file_id}{ext}"
            if storage.exists(s3_key):
                return s3_key
        return None

    def get_resume_path(self, org_id: uuid.UUID, file_id: str) -> Path | None:
        """
        Get the path to a resume file.

        Checks S3 for the file and returns a Path derived from the S3 key.
        The returned Path may not exist on local disk — it represents the
        logical file location.

        Args:
            org_id: Organization UUID
            file_id: File UUID (without extension)

        Returns:
            Path derived from S3 key if found, None otherwise
        """
        s3_key = self._find_s3_key(org_id, file_id)
        if s3_key:
            # Return a Path from the S3 key so callers can use .name
            return Path(s3_key)
        return None

    def delete_resume(self, db, org_id: uuid.UUID, file_id: str) -> bool:
        """
        Delete a resume file, staged or legacy.

        Args:
            db: Database session
            org_id: Organization UUID
            file_id: File UUID

        Returns:
            True if deleted, False if not found
        """
        staged_id = self._staged_file_id(file_id)
        if staged_id is not None and self._staged_target(db, org_id, staged_id):
            from datetime import datetime, timezone

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
                db.commit()
            except FileError:
                db.rollback()
                return False
            logger.info("Resume deleted: %s for org %s", file_id, org_id)
            return True
        s3_key = self._find_s3_key(org_id, file_id)
        if s3_key:
            get_storage().delete(s3_key)
            logger.info("Resume deleted: %s for org %s", file_id, org_id)
            return True
        return False

    @staticmethod
    def _staged_file_id(file_id: str) -> uuid.UUID | None:
        try:
            return uuid.UUID(str(file_id))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _staged_target(db, org_id: uuid.UUID, file_id: uuid.UUID):
        """The authorized download target for a staged resume, if any."""
        from dotmac_files import FileError, download_target

        from app.tenancy import OrganizationTenantContext

        scope = OrganizationTenantContext.for_organization(org_id).tenant_scope
        try:
            return download_target(db, scope=scope, file_id=file_id)
        except FileError:
            return None

    def get_resume_url(self, db, org_id: uuid.UUID, file_id: str) -> str | None:
        """
        Get the URL for accessing a resume file.

        Returns an authenticated download URL. Staged resumes resolve
        through the by-id route; legacy keys keep their org/name URL.

        Args:
            db: Database session
            org_id: Organization UUID
            file_id: File UUID

        Returns:
            URL path if file exists, None otherwise
        """
        staged_id = self._staged_file_id(file_id)
        if staged_id is not None and self._staged_target(db, org_id, staged_id):
            return f"/files/resumes/{file_id}"
        path = self.get_resume_path(org_id, file_id)
        if path:
            return f"/files/resumes/{org_id}/{path.name}"
        return None
