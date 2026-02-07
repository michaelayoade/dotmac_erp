"""
Unified File Upload Service.

Central service for file validation, storage, and deletion.
All upload services (avatar, branding, resume, finance attachments,
support attachments, PM attachments, discipline attachments) delegate
to this core service.

Key improvements:
- Size validated BEFORE writing to disk
- Consistent path-traversal protection
- Magic byte validation (opt-in)
- SHA-256 checksum (opt-in)
- Shared helpers: coerce_uuid, format_file_size, compute_checksum_*
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Union

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Magic bytes for file-type validation (extension → list of valid signatures)
# ---------------------------------------------------------------------------
MAGIC_BYTES: dict[str, list[bytes]] = {
    # Documents
    ".pdf": [b"%PDF"],
    ".doc": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
    ".docx": [b"PK\x03\x04"],
    ".xls": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
    ".xlsx": [b"PK\x03\x04"],
    # Images
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".gif": [b"GIF87a", b"GIF89a"],
    ".webp": [b"RIFF"],
    # Archives
    ".zip": [b"PK\x03\x04"],
}

# Content-type → extension mapping (superset across all services)
CONTENT_TYPE_EXTENSIONS: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/zip": ".zip",
}

# Safe entity-type pattern (used by finance/PM attachment paths)
SAFE_ENTITY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


# ---------------------------------------------------------------------------
# Shared helpers (used by multiple domain-specific attachment services)
# ---------------------------------------------------------------------------


def coerce_uuid(value: Union[str, uuid.UUID]) -> uuid.UUID:
    """Convert string to UUID if needed."""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def format_file_size(size: int) -> str:
    """Format file size for human-readable display."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


def compute_checksum(data: bytes) -> str:
    """Compute SHA-256 checksum of in-memory bytes."""
    return hashlib.sha256(data).hexdigest()


