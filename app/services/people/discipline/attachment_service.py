"""
Discipline Attachment Service.

Handles file uploads, downloads, and deletions for disciplinary case documents.
Provides secure file storage with MIME type validation.
"""

import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import BinaryIO, Optional

from sqlalchemy.orm import Session

from app.models.people.discipline import CaseDocument, DocumentType, DisciplinaryCase
from app.errors import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

# Configuration
UPLOAD_BASE_DIR = os.getenv("DISCIPLINE_UPLOAD_DIR", "uploads/discipline")
MAX_FILE_SIZE = int(
    os.getenv("MAX_DISCIPLINE_FILE_SIZE", 10 * 1024 * 1024)
)  # 10MB default

# Allowed MIME types for discipline documents
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

# Magic byte signatures for file type validation
MAGIC_BYTES = {
    b"%PDF": "application/pdf",
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"RIFF": "image/webp",  # WebP starts with RIFF
    b"\xd0\xcf\x11\xe0": "application/msword",  # Old DOC format
    b"PK\x03\x04": "application/zip",  # DOCX, XLSX are ZIP-based
}


def _coerce_uuid(value) -> uuid.UUID:
    """Convert string to UUID if needed."""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _validate_magic_bytes(file_content: BinaryIO, claimed_type: str) -> bool:
    """
    Validate file content against magic bytes.

    Checks first few bytes of file to verify claimed MIME type.
    Returns True if valid, False if suspicious.
    """
    # Read first 16 bytes for signature detection
    start_pos = file_content.tell()
    header = file_content.read(16)
    file_content.seek(start_pos)  # Reset position

    if not header:
        return False

    # Check for PDF
    if claimed_type == "application/pdf":
        return header.startswith(b"%PDF")

    # Check for images
    if claimed_type == "image/jpeg":
        return header.startswith(b"\xff\xd8\xff")
    if claimed_type == "image/png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if claimed_type in ("image/gif",):
        return header.startswith(b"GIF87a") or header.startswith(b"GIF89a")
    if claimed_type == "image/webp":
        return header.startswith(b"RIFF") and b"WEBP" in header[:16]

    # Check for Office formats (ZIP-based)
    if claimed_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ):
        return header.startswith(b"PK\x03\x04")

    # Check for old Office formats
    if claimed_type in ("application/msword", "application/vnd.ms-excel"):
        return header.startswith(b"\xd0\xcf\x11\xe0")

    # For text files, ensure no binary content
    if claimed_type in ("text/plain", "text/csv"):
        try:
            header.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False

    # Default allow for types we can't validate
    return True


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


def _resolve_safe_path(base_dir: Path, relative_path: str) -> Path:
    """Resolve path safely within upload directory to prevent traversal."""
    base = base_dir.resolve()
    full_path = (base / relative_path).resolve()

    # Ensure the resolved path is within the base directory
    if base not in full_path.parents and base != full_path:
        raise ValueError("Invalid file path")

    return full_path


