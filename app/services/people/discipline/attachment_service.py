"""
Discipline Attachment Service.

Handles file uploads, downloads, and deletions for disciplinary case documents.
Provides secure file storage with MIME type validation. New uploads
are ``dotmac-files`` tenant records staged in the caller's
transaction; ``CaseDocument.file_path`` then holds the canonical
tenant storage key. Pre-cutover relative paths keep their legacy
serving branch until
``scripts/backfill_discipline_document_files.py`` rewrites them.
"""

import logging
import uuid
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.errors import NotFoundError, ValidationError
from app.models.people.discipline import CaseDocument, DisciplinaryCase, DocumentType
from app.services.common import coerce_uuid
from app.services.file_upload import (
    FileUploadError,
    FileUploadService,
    get_discipline_attachment_upload,
    resolve_safe_path,
)

logger = logging.getLogger(__name__)


def _upload_service() -> FileUploadService:
    return get_discipline_attachment_upload()


def staged_file_id(document: CaseDocument) -> uuid.UUID | None:
    """The staged file id for a post-cutover document, else None."""
    file_path = document.file_path or ""
    if not file_path.startswith("tenants/"):
        return None
    try:
        return uuid.UUID(file_path.rsplit("/", 1)[-1])
    except ValueError:
        return None


def _stage_discipline_file(
    db: Session,
    organization_id: uuid.UUID,
    *,
    file_data: bytes,
    content_type: str | None,
    original_filename: str | None,
):
    """Upload and stage one discipline document on the tenant files plane."""
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

    config = _upload_service().config
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
            original_filename=original_filename or "document",
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
        title: str | None = None,
        description: str | None = None,
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
        try:
            prepared = _stage_discipline_file(
                self.db,
                org_id,
                file_data=file_bytes,
                content_type=content_type,
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
            file_path=prepared.storage_key,
            file_name=file_name,
            file_size=prepared.size_bytes,
            mime_type=content_type,
            uploaded_by_id=user_id,
        )

        self.db.add(document)
        self.db.flush()

        logger.info(
            "Uploaded document '%s' to case %s (size: %s)",
            file_name,
            case.case_number,
            prepared.size_bytes,
        )

        return document

    def get_document(
        self,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> CaseDocument | None:
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
        Post-cutover staged documents have no local path; resolving
        their tenant storage key raises ValueError like any unsafe
        path, and download callers use staged_download_response.
        """
        return resolve_safe_path(_upload_service().base_path, document.file_path)

    def staged_download_response(
        self, organization_id: uuid.UUID, document: CaseDocument
    ):
        """Stream a post-cutover document, or None when it is not staged."""
        file_id = staged_file_id(document)
        if file_id is None:
            return None

        from dotmac_files import FileError, download_target, open_object
        from fastapi.responses import StreamingResponse

        from app.services.storage import get_dotmac_files_provider
        from app.tenancy import OrganizationTenantContext

        scope = OrganizationTenantContext.for_organization(
            coerce_uuid(organization_id)
        ).tenant_scope
        try:
            target = download_target(self.db, scope=scope, file_id=file_id)
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
            or document.mime_type
            or "application/octet-stream",
            headers={
                "Content-Disposition": (f'attachment; filename="{document.file_name}"'),
                "Content-Length": str(target.size_bytes),
            },
        )

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

        # Retire the staged file record, or delete the legacy object.
        staged_id = staged_file_id(document)
        if staged_id is not None:
            from datetime import datetime, timezone

            from dotmac_files import FileError, request_deletion

            from app.tenancy import OrganizationTenantContext

            try:
                request_deletion(
                    self.db,
                    scope=OrganizationTenantContext.for_organization(
                        coerce_uuid(organization_id)
                    ).tenant_scope,
                    file_id=staged_id,
                    now=datetime.now(timezone.utc),
                )
            except FileError:
                logger.warning(
                    "document file %s could not be marked for deletion", staged_id
                )
            self.db.delete(document)
            self.db.flush()
            logger.info("Deleted document %s", document_id)
            return True

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