def compute_checksum_from_file(file_path: str) -> str:
    """Compute SHA-256 checksum of a file on disk (chunked)."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def resolve_safe_path(base_dir: Path, relative_path: str) -> Path:
    """
    Resolve a relative path safely within a base directory.

    Raises ValueError if the resolved path would escape base_dir.
    """
    base = base_dir.resolve()
    full_path = (base / relative_path).resolve()
    if base != full_path and base not in full_path.parents:
        raise ValueError("Invalid file path: outside upload directory")
    return full_path


def safe_entity_segment(entity_type: str) -> str:
    """Validate an entity-type string for use in filesystem paths."""
    if not entity_type:
        raise ValueError("Entity type is required")
    if not SAFE_ENTITY_PATTERN.match(entity_type):
        raise ValueError("Invalid entity type")
    if Path(entity_type).name != entity_type:
        raise ValueError("Invalid entity type")
    return entity_type


@dataclass(frozen=True)
class FileUploadConfig:
    """Policy object defining upload constraints for a specific domain."""

    base_dir: str
    allowed_content_types: frozenset[str]
    max_size_bytes: int
    allowed_extensions: frozenset[str] = field(default_factory=frozenset)
    require_magic_bytes: bool = False
    compute_checksum: bool = False


@dataclass
class UploadResult:
    """Result of a successful file upload."""

    file_path: Path
    relative_path: str
    filename: str
    file_size: int
    checksum: Optional[str] = None


class FileUploadError(Exception):
    """Base exception for upload errors."""

    pass


class InvalidContentTypeError(FileUploadError):
    """Content type is not allowed."""

    pass


class InvalidExtensionError(FileUploadError):
    """File extension is not allowed."""

    pass


class FileTooLargeError(FileUploadError):
    """File exceeds size limit."""

    pass


class InvalidMagicBytesError(FileUploadError):
    """File content does not match its claimed format."""

    pass


class PathTraversalError(FileUploadError):
    """Attempted path traversal detected."""

    pass


class FileUploadService:
    """
    Core file upload service.

    Handles validation, storage, and deletion with consistent
    security guarantees across all upload domains.
    """

    def __init__(self, config: FileUploadConfig) -> None:
        self.config = config

    @property
    def base_path(self) -> Path:
        """Resolved base directory for uploads."""
        return Path(self.config.base_dir).resolve()

    def validate(
        self,
        content_type: Optional[str],
        filename: Optional[str],
        file_size: int,
        file_data: Optional[bytes] = None,
    ) -> None:
        """
        Run all validation checks BEFORE writing to disk.

        Raises FileUploadError subclass on failure.
        """
        # Content type check
        if content_type and self.config.allowed_content_types:
            if content_type not in self.config.allowed_content_types:
                allowed = ", ".join(sorted(self.config.allowed_content_types))
                raise InvalidContentTypeError(
                    f"Content type '{content_type}' not allowed. Allowed: {allowed}"
                )

        # Extension check
        if filename and self.config.allowed_extensions:
            ext = Path(filename).suffix.lower()
            if ext not in self.config.allowed_extensions:
                allowed = ", ".join(sorted(self.config.allowed_extensions))
                raise InvalidExtensionError(
                    f"File extension '{ext}' not allowed. Allowed: {allowed}"
                )

        # Size check
        if file_size > self.config.max_size_bytes:
            max_mb = self.config.max_size_bytes / (1024 * 1024)
            raise FileTooLargeError(
                f"File too large ({file_size} bytes). Maximum size: {max_mb:.0f}MB"
            )

        # Magic bytes check
        if self.config.require_magic_bytes and file_data and filename:
            ext = Path(filename).suffix.lower()
            if ext in MAGIC_BYTES:
                valid = any(
                    file_data[: len(magic)] == magic for magic in MAGIC_BYTES[ext]
                )
                if not valid:
                    raise InvalidMagicBytesError(
                        "File content does not match the expected format"
                    )
            elif content_type in {"text/plain", "text/csv"}:
                try:
                    file_data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise InvalidMagicBytesError(
                        "Text file contains invalid UTF-8 content"
                    ) from exc

    def _generate_filename(
        self,
        content_type: Optional[str],
        original_filename: Optional[str],
        prefix: Optional[str] = None,
    ) -> str:
        """Generate a unique filename preserving the original extension."""
        ext = ""
        if original_filename:
            ext = Path(original_filename).suffix.lower()
        if not ext and content_type:
            ext = CONTENT_TYPE_EXTENSIONS.get(content_type, "")

        unique_id = uuid.uuid4().hex[:12]
        if prefix:
            return f"{prefix}_{unique_id}{ext}"
        return f"{unique_id}{ext}"

    def save(
        self,
        file_data: bytes,
        content_type: Optional[str] = None,
        subdirs: Optional[Sequence[str]] = None,
        prefix: Optional[str] = None,
        original_filename: Optional[str] = None,
    ) -> UploadResult:
        """
        Validate and save a file.

        Args:
            file_data: Raw file bytes.
            content_type: MIME type.
            subdirs: Optional subdirectory components (e.g. [org_id]).
            prefix: Optional filename prefix (e.g. "logo", "favicon").
            original_filename: Original filename for extension preservation.

        Returns:
            UploadResult with paths and metadata.

        Raises:
            FileUploadError subclass on validation failure.
        """
        # Validate BEFORE writing
        self.validate(content_type, original_filename, len(file_data), file_data)

        # Build target directory
        target_dir = self.base_path
        if subdirs:
            for sub in subdirs:
                target_dir = target_dir / sub
        target_dir.mkdir(parents=True, exist_ok=True)

        # Generate unique filename
        filename = self._generate_filename(content_type, original_filename, prefix)
        file_path = target_dir / filename

        # Path traversal protection
        resolved = file_path.resolve()
        try:
            resolved.relative_to(self.base_path)
        except ValueError:
            raise PathTraversalError(
                "Path traversal detected: target is outside upload directory"
            )

        # Write
        resolved.write_bytes(file_data)

        # Relative path from base_dir
        relative = str(resolved.relative_to(self.base_path))

        # Optional checksum
        checksum: Optional[str] = None
        if self.config.compute_checksum:
            checksum = hashlib.sha256(file_data).hexdigest()

        logger.info(
            "File saved: %s (%d bytes, type=%s)",
            relative,
            len(file_data),
            content_type,
        )

        return UploadResult(
            file_path=resolved,
            relative_path=relative,
            filename=filename,
            file_size=len(file_data),
            checksum=checksum,
        )

    def delete(self, relative_path: str) -> bool:
        """
        Delete a file by its relative path within base_dir.

        Returns True if deleted, False if not found.
        Raises PathTraversalError if path escapes base_dir.
        """
        target = (self.base_path / relative_path).resolve()
        try:
            target.relative_to(self.base_path)
        except ValueError:
            raise PathTraversalError(
                "Path traversal detected: target is outside upload directory"
            )

        if target.exists():
            target.unlink()
            logger.info("File deleted: %s", relative_path)
            return True
        return False

    def delete_by_url(self, url: str, url_prefix: str) -> bool:
        """
        Delete a file identified by its URL.

        Strips url_prefix to derive the relative path, then delegates
        to delete(). Used by avatar and branding services.
        """
        if not url or not url.startswith(url_prefix):
            return False

        relative = url[len(url_prefix) :].lstrip("/")
        if not relative:
            return False

        return self.delete(relative)


# ---------------------------------------------------------------------------
# Pre-configured instances for each upload domain
# ---------------------------------------------------------------------------


def _avatar_config() -> FileUploadConfig:
    return FileUploadConfig(
        base_dir=settings.avatar_upload_dir,
        allowed_content_types=frozenset(settings.avatar_allowed_types.split(",")),
        max_size_bytes=settings.avatar_max_size_bytes,
    )


def _branding_config() -> FileUploadConfig:
    return FileUploadConfig(
        base_dir=settings.branding_upload_dir,
        allowed_content_types=frozenset(settings.branding_allowed_types.split(",")),
        max_size_bytes=settings.branding_max_size_bytes,
    )


def _resume_config() -> FileUploadConfig:
    extensions = frozenset(
        ext.strip().lower() for ext in settings.resume_allowed_extensions.split(",")
    )
    # Derive content types from extensions
    ext_to_ct: dict[str, str] = {
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    content_types = frozenset(ct for ext, ct in ext_to_ct.items() if ext in extensions)
    return FileUploadConfig(
        base_dir=settings.resume_upload_dir,
        allowed_content_types=content_types,
        allowed_extensions=extensions,
        max_size_bytes=settings.resume_max_size_bytes,
        require_magic_bytes=True,
    )


def _finance_attachment_config() -> FileUploadConfig:
    import os

    base_dir = os.getenv("ATTACHMENT_UPLOAD_DIR", "uploads/attachments")
    max_size = int(os.getenv("MAX_ATTACHMENT_SIZE", str(10 * 1024 * 1024)))
    return FileUploadConfig(
        base_dir=base_dir,
        allowed_content_types=frozenset(
            {
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
        ),
        max_size_bytes=max_size,
        compute_checksum=True,
    )


def _support_attachment_config() -> FileUploadConfig:
    return FileUploadConfig(
        base_dir="/app/uploads/support",
        allowed_content_types=frozenset(
            {
                "image/jpeg",
                "image/png",
                "image/gif",
                "image/webp",
                "application/pdf",
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.ms-excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "text/csv",
                "text/plain",
                "application/zip",
            }
        ),
        allowed_extensions=frozenset(
            {
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".webp",
                ".pdf",
                ".doc",
                ".docx",
                ".xls",
                ".xlsx",
                ".csv",
                ".txt",
                ".zip",
            }
        ),
        max_size_bytes=10 * 1024 * 1024,
        compute_checksum=True,
    )


def get_avatar_upload() -> FileUploadService:
    """Get avatar upload service (lazily configured from settings)."""
    return FileUploadService(_avatar_config())


def get_branding_upload() -> FileUploadService:
    """Get branding upload service (lazily configured from settings)."""
    return FileUploadService(_branding_config())


def get_resume_upload() -> FileUploadService:
    """Get resume upload service (lazily configured from settings)."""
    return FileUploadService(_resume_config())


def get_finance_attachment_upload() -> FileUploadService:
    """Get finance attachment upload service."""
    return FileUploadService(_finance_attachment_config())


def get_support_attachment_upload() -> FileUploadService:
    """Get support attachment upload service."""
    return FileUploadService(_support_attachment_config())


def _pm_attachment_config() -> FileUploadConfig:
    return FileUploadConfig(
        base_dir="/app/uploads/projects",
        allowed_content_types=frozenset(
            {
                "image/jpeg",
                "image/png",
                "image/gif",
                "image/webp",
                "application/pdf",
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.ms-excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "text/csv",
                "text/plain",
                "application/zip",
            }
        ),
        allowed_extensions=frozenset(
            {
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".webp",
                ".pdf",
                ".doc",
                ".docx",
                ".xls",
                ".xlsx",
                ".csv",
                ".txt",
                ".zip",
            }
        ),
        max_size_bytes=10 * 1024 * 1024,
        compute_checksum=True,
    )


def _discipline_attachment_config() -> FileUploadConfig:
    import os

    base_dir = os.getenv("DISCIPLINE_UPLOAD_DIR", "uploads/discipline")
    max_size = int(os.getenv("MAX_DISCIPLINE_FILE_SIZE", str(10 * 1024 * 1024)))
    return FileUploadConfig(
        base_dir=base_dir,
        allowed_content_types=frozenset(
            {
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
        ),
        max_size_bytes=max_size,
        require_magic_bytes=True,
        compute_checksum=True,
    )


def get_pm_attachment_upload() -> FileUploadService:
    """Get PM/project attachment upload service."""
    return FileUploadService(_pm_attachment_config())


def get_discipline_attachment_upload() -> FileUploadService:
    """Get discipline attachment upload service."""
    return FileUploadService(_discipline_attachment_config())


def _expense_receipt_config() -> FileUploadConfig:
    return FileUploadConfig(
        base_dir="/app/uploads/expense_receipts",
        allowed_content_types=frozenset(
            {
                "image/jpeg",
                "image/png",
                "image/gif",
                "image/webp",
                "application/pdf",
            }
        ),
        allowed_extensions=frozenset(
            {
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".webp",
                ".pdf",
            }
        ),
        max_size_bytes=10 * 1024 * 1024,
        compute_checksum=True,
        require_magic_bytes=True,
    )


def get_expense_receipt_upload() -> FileUploadService:
    """Get expense receipt upload service."""
    return FileUploadService(_expense_receipt_config())
