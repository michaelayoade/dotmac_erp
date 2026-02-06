"""
Resume upload and validation service.

Handles secure file uploads for job applications with:
- File type validation (extension + magic bytes)
- Size limits
- Secure file storage
"""

import logging
import uuid
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

# Magic bytes for common document formats
MAGIC_BYTES = {
    ".pdf": [b"%PDF"],
    ".doc": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],  # OLE compound document
    ".docx": [b"PK\x03\x04"],  # ZIP archive (OOXML)
}


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

    upload_dir: Path
    max_size: int
    allowed_extensions: set[str]

    def __init__(self):
        self.upload_dir = Path(settings.resume_upload_dir)
        self.max_size = settings.resume_max_size_bytes
        self.allowed_extensions = {
            ext.strip().lower() for ext in settings.resume_allowed_extensions.split(",")
        }

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
        # Check file extension
        ext = Path(filename).suffix.lower()
        if ext not in self.allowed_extensions:
            allowed = ", ".join(sorted(self.allowed_extensions))
            return False, f"File type not allowed. Accepted formats: {allowed}"

        # Check file size
        if file_size > self.max_size:
            max_mb = self.max_size / (1024 * 1024)
            return False, f"File too large. Maximum size is {max_mb:.0f}MB"

        # Validate magic bytes if data provided
        if file_data and ext in MAGIC_BYTES:
            valid_magic = False
            for magic in MAGIC_BYTES[ext]:
                if file_data[: len(magic)] == magic:
                    valid_magic = True
                    break
            if not valid_magic:
                return False, "File content does not match the expected format"

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

        # Generate unique file ID
        file_id = str(uuid.uuid4())
        ext = Path(filename).suffix.lower()

        # Create org-specific directory
        org_dir = self.upload_dir / str(org_id)
        org_dir.mkdir(parents=True, exist_ok=True)

        # Save file with UUID name (prevents path traversal and name conflicts)
        saved_filename = f"{file_id}{ext}"
        file_path = org_dir / saved_filename

        file_path.write_bytes(file_data)
        logger.info("Resume saved: %s for org %s", file_id, org_id)

        relative_path = f"{org_id}/{saved_filename}"
        return file_id, relative_path

    def get_resume_path(self, org_id: uuid.UUID, file_id: str) -> Optional[Path]:
        """
        Get the full path to a resume file.

        Args:
            org_id: Organization UUID
            file_id: File UUID (without extension)

        Returns:
            Path to file if found, None otherwise
        """
        org_dir = self.upload_dir / str(org_id)
        if not org_dir.exists():
            return None

        # Find file with any allowed extension
        for ext in self.allowed_extensions:
            file_path = org_dir / f"{file_id}{ext}"
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
