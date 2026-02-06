"""
Discipline Attachment Service.

Handles file uploads, downloads, and deletions for disciplinary case documents.
Provides secure file storage with MIME type validation.
"""

import logging
import uuid
from pathlib import Path
from typing import BinaryIO, Optional

from sqlalchemy.orm import Session

from app.errors import NotFoundError, ValidationError
from app.models.people.discipline import CaseDocument, DisciplinaryCase, DocumentType
from app.services.common import coerce_uuid
from app.services.file_upload import (
    FileUploadError,
    get_discipline_attachment_upload,
    resolve_safe_path,
)

logger = logging.getLogger(__name__)


def _upload_service():
    return get_discipline_attachment_upload()


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
        path = _upload_service().base_path / str(organization_id) / str(case_id)
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
        org_id = coerce_uuid(organization_id)
        case_uuid = coerce_uuid(case_id)
        user_id = coerce_uuid(uploaded_by_id)

        # Verify case exists and belongs to organization
        case = self.db.get(DisciplinaryCase, case_uuid)
        if not case:
            raise NotFoundError(f"Disciplinary case {case_id} not found")
        if case.organization_id != org_id:
            raise NotFoundError(f"Disciplinary case {case_id} not found")

        file_bytes = file_content.read()
        upload_service = _upload_service()
        try:
            upload_result = upload_service.save(
                file_bytes,
                content_type=content_type,
                subdirs=[str(org_id), str(case_uuid)],
                original_filename=file_name,
            )
        except FileUploadError as exc:
            raise ValidationError(str(exc)) from exc

        # Create document record
        document = CaseDocument(
            case_id=case_uuid,
            document_type=document_type,
            title=title or file_name,
            description=description,
            file_path=upload_result.relative_path,
            file_name=file_name,
            file_size=upload_result.file_size,
            mime_type=content_type,
            uploaded_by_id=user_id,
        )

        self.db.add(document)
        self.db.flush()

        logger.info(
            "Uploaded document '%s' to case %s (size: %s)",
            file_name,
            case.case_number,
            upload_result.file_size,
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
        org_id = coerce_uuid(organization_id)
        doc_id = coerce_uuid(document_id)

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
        return resolve_safe_path(_upload_service().base_path, document.file_path)

    def list_for_case(
        self,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
    ) -> list[CaseDocument]:
        """List all documents for a specific case."""
        org_id = coerce_uuid(organization_id)
        case_uuid = coerce_uuid(case_id)

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
