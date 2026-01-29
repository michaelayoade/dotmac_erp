"""
HR Document / Handbook Service.

Provides operations for managing HR policy documents and employee acknowledgments.
"""

import hashlib
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.people.hr import Employee
from app.models.people.hr.handbook import (
    DocumentCategory,
    DocumentStatus,
    HRDocument,
    HRDocumentAcknowledgment,
)

logger = logging.getLogger(__name__)

__all__ = [
    "HRDocumentService",
    "HRDocumentNotFoundError",
    "HRDocumentValidationError",
]


class HRDocumentNotFoundError(Exception):
    """HR Document not found."""

    def __init__(self, document_id: UUID | None = None, message: str | None = None):
        self.document_id = document_id
        self.message = message or f"HR Document {document_id} not found"
        super().__init__(self.message)


class HRDocumentValidationError(Exception):
    """HR Document validation error."""

    pass


class HRDocumentService:
    """
    Service for managing HR documents and acknowledgments.

    Provides:
    - Document CRUD operations
    - Version management
    - File storage
    - Acknowledgment tracking
    - Compliance reporting
    """

    # Default upload directory
    UPLOAD_DIR = Path(getattr(settings, "hr_documents_dir", "uploads/hr_documents"))

    def __init__(self, db: Session):
        self.db = db

    # =========================================================================
    # Document CRUD
    # =========================================================================

    def get_document(
        self,
        org_id: UUID,
        document_id: UUID,
    ) -> HRDocument:
        """Get a document by ID."""
        doc = self.db.scalar(
            select(HRDocument).where(
                HRDocument.organization_id == org_id,
                HRDocument.document_id == document_id,
            )
        )
        if not doc:
            raise HRDocumentNotFoundError(document_id)
        return doc

    def list_documents(
        self,
        org_id: UUID,
        *,
        category: DocumentCategory | None = None,
        status: DocumentStatus | None = None,
        active_only: bool = False,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[HRDocument], int]:
        """
        List documents with filters.

        Returns tuple of (documents, total_count).
        """
        query = select(HRDocument).where(HRDocument.organization_id == org_id)

        if category:
            query = query.where(HRDocument.category == category)

        if status:
            query = query.where(HRDocument.status == status)

        if active_only:
            today = date.today()
            query = query.where(
                HRDocument.status == DocumentStatus.ACTIVE,
                HRDocument.effective_date <= today,
            ).where(
                (HRDocument.expiry_date.is_(None)) | (HRDocument.expiry_date >= today)
            )

        if search:
            search_term = f"%{search}%"
            query = query.where(
                (HRDocument.title.ilike(search_term))
                | (HRDocument.document_code.ilike(search_term))
                | (HRDocument.description.ilike(search_term))
            )

        # Count total
        count_query = query.with_only_columns(func.count(HRDocument.document_id))
        total = self.db.scalar(count_query) or 0

        # Apply ordering and pagination
        query = query.order_by(
            HRDocument.category,
            HRDocument.document_code,
            HRDocument.version.desc(),
        )
        query = query.offset(offset).limit(limit)

        documents = list(self.db.scalars(query).all())
        return documents, total

    def get_latest_version(
        self,
        org_id: UUID,
        document_code: str,
    ) -> HRDocument | None:
        """Get the latest version of a document by code."""
        return self.db.scalar(
            select(HRDocument)
            .where(
                HRDocument.organization_id == org_id,
                HRDocument.document_code == document_code,
            )
            .order_by(HRDocument.version.desc())
        )

    def create_document(
        self,
        org_id: UUID,
        *,
        document_code: str,
        title: str,
        category: DocumentCategory,
        file_path: str,
        file_name: str,
        file_size_bytes: int,
        content_type: str = "application/pdf",
        content_hash: str | None = None,
        description: str | None = None,
        effective_date: date | None = None,
        expiry_date: date | None = None,
        requires_acknowledgment: bool = True,
        acknowledgment_deadline_days: int | None = None,
        applies_to_all_employees: bool = True,
        applies_to_departments: list[str] | None = None,
        tags: list[str] | None = None,
        status: DocumentStatus = DocumentStatus.DRAFT,
        created_by: UUID,
    ) -> HRDocument:
        """
        Create a new HR document.

        Automatically determines version number based on existing documents
        with the same code.
        """
        # Determine version
        existing = self.get_latest_version(org_id, document_code)
        version = 1
        previous_version_id = None

        if existing:
            version = existing.version + 1
            previous_version_id = existing.document_id
            # Mark previous version as superseded if activating new one
            if status == DocumentStatus.ACTIVE:
                existing.status = DocumentStatus.SUPERSEDED
                existing.updated_by = created_by
                existing.updated_at = datetime.now(timezone.utc)

        document = HRDocument(
            organization_id=org_id,
            document_code=document_code,
            title=title,
            description=description,
            category=category,
            version=version,
            previous_version_id=previous_version_id,
            file_path=file_path,
            file_name=file_name,
            content_type=content_type,
            file_size_bytes=file_size_bytes,
            content_hash=content_hash,
            effective_date=effective_date or date.today(),
            expiry_date=expiry_date,
            requires_acknowledgment=requires_acknowledgment,
            acknowledgment_deadline_days=acknowledgment_deadline_days,
            applies_to_all_employees=applies_to_all_employees,
            applies_to_departments=applies_to_departments,
            tags=tags,
            status=status,
            created_by=created_by,
        )

        self.db.add(document)
        self.db.flush()

        logger.info(
            "Created HR document %s v%d: %s",
            document_code,
            version,
            title,
        )

        return document

    def update_document(
        self,
        org_id: UUID,
        document_id: UUID,
        *,
        title: str | None = None,
        description: str | None = None,
        effective_date: date | None = None,
        expiry_date: date | None = None,
        requires_acknowledgment: bool | None = None,
        acknowledgment_deadline_days: int | None = None,
        applies_to_all_employees: bool | None = None,
        applies_to_departments: list[str] | None = None,
        tags: list[str] | None = None,
        status: DocumentStatus | None = None,
        updated_by: UUID,
    ) -> HRDocument:
        """Update document metadata (not file content)."""
        doc = self.get_document(org_id, document_id)

        if title is not None:
            doc.title = title
        if description is not None:
            doc.description = description
        if effective_date is not None:
            doc.effective_date = effective_date
        if expiry_date is not None:
            doc.expiry_date = expiry_date
        if requires_acknowledgment is not None:
            doc.requires_acknowledgment = requires_acknowledgment
        if acknowledgment_deadline_days is not None:
            doc.acknowledgment_deadline_days = acknowledgment_deadline_days
        if applies_to_all_employees is not None:
            doc.applies_to_all_employees = applies_to_all_employees
        if applies_to_departments is not None:
            doc.applies_to_departments = applies_to_departments
        if tags is not None:
            doc.tags = tags
        if status is not None:
            doc.status = status

        doc.updated_by = updated_by
        doc.updated_at = datetime.now(timezone.utc)

        self.db.flush()
        logger.info("Updated HR document %s", document_id)

        return doc

    def activate_document(
        self,
        org_id: UUID,
        document_id: UUID,
        updated_by: UUID,
    ) -> HRDocument:
        """Activate a document and supersede previous versions."""
        doc = self.get_document(org_id, document_id)

        if doc.status == DocumentStatus.ACTIVE:
            return doc

        # Supersede any other active versions with same code
        active_docs = self.db.scalars(
            select(HRDocument).where(
                HRDocument.organization_id == org_id,
                HRDocument.document_code == doc.document_code,
                HRDocument.status == DocumentStatus.ACTIVE,
                HRDocument.document_id != document_id,
            )
        ).all()

        for active_doc in active_docs:
            active_doc.status = DocumentStatus.SUPERSEDED
            active_doc.updated_by = updated_by
            active_doc.updated_at = datetime.now(timezone.utc)

        doc.status = DocumentStatus.ACTIVE
        doc.updated_by = updated_by
        doc.updated_at = datetime.now(timezone.utc)

        self.db.flush()
        logger.info("Activated HR document %s v%d", doc.document_code, doc.version)

        return doc

    def archive_document(
        self,
        org_id: UUID,
        document_id: UUID,
        updated_by: UUID,
    ) -> HRDocument:
        """Archive a document."""
        doc = self.get_document(org_id, document_id)
        doc.status = DocumentStatus.ARCHIVED
        doc.updated_by = updated_by
        doc.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        logger.info("Archived HR document %s", document_id)
        return doc

    # =========================================================================
    # File Management
    # =========================================================================

    def save_document_file(
        self,
        org_id: UUID,
        file_name: str,
        file_content: bytes,
    ) -> tuple[str, int, str]:
        """
        Save a document file and return (path, size, hash).

        Args:
            org_id: Organization ID
            file_name: Original filename
            file_content: File bytes

        Returns:
            Tuple of (relative_path, file_size_bytes, content_hash)

        Raises:
            HRDocumentValidationError: If file cannot be saved (disk full, permission denied, etc.)
        """
        # Generate unique filename
        import uuid as uuid_module

        file_id = str(uuid_module.uuid4())
        ext = Path(file_name).suffix or ".pdf"

        # Validate extension (only allow safe document types)
        allowed_extensions = {".pdf", ".doc", ".docx", ".txt", ".rtf"}
        if ext.lower() not in allowed_extensions:
            raise HRDocumentValidationError(
                f"File type '{ext}' not allowed. Allowed types: {', '.join(allowed_extensions)}"
            )

        safe_name = f"{file_id}{ext}"

        try:
            # Create org-specific directory
            org_dir = self.UPLOAD_DIR / str(org_id)
            org_dir.mkdir(parents=True, exist_ok=True)

            file_path = org_dir / safe_name

            # Write file
            file_path.write_bytes(file_content)

        except PermissionError:
            logger.error("Permission denied saving HR document to %s", self.UPLOAD_DIR)
            raise HRDocumentValidationError(
                "Unable to save document: permission denied. Please contact administrator."
            )
        except OSError as e:
            # Covers disk full, I/O errors, etc.
            if "No space left on device" in str(e) or e.errno == 28:  # ENOSPC
                logger.error("Disk full when saving HR document")
                raise HRDocumentValidationError(
                    "Unable to save document: storage is full. Please contact administrator."
                )
            logger.exception("OS error saving HR document: %s", e)
            raise HRDocumentValidationError(
                f"Unable to save document: {e}. Please try again or contact administrator."
            )

        # Calculate hash
        content_hash = hashlib.sha256(file_content).hexdigest()

        # Return relative path
        relative_path = f"{org_id}/{safe_name}"

        logger.info("Saved HR document file: %s (%d bytes)", relative_path, len(file_content))

        return relative_path, len(file_content), content_hash

    def get_document_path(self, document: HRDocument) -> Path:
        """Get full filesystem path for a document."""
        return self.UPLOAD_DIR / document.file_path

    # =========================================================================
    # Acknowledgment Management
    # =========================================================================

    def acknowledge_document(
        self,
        org_id: UUID,
        document_id: UUID,
        employee_id: UUID,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
        confirmation_text: str | None = None,
        signature_data: str | None = None,
    ) -> HRDocumentAcknowledgment:
        """
        Record employee acknowledgment of a document.

        Validates that both document and employee belong to the organization.
        Raises error if already acknowledged.
        """
        doc = self.get_document(org_id, document_id)

        # SECURITY: Verify employee belongs to the same organization
        employee = self.db.scalar(
            select(Employee).where(
                Employee.employee_id == employee_id,
                Employee.organization_id == org_id,
            )
        )
        if not employee:
            raise HRDocumentValidationError("Employee not found in this organization")

        if not doc.requires_acknowledgment:
            raise HRDocumentValidationError("This document does not require acknowledgment")

        if doc.status != DocumentStatus.ACTIVE:
            raise HRDocumentValidationError("Can only acknowledge active documents")

        # Check for existing acknowledgment (with org validation)
        existing = self.get_acknowledgment(org_id, document_id, employee_id)

        if existing:
            raise HRDocumentValidationError("Document already acknowledged")

        ack = HRDocumentAcknowledgment(
            document_id=document_id,
            employee_id=employee_id,
            ip_address=ip_address,
            user_agent=user_agent,
            confirmation_text=confirmation_text or "I have read and understood this document.",
            signature_data=signature_data,
        )

        # Handle race condition: if another request created the acknowledgment
        # between our check and insert, the unique constraint will catch it.
        try:
            self.db.add(ack)
            self.db.flush()
        except Exception as e:
            # Check if it's a unique constraint violation
            from sqlalchemy.exc import IntegrityError

            if isinstance(e, IntegrityError) and "uq_hr_doc_ack_document_employee" in str(e):
                # Race condition - acknowledgment was created by another request
                logger.info(
                    "Acknowledgment race condition for employee %s, document %s",
                    employee_id,
                    document_id,
                )
                # Return the existing acknowledgment
                self.db.rollback()
                existing = self.get_acknowledgment(org_id, document_id, employee_id)
                if existing:
                    return existing
                # If still not found, re-raise (shouldn't happen)
                raise HRDocumentValidationError("Document already acknowledged")
            raise

        logger.info(
            "Employee %s acknowledged document %s",
            employee_id,
            document_id,
        )

        return ack

    def get_acknowledgment(
        self,
        org_id: UUID,
        document_id: UUID,
        employee_id: UUID,
    ) -> HRDocumentAcknowledgment | None:
        """
        Get acknowledgment record if exists.

        Validates that both document and employee belong to the organization
        to prevent cross-tenant data access.
        """
        return self.db.scalar(
            select(HRDocumentAcknowledgment)
            .join(HRDocument)
            .join(Employee, HRDocumentAcknowledgment.employee_id == Employee.employee_id)
            .where(
                HRDocument.organization_id == org_id,
                Employee.organization_id == org_id,
                HRDocumentAcknowledgment.document_id == document_id,
                HRDocumentAcknowledgment.employee_id == employee_id,
            )
        )

    def list_document_acknowledgments(
        self,
        org_id: UUID,
        document_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[HRDocumentAcknowledgment], int]:
        """List all acknowledgments for a document."""
        # Verify document belongs to org
        self.get_document(org_id, document_id)

        query = (
            select(HRDocumentAcknowledgment)
            .options(joinedload(HRDocumentAcknowledgment.employee))
            .where(HRDocumentAcknowledgment.document_id == document_id)
        )

        count_query = query.with_only_columns(func.count(HRDocumentAcknowledgment.acknowledgment_id))
        total = self.db.scalar(count_query) or 0

        query = query.order_by(HRDocumentAcknowledgment.acknowledged_at.desc())
        query = query.offset(offset).limit(limit)

        acks = list(self.db.scalars(query).all())
        return acks, total

    def get_employee_pending_documents(
        self,
        org_id: UUID,
        employee_id: UUID,
    ) -> list[HRDocument]:
        """Get documents an employee hasn't acknowledged yet."""
        # Get all acknowledged document IDs for this employee in one query
        acknowledged_doc_ids = set(
            self.db.scalars(
                select(HRDocumentAcknowledgment.document_id)
                .join(HRDocument)
                .where(
                    HRDocument.organization_id == org_id,
                    HRDocumentAcknowledgment.employee_id == employee_id,
                )
            ).all()
        )

        # Get active documents requiring acknowledgment
        active_docs = self.db.scalars(
            select(HRDocument).where(
                HRDocument.organization_id == org_id,
                HRDocument.status == DocumentStatus.ACTIVE,
                HRDocument.requires_acknowledgment == True,
                HRDocument.effective_date <= date.today(),
            ).where(
                (HRDocument.expiry_date.is_(None)) | (HRDocument.expiry_date >= date.today())
            )
        ).all()

        # Filter out already acknowledged (in memory - no N+1)
        return [doc for doc in active_docs if doc.document_id not in acknowledged_doc_ids]

    def get_employee_acknowledgments(
        self,
        org_id: UUID,
        employee_id: UUID,
    ) -> list[HRDocumentAcknowledgment]:
        """Get all documents an employee has acknowledged."""
        return list(
            self.db.scalars(
                select(HRDocumentAcknowledgment)
                .options(joinedload(HRDocumentAcknowledgment.document))
                .join(HRDocument)
                .where(
                    HRDocument.organization_id == org_id,
                    HRDocumentAcknowledgment.employee_id == employee_id,
                )
                .order_by(HRDocumentAcknowledgment.acknowledged_at.desc())
            ).all()
        )

    # =========================================================================
    # Compliance Reporting
    # =========================================================================

    def get_acknowledgment_stats(
        self,
        org_id: UUID,
        document_id: UUID,
    ) -> dict:
        """
        Get acknowledgment statistics for a document.

        Returns dict with total_employees, acknowledged_count, pending_count, percentage.
        """
        doc = self.get_document(org_id, document_id)

        # Count active employees
        total_employees = self.db.scalar(
            select(func.count(Employee.employee_id)).where(
                Employee.organization_id == org_id,
                Employee.status == "active",
                Employee.is_deleted == False,
            )
        ) or 0

        # Count acknowledgments
        acknowledged_count = self.db.scalar(
            select(func.count(HRDocumentAcknowledgment.acknowledgment_id)).where(
                HRDocumentAcknowledgment.document_id == document_id,
            )
        ) or 0

        pending_count = total_employees - acknowledged_count
        percentage = int((acknowledged_count / total_employees * 100)) if total_employees > 0 else 0

        return {
            "total_employees": total_employees,
            "acknowledged_count": acknowledged_count,
            "pending_count": pending_count,
            "percentage": percentage,
        }

    def get_batch_acknowledgment_stats(
        self,
        org_id: UUID,
        document_ids: list[UUID],
    ) -> dict[UUID, dict]:
        """
        Get acknowledgment statistics for multiple documents in a single query.

        More efficient than calling get_acknowledgment_stats() in a loop.

        Returns dict mapping document_id to stats dict with:
        - total_employees
        - acknowledged_count
        - pending_count
        - percentage
        """
        if not document_ids:
            return {}

        # Count active employees (one query - shared across all docs)
        total_employees = self.db.scalar(
            select(func.count(Employee.employee_id)).where(
                Employee.organization_id == org_id,
                Employee.status == "active",
                Employee.is_deleted == False,
            )
        ) or 0

        # Get acknowledgment counts for all documents in one query
        ack_rows = self.db.execute(
            select(
                HRDocumentAcknowledgment.document_id,
                func.count(HRDocumentAcknowledgment.acknowledgment_id),
            )
            .join(HRDocument)
            .where(
                HRDocument.organization_id == org_id,
                HRDocumentAcknowledgment.document_id.in_(document_ids),
            )
            .group_by(HRDocumentAcknowledgment.document_id)
        ).all()
        ack_counts: dict[UUID, int] = {doc_id: count for doc_id, count in ack_rows}

        # Build stats for each document
        stats_map: dict[UUID, dict] = {}
        for doc_id in document_ids:
            acknowledged_count = ack_counts.get(doc_id, 0)
            pending_count = max(0, total_employees - acknowledged_count)
            percentage = int((acknowledged_count / total_employees * 100)) if total_employees > 0 else 0

            stats_map[doc_id] = {
                "total_employees": total_employees,
                "acknowledged_count": acknowledged_count,
                "pending_count": pending_count,
                "percentage": percentage,
            }

        return stats_map

    def get_pending_employees(
        self,
        org_id: UUID,
        document_id: UUID,
    ) -> list[Employee]:
        """Get list of employees who haven't acknowledged a document."""
        doc = self.get_document(org_id, document_id)

        # Get acknowledged employee IDs
        acknowledged_ids = self.db.scalars(
            select(HRDocumentAcknowledgment.employee_id).where(
                HRDocumentAcknowledgment.document_id == document_id,
            )
        ).all()

        # Get active employees not in acknowledged list
        query = select(Employee).where(
            Employee.organization_id == org_id,
            Employee.status == "active",
            Employee.is_deleted == False,
        )

        if acknowledged_ids:
            query = query.where(Employee.employee_id.not_in(acknowledged_ids))

        return list(self.db.scalars(query).all())