class DisciplineAttachmentService:
    """
    Service for managing discipline case document attachments.

    Handles file storage, retrieval, and deletion with security validation.
    """

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def get_upload_path(organization_id: uuid.UUID, case_id: uuid.UUID) -> Path:
        """
        Get the upload directory path for a case.

        Creates directory structure: uploads/discipline/{org_id}/{case_id}/
        """
        path = Path(UPLOAD_BASE_DIR) / str(organization_id) / str(case_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_file(
        self,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
        file_content: BinaryIO,
        file_name: str,
        content_type: str,
        document_type: DocumentType,
        uploaded_by_id: uuid.UUID,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ) -> CaseDocument:
        """
        Save an uploaded file and create document record.

        Args:
            organization_id: Organization UUID
            case_id: Disciplinary case UUID
            file_content: File binary content
            file_name: Original file name
            content_type: MIME type of file
            document_type: Type of discipline document
            uploaded_by_id: User who uploaded the file
            title: Optional document title (defaults to filename)
            description: Optional document description

        Returns:
            Created CaseDocument record

        Raises:
            ValidationError: If file type not allowed or validation fails
            NotFoundError: If case not found
        """
        org_id = _coerce_uuid(organization_id)
        case_uuid = _coerce_uuid(case_id)
        user_id = _coerce_uuid(uploaded_by_id)

        # Verify case exists and belongs to organization
        case = self.db.get(DisciplinaryCase, case_uuid)
        if not case:
            raise NotFoundError(f"Disciplinary case {case_id} not found")
        if case.organization_id != org_id:
            raise NotFoundError(f"Disciplinary case {case_id} not found")

        # Validate content type
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ValidationError(f"Content type '{content_type}' is not allowed")

        # Validate magic bytes for security
        if not _validate_magic_bytes(file_content, content_type):
            raise ValidationError(
                "File content does not match declared type. "
                "Please ensure you're uploading the correct file."
            )

        # Generate unique filename
        file_ext = Path(file_name).suffix.lower()
        unique_name = f"{uuid.uuid4()}{file_ext}"

        # Get upload directory
        upload_dir = self.get_upload_path(org_id, case_uuid)
        file_path = upload_dir / unique_name

        # Read file content into memory with size limit to prevent disk exhaustion
        chunks = []
        bytes_read = 0
        while True:
            chunk = file_content.read(8192)
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > MAX_FILE_SIZE:
                raise ValidationError(
                    f"File size exceeds maximum allowed ({_format_file_size(MAX_FILE_SIZE)})"
                )
            chunks.append(chunk)

        file_size = bytes_read

        # Write validated content to disk
        with open(file_path, "wb") as f:
            for chunk in chunks:
                f.write(chunk)

        # Create document record
        relative_path = f"{org_id}/{case_uuid}/{unique_name}"
        document = CaseDocument(
            case_id=case_uuid,
            document_type=document_type,
            title=title or file_name,
            description=description,
            file_path=relative_path,
            file_name=file_name,
            file_size=file_size,
            mime_type=content_type,
            uploaded_by_id=user_id,
        )

        self.db.add(document)
        self.db.flush()

        logger.info(
            "Uploaded document '%s' to case %s (size: %s)",
            file_name,
            case.case_number,
            _format_file_size(file_size),
        )

        return document

    def get_document(
        self,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> Optional[CaseDocument]:
        """
        Get document by ID with organization validation.

        Returns None if document not found or doesn't belong to organization.
        """
        org_id = _coerce_uuid(organization_id)
        doc_id = _coerce_uuid(document_id)

        document = self.db.get(CaseDocument, doc_id)
        if not document:
            return None

        # Verify organization access via case
        case = self.db.get(DisciplinaryCase, document.case_id)
        if not case or case.organization_id != org_id:
            return None

        return document

    def get_document_or_404(
        self,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> CaseDocument:
        """Get document or raise NotFoundError."""
        document = self.get_document(organization_id, document_id)
        if not document:
            raise NotFoundError(f"Document {document_id} not found")
        return document

    def get_file_path(self, document: CaseDocument) -> Path:
        """
        Get the full file path for a document.

        Validates path is within upload directory for security.
        """
        base_dir = Path(UPLOAD_BASE_DIR)
        return _resolve_safe_path(base_dir, document.file_path)

    def list_for_case(
        self,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
    ) -> list[CaseDocument]:
        """List all documents for a specific case."""
        org_id = _coerce_uuid(organization_id)
        case_uuid = _coerce_uuid(case_id)

        # Verify case belongs to organization
        case = self.db.get(DisciplinaryCase, case_uuid)
        if not case or case.organization_id != org_id:
            return []

        return list(case.documents)

    def delete(
        self,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> bool:
        """
        Delete a document and its file.

        Returns True if deleted, False if not found.
        """
        document = self.get_document(organization_id, document_id)
        if not document:
            return False

        # Delete file from storage
        try:
            file_path = self.get_file_path(document)
            if file_path.exists():
                file_path.unlink()
                logger.info("Deleted file: %s", file_path)
        except (ValueError, FileNotFoundError) as e:
            logger.warning("Could not delete file: %s", e)

        # Delete database record
        self.db.delete(document)
        self.db.flush()

        logger.info("Deleted document %s", document_id)
        return True


# Singleton instance for convenience
discipline_attachment_service = DisciplineAttachmentService
