"""
Resume upload and validation service.

Handles secure file uploads for job applications with:
- File type validation (extension + magic bytes)
- Size limits
- Secure file storage
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
        self, org_id: uuid.UUID, filename: str, file_data: bytes
    ) -> tuple[str, str]:
        """
        Save a resume file securely.

        Args:
            org_id: Organization UUID for folder isolation
            filename: Original filename
            file_data: File content

        Returns:
            Tuple of (file_id, relative_path)

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
            upload_result = self.upload_service.save(
                file_data,
                content_type=None,
                subdirs=[str(org_id)],
                original_filename=filename,
            )
        except FileUploadError as exc:
            raise InvalidFileTypeError(str(exc)) from exc

        file_id = Path(upload_result.filename).stem
        logger.info("Resume saved: %s for org %s", file_id, org_id)

        return file_id, upload_result.relative_path

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

    def delete_resume(self, org_id: uuid.UUID, file_id: str) -> bool:
        """
        Delete a resume file from S3.

        Args:
            org_id: Organization UUID
            file_id: File UUID

        Returns:
            True if deleted, False if not found
        """
        s3_key = self._find_s3_key(org_id, file_id)
        if s3_key:
            get_storage().delete(s3_key)
            logger.info("Resume deleted: %s for org %s", file_id, org_id)
            return True
        return False

    def get_resume_url(self, org_id: uuid.UUID, file_id: str) -> str | None:
        """
        Get the URL for accessing a resume file.

        Returns an authenticated download URL that proxies from S3.

        Args:
            org_id: Organization UUID
            file_id: File UUID

        Returns:
            URL path if file exists, None otherwise
        """
        path = self.get_resume_path(org_id, file_id)
        if path:
            return f"/files/resumes/{org_id}/{path.name}"
        return None
