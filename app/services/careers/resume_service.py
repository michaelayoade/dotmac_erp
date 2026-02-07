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
from typing import Optional

from app.config import settings
from app.services.file_upload import (
    FileUploadError,
    FileUploadService,
    get_resume_upload,
    resolve_safe_path,
)

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
        self, filename: str, file_size: int, file_data: Optional[bytes] = None
    ) -> tuple[bool, Optional[str]]:
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

    def get_resume_path(self, org_id: uuid.UUID, file_id: str) -> Optional[Path]:
        """
        Get the full path to a resume file.

        Args:
            org_id: Organization UUID
            file_id: File UUID (without extension)

        Returns:
            Path to file if found, None otherwise
        """
        for ext in self.allowed_extensions:
            try:
                file_path = resolve_safe_path(
                    self.upload_service.base_path, f"{org_id}/{file_id}{ext}"
                )
            except ValueError:
                continue
            if file_path.exists():
                return file_path
        return None

    def delete_resume(self, org_id: uuid.UUID, file_id: str) -> bool:
        """
        Delete a resume file.

        Args:
            org_id: Organization UUID
            file_id: File UUID

        Returns:
            True if deleted, False if not found
        """
        file_path = self.get_resume_path(org_id, file_id)
        if file_path and file_path.exists():
            file_path.unlink()
            logger.info("Resume deleted: %s for org %s", file_id, org_id)
            return True
        return False

    def get_resume_url(self, org_id: uuid.UUID, file_id: str) -> Optional[str]:
        """
        Get the URL for accessing a resume file.

        This returns an internal path - actual serving should go through
        an authenticated endpoint for security.

        Args:
            org_id: Organization UUID
            file_id: File UUID

        Returns:
            URL path if file exists, None otherwise
        """
        file_path = self.get_resume_path(org_id, file_id)
        if file_path:
            return f"/uploads/resumes/{org_id}/{file_path.name}"
        return None
