"""Employee Extended Data Services.

Services for managing employee documents, qualifications, certifications,
dependents, and skills.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from decimal import Decimal
from typing import TYPE_CHECKING, Any, BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.people.hr import (
    DocumentType,
    Employee,
    EmployeeCertification,
    EmployeeDependent,
    EmployeeDocument,
    EmployeeQualification,
    EmployeeSkill,
    EmployeeStatus,
    Gender as DependentGender,
    QualificationType,
    RelationshipType,
    Skill,
    SkillCategory,
)
from app.services.file_upload import FileUploadError, get_employee_document_upload
from app.services.storage import get_storage

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.auth import Principal

__all__ = [
    "EmployeeDocumentService",
    "ResolvedDocumentDownload",
    "EmployeeQualificationService",
    "EmployeeCertificationService",
    "EmployeeDependentService",
    "SkillService",
    "EmployeeSkillService",
    "EmployeeExtendedSelfServiceService",
]


# =============================================================================
# Exceptions
# =============================================================================


class EmployeeExtendedDataError(Exception):
    """Base exception for employee extended data errors."""

    pass


class DocumentNotFoundError(EmployeeExtendedDataError):
    """Document not found."""

    pass


class QualificationNotFoundError(EmployeeExtendedDataError):
    """Qualification not found."""

    pass


class CertificationNotFoundError(EmployeeExtendedDataError):
    """Certification not found."""

    pass


class DependentNotFoundError(EmployeeExtendedDataError):
    """Dependent not found."""

    pass


class SkillNotFoundError(EmployeeExtendedDataError):
    """Skill not found."""

    pass


class EmployeeSkillNotFoundError(EmployeeExtendedDataError):
    """Employee skill not found."""

    pass


@dataclass(frozen=True)
class ResolvedDocumentDownload:
    """Tenant-scoped employee document stream payload."""

    chunks: AsyncIterable[str | bytes] | Iterable[str | bytes]
    content_type: str
    content_length: int | None
    filename: str


def _clean_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    return text


def _require_length(value: str | None, field_name: str, max_length: int) -> None:
    if value and len(value) > max_length:
        raise EmployeeExtendedDataError(
            f"{field_name} must be {max_length} characters or fewer"
        )


def _safe_download_filename(value: str | None, fallback: str) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return fallback
    return Path(candidate).name or fallback


# =============================================================================
# EmployeeDocumentService
# =============================================================================


class EmployeeDocumentService:
    """Service for managing employee documents."""

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        principal: Principal | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    def _get_employee(self, employee_id: uuid.UUID) -> Employee:
        employee = self.db.scalar(
            select(Employee).where(
                Employee.employee_id == employee_id,
                Employee.organization_id == self.organization_id,
                Employee.status != EmployeeStatus.TERMINATED,
            )
        )
        if not employee:
            raise EmployeeExtendedDataError(f"Employee {employee_id} not found")
        return employee

    def list_documents(
        self,
        employee_id: uuid.UUID,
        document_type: DocumentType | None = None,
        is_verified: bool | None = None,
        include_expired: bool = True,
    ) -> list[EmployeeDocument]:
        """List documents for an employee."""
        query = select(EmployeeDocument).where(
            EmployeeDocument.organization_id == self.organization_id,
            EmployeeDocument.employee_id == employee_id,
            EmployeeDocument.is_active.is_(True),
        )

        if document_type:
            query = query.where(EmployeeDocument.document_type == document_type)
        if is_verified is not None:
            query = query.where(EmployeeDocument.is_verified == is_verified)
        if not include_expired:
            query = query.where(
                (EmployeeDocument.expiry_date == None)
                | (EmployeeDocument.expiry_date >= date.today())
            )

        query = query.order_by(EmployeeDocument.uploaded_at.desc())
        return list(self.db.scalars(query).all())

    def get_document(
        self,
        document_id: uuid.UUID,
    ) -> EmployeeDocument:
        """Get a document by ID."""
        doc = self.db.scalar(
            select(EmployeeDocument).where(
                EmployeeDocument.document_id == document_id,
                EmployeeDocument.organization_id == self.organization_id,
                EmployeeDocument.is_active.is_(True),
            )
        )
        if not doc:
            raise DocumentNotFoundError(f"Document {document_id} not found")
        return doc

    def resolve_document_download(
        self,
        document_id: uuid.UUID,
    ) -> ResolvedDocumentDownload:
        """Resolve an active employee document to a storage-backed download stream."""
        document = self.get_document(document_id)
        storage = get_storage()
        s3_key = document.file_path
        if not s3_key.startswith("employee_documents/"):
            s3_key = f"employee_documents/{s3_key}"
        if not storage.exists(s3_key):
            raise DocumentNotFoundError(f"Document {document_id} not found")
        chunks, content_type, content_length = storage.stream(s3_key)
        return ResolvedDocumentDownload(
            chunks=chunks,
            content_type=content_type or "application/octet-stream",
            content_length=content_length,
            filename=_safe_download_filename(
                document.file_name,
                fallback=f"employee-document-{document.document_id}",
            ),
        )

    def resolve_owned_document_download(
        self,
        employee_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> ResolvedDocumentDownload:
        """Resolve a tenant and employee-owned document download."""
        document = self.get_document(document_id)
        if document.employee_id != employee_id:
            raise DocumentNotFoundError(f"Document {document_id} not found")
        return self.resolve_document_download(document_id)

    def create_document(
        self,
        employee_id: uuid.UUID,
        document_type: DocumentType,
        document_name: str,
        file_path: str,
        file_name: str,
        file_size: int | None = None,
        mime_type: str | None = None,
        content_checksum: str | None = None,
        description: str | None = None,
        issue_date: date | None = None,
        expiry_date: date | None = None,
    ) -> EmployeeDocument:
        """Create a new document record."""
        self._get_employee(employee_id)
        return self._create_document_record(
            employee_id=employee_id,
            document_type=document_type,
            document_name=document_name,
            file_path=file_path,
            file_name=file_name,
            file_size=file_size,
            mime_type=mime_type,
            content_checksum=content_checksum,
            description=description,
            issue_date=issue_date,
            expiry_date=expiry_date,
        )

    def _create_document_record(
        self,
        employee_id: uuid.UUID,
        document_type: DocumentType,
        document_name: str,
        file_path: str,
        file_name: str,
        file_size: int | None = None,
        mime_type: str | None = None,
        content_checksum: str | None = None,
        description: str | None = None,
        issue_date: date | None = None,
        expiry_date: date | None = None,
    ) -> EmployeeDocument:
        """Persist document metadata after employee ownership is validated."""
        doc = EmployeeDocument(
            organization_id=self.organization_id,
            employee_id=employee_id,
            document_type=document_type,
            document_name=document_name,
            file_path=file_path,
            file_name=file_name,
            file_size=file_size,
            mime_type=mime_type,
            content_checksum=content_checksum,
            description=description,
            issue_date=issue_date,
            expiry_date=expiry_date,
        )
        self.db.add(doc)
        self.db.flush()
        return doc

    def upload_document(
        self,
        employee_id: uuid.UUID,
        document_type: DocumentType,
        document_name: str,
        file_content: BinaryIO,
        file_name: str,
        content_type: str | None,
        description: str | None = None,
        issue_date: date | None = None,
        expiry_date: date | None = None,
    ) -> EmployeeDocument:
        """Upload a file and create its tenant-scoped employee document record."""
        self._get_employee(employee_id)

        original_filename = file_name.strip()
        if not original_filename:
            raise EmployeeExtendedDataError("Select a file to upload")

        file_bytes = file_content.read()
        if not file_bytes:
            raise EmployeeExtendedDataError("The selected file is empty")

        upload_service = get_employee_document_upload()
        try:
            upload_result = upload_service.save(
                file_data=file_bytes,
                content_type=content_type or "application/octet-stream",
                subdirs=(str(self.organization_id), str(employee_id)),
                original_filename=original_filename,
            )
        except FileUploadError as exc:
            raise EmployeeExtendedDataError(str(exc)) from exc

        try:
            document = self._create_document_record(
                employee_id=employee_id,
                document_type=document_type,
                document_name=document_name,
                file_path=upload_result.relative_path,
                file_name=original_filename,
                file_size=upload_result.file_size,
                mime_type=content_type,
                content_checksum=upload_result.checksum,
                description=description,
                issue_date=issue_date,
                expiry_date=expiry_date,
            )
        except Exception:
            try:
                upload_service.delete(upload_result.relative_path)
            except Exception:
                logger.exception(
                    "Failed to clean up employee document upload after database error"
                )
            raise

        logger.info(
            "Uploaded employee document %s for employee %s",
            document.document_id,
            employee_id,
        )
        return document

    def update_document(
        self,
        document_id: uuid.UUID,
        document_name: str | None = None,
        description: str | None = None,
        issue_date: date | None = None,
        expiry_date: date | None = None,
    ) -> EmployeeDocument:
        """Update document metadata."""
        doc = self.get_document(document_id)
        if document_name is not None:
            doc.document_name = document_name
        if description is not None:
            doc.description = description
        if issue_date is not None:
            doc.issue_date = issue_date
        if expiry_date is not None:
            doc.expiry_date = expiry_date
        self.db.flush()
        return doc

    def get_employee_id_for_person(self, person_id: uuid.UUID) -> uuid.UUID | None:
        """Look up the employee_id for a person within this organization."""
        emp = self.db.scalar(
            select(Employee).where(
                Employee.organization_id == self.organization_id,
                Employee.person_id == person_id,
            )
        )
        return emp.employee_id if emp else None

    def verify_document(
        self,
        document_id: uuid.UUID,
        verified_by_id: uuid.UUID,
        notes: str | None = None,
    ) -> EmployeeDocument:
        """Mark document as verified."""
        doc = self.get_document(document_id)
        doc.is_verified = True
        doc.verified_by_id = verified_by_id
        doc.verified_at = datetime.now(UTC)
        doc.verification_notes = notes
        self.db.flush()
        return doc

    def delete_document(self, document_id: uuid.UUID) -> None:
        """Soft delete a document."""
        doc = self.get_document(document_id)
        doc.is_active = False
        self.db.flush()

    def get_expiring_documents(
        self,
        days_until_expiry: int = 30,
    ) -> list[EmployeeDocument]:
        """Get documents expiring within specified days."""
        cutoff = date.today()
        end_date = date.today()
        from datetime import timedelta

        end_date = cutoff + timedelta(days=days_until_expiry)

        query = (
            select(EmployeeDocument)
            .where(
                EmployeeDocument.organization_id == self.organization_id,
                EmployeeDocument.is_active.is_(True),
                EmployeeDocument.expiry_date != None,
                EmployeeDocument.expiry_date >= cutoff,
                EmployeeDocument.expiry_date <= end_date,
            )
            .options(selectinload(EmployeeDocument.employee))
        )

        return list(self.db.scalars(query).all())


# =============================================================================
# EmployeeQualificationService
# =============================================================================


class EmployeeQualificationService:
    """Service for managing employee qualifications."""

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        principal: Principal | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    def _get_employee(self, employee_id: uuid.UUID) -> Employee:
        employee = self.db.scalar(
            select(Employee).where(
                Employee.employee_id == employee_id,
                Employee.organization_id == self.organization_id,
                Employee.status != EmployeeStatus.TERMINATED,
            )
        )
        if not employee:
            raise EmployeeExtendedDataError(f"Employee {employee_id} not found")
        return employee

    @staticmethod
    def _validate_payload(
        *,
        qualification_type: QualificationType,
        qualification_name: str,
        institution_name: str,
        field_of_study: str | None = None,
        institution_location: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        is_ongoing: bool = False,
        grade: str | None = None,
        score: float | None = None,
        max_score: float | None = None,
        notes: str | None = None,
    ) -> None:
        del qualification_type
        qualification_name_clean = _clean_text(qualification_name)
        institution_name_clean = _clean_text(institution_name)
        if not qualification_name_clean or not institution_name_clean:
            raise EmployeeExtendedDataError(
                "Qualification name and institution name are required"
            )
        _require_length(qualification_name_clean, "qualification_name", 200)
        _require_length(_clean_text(field_of_study), "field_of_study", 200)
        _require_length(institution_name_clean, "institution_name", 255)
        _require_length(
            _clean_text(institution_location),
            "institution_location",
            200,
        )
        _require_length(_clean_text(grade), "grade", 50)
        if is_ongoing and end_date is not None:
            raise EmployeeExtendedDataError(
                "Ongoing qualifications cannot include an end date"
            )
        if start_date and end_date and end_date < start_date:
            raise EmployeeExtendedDataError(
                "Qualification end date cannot be before start date"
            )
        if score is not None and score < 0:
            raise EmployeeExtendedDataError("Score cannot be negative")
        if max_score is not None and max_score <= 0:
            raise EmployeeExtendedDataError("Maximum score must be greater than zero")
        if score is not None and max_score is not None and score > max_score:
            raise EmployeeExtendedDataError("Score cannot exceed maximum score")
        _require_length(_clean_text(notes), "notes", 5000)

    def list_qualifications(
        self,
        employee_id: uuid.UUID,
        qualification_type: QualificationType | None = None,
    ) -> list[EmployeeQualification]:
        """List qualifications for an employee."""
        query = select(EmployeeQualification).where(
            EmployeeQualification.organization_id == self.organization_id,
            EmployeeQualification.employee_id == employee_id,
            EmployeeQualification.is_active.is_(True),
        )

        if qualification_type:
            query = query.where(
                EmployeeQualification.qualification_type == qualification_type
            )

        query = query.order_by(EmployeeQualification.end_date.desc().nullslast())
        return list(self.db.scalars(query).all())

    def get_qualification(
        self,
        qualification_id: uuid.UUID,
    ) -> EmployeeQualification:
        """Get a qualification by ID."""
        qual = self.db.scalar(
            select(EmployeeQualification).where(
                EmployeeQualification.qualification_id == qualification_id,
                EmployeeQualification.organization_id == self.organization_id,
                EmployeeQualification.is_active.is_(True),
            )
        )
        if not qual:
            raise QualificationNotFoundError(
                f"Qualification {qualification_id} not found"
            )
        return qual

    def create_qualification(
        self,
        employee_id: uuid.UUID,
        qualification_type: QualificationType,
        qualification_name: str,
        institution_name: str,
        field_of_study: str | None = None,
        institution_location: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        is_ongoing: bool = False,
        grade: str | None = None,
        score: float | None = None,
        max_score: float | None = None,
        document_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> EmployeeQualification:
        """Create a new qualification record."""
        self._get_employee(employee_id)
        self._validate_payload(
            qualification_type=qualification_type,
            qualification_name=qualification_name,
            institution_name=institution_name,
            field_of_study=field_of_study,
            institution_location=institution_location,
            start_date=start_date,
            end_date=end_date,
            is_ongoing=is_ongoing,
            grade=grade,
            score=score,
            max_score=max_score,
            notes=notes,
        )
        qual = EmployeeQualification(
            organization_id=self.organization_id,
            employee_id=employee_id,
            qualification_type=qualification_type,
            qualification_name=qualification_name,
            institution_name=institution_name,
            field_of_study=field_of_study,
            institution_location=institution_location,
            start_date=start_date,
            end_date=end_date,
            is_ongoing=is_ongoing,
            grade=grade,
            score=score,
            max_score=max_score,
            document_id=document_id,
            notes=notes,
        )
        self.db.add(qual)
        self.db.flush()
        return qual

    def update_qualification(
        self,
        qualification_id: uuid.UUID,
        **kwargs,
    ) -> EmployeeQualification:
        """Update a qualification."""
        qual = self.get_qualification(qualification_id)
        merged = {
            "qualification_type": kwargs.get(
                "qualification_type", qual.qualification_type
            ),
            "qualification_name": kwargs.get(
                "qualification_name", qual.qualification_name
            ),
            "institution_name": kwargs.get("institution_name", qual.institution_name),
            "field_of_study": kwargs.get("field_of_study", qual.field_of_study),
            "institution_location": kwargs.get(
                "institution_location",
                qual.institution_location,
            ),
            "start_date": kwargs.get("start_date", qual.start_date),
            "end_date": kwargs.get("end_date", qual.end_date),
            "is_ongoing": kwargs.get("is_ongoing", qual.is_ongoing),
            "grade": kwargs.get("grade", qual.grade),
            "score": kwargs.get(
                "score", float(qual.score) if qual.score is not None else None
            ),
            "max_score": kwargs.get(
                "max_score",
                float(qual.max_score) if qual.max_score is not None else None,
            ),
            "notes": kwargs.get("notes", qual.notes),
        }
        self._validate_payload(**merged)
        allowed_fields = {
            "qualification_type",
            "qualification_name",
            "institution_name",
            "field_of_study",
            "institution_location",
            "start_date",
            "end_date",
            "is_ongoing",
            "grade",
            "score",
            "max_score",
            "document_id",
            "notes",
        }
        for key, value in kwargs.items():
            if key in allowed_fields and value is not None:
                setattr(qual, key, value)
        self.db.flush()
        return qual

    def verify_qualification(
        self,
        qualification_id: uuid.UUID,
    ) -> EmployeeQualification:
        """Mark qualification as verified."""
        qual = self.get_qualification(qualification_id)
        qual.is_verified = True
        self.db.flush()
        return qual

    def delete_qualification(self, qualification_id: uuid.UUID) -> None:
        """Soft delete a qualification."""
        qual = self.get_qualification(qualification_id)
        qual.is_active = False
        self.db.flush()


# =============================================================================
# EmployeeCertificationService
# =============================================================================


class EmployeeCertificationService:
    """Service for managing employee certifications."""

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        principal: Principal | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    def _get_employee(self, employee_id: uuid.UUID) -> Employee:
        employee = self.db.scalar(
            select(Employee).where(
                Employee.employee_id == employee_id,
                Employee.organization_id == self.organization_id,
                Employee.status != EmployeeStatus.TERMINATED,
            )
        )
        if not employee:
            raise EmployeeExtendedDataError(f"Employee {employee_id} not found")
        return employee

    @staticmethod
    def _validate_payload(
        *,
        certification_name: str,
        issuing_authority: str,
        issue_date: date,
        expiry_date: date | None = None,
        does_not_expire: bool = False,
        credential_id: str | None = None,
        credential_url: str | None = None,
        notes: str | None = None,
    ) -> None:
        certification_name_clean = _clean_text(certification_name)
        issuing_authority_clean = _clean_text(issuing_authority)
        if (
            not certification_name_clean
            or not issuing_authority_clean
            or not issue_date
        ):
            raise EmployeeExtendedDataError(
                "Certification name, issuing authority, and issue date are required"
            )
        _require_length(certification_name_clean, "certification_name", 255)
        _require_length(issuing_authority_clean, "issuing_authority", 255)
        _require_length(_clean_text(credential_id), "credential_id", 100)
        _require_length(_clean_text(notes), "notes", 5000)
        if does_not_expire:
            return
        if expiry_date and expiry_date < issue_date:
            raise EmployeeExtendedDataError(
                "Certification expiry date cannot be before issue date"
            )
        credential_url = _clean_text(credential_url)
        if credential_url:
            parsed = urlparse(credential_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise EmployeeExtendedDataError(
                    "Credential URL must be a valid http(s) URL"
                )

    def list_certifications(
        self,
        employee_id: uuid.UUID,
        include_expired: bool = True,
    ) -> list[EmployeeCertification]:
        """List certifications for an employee."""
        query = select(EmployeeCertification).where(
            EmployeeCertification.organization_id == self.organization_id,
            EmployeeCertification.employee_id == employee_id,
            EmployeeCertification.is_active.is_(True),
        )

        if not include_expired:
            query = query.where(
                (EmployeeCertification.does_not_expire == True)
                | (EmployeeCertification.expiry_date == None)
                | (EmployeeCertification.expiry_date >= date.today())
            )

        query = query.order_by(EmployeeCertification.issue_date.desc())
        return list(self.db.scalars(query).all())

    def get_certification(
        self,
        certification_id: uuid.UUID,
    ) -> EmployeeCertification:
        """Get a certification by ID."""
        cert = self.db.scalar(
            select(EmployeeCertification).where(
                EmployeeCertification.certification_id == certification_id,
                EmployeeCertification.organization_id == self.organization_id,
                EmployeeCertification.is_active.is_(True),
            )
        )
        if not cert:
            raise CertificationNotFoundError(
                f"Certification {certification_id} not found"
            )
        return cert

    def create_certification(
        self,
        employee_id: uuid.UUID,
        certification_name: str,
        issuing_authority: str,
        issue_date: date,
        expiry_date: date | None = None,
        does_not_expire: bool = False,
        credential_id: str | None = None,
        credential_url: str | None = None,
        renewal_reminder_days: int = 30,
        document_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> EmployeeCertification:
        """Create a new certification record."""
        self._get_employee(employee_id)
        self._validate_payload(
            certification_name=certification_name,
            issuing_authority=issuing_authority,
            issue_date=issue_date,
            expiry_date=expiry_date,
            does_not_expire=does_not_expire,
            credential_id=credential_id,
            credential_url=credential_url,
            notes=notes,
        )
        cert = EmployeeCertification(
            organization_id=self.organization_id,
            employee_id=employee_id,
            certification_name=certification_name,
            issuing_authority=issuing_authority,
            issue_date=issue_date,
            expiry_date=expiry_date,
            does_not_expire=does_not_expire,
            credential_id=credential_id,
            credential_url=credential_url,
            renewal_reminder_days=renewal_reminder_days,
            document_id=document_id,
            notes=notes,
        )
        self.db.add(cert)
        self.db.flush()
        return cert

    def update_certification(
        self,
        certification_id: uuid.UUID,
        **kwargs,
    ) -> EmployeeCertification:
        """Update a certification."""
        cert = self.get_certification(certification_id)
        self._validate_payload(
            certification_name=kwargs.get(
                "certification_name",
                cert.certification_name,
            ),
            issuing_authority=kwargs.get(
                "issuing_authority",
                cert.issuing_authority,
            ),
            issue_date=kwargs.get("issue_date", cert.issue_date),
            expiry_date=kwargs.get("expiry_date", cert.expiry_date),
            does_not_expire=kwargs.get(
                "does_not_expire",
                cert.does_not_expire,
            ),
            credential_id=kwargs.get("credential_id", cert.credential_id),
            credential_url=kwargs.get("credential_url", cert.credential_url),
            notes=kwargs.get("notes", cert.notes),
        )
        allowed_fields = {
            "certification_name",
            "issuing_authority",
            "issue_date",
            "expiry_date",
            "does_not_expire",
            "credential_id",
            "credential_url",
            "renewal_reminder_days",
            "document_id",
            "notes",
        }
        for key, value in kwargs.items():
            if key in allowed_fields and value is not None:
                setattr(cert, key, value)
        self.db.flush()
        return cert

    def verify_certification(
        self,
        certification_id: uuid.UUID,
    ) -> EmployeeCertification:
        """Mark certification as verified."""
        cert = self.get_certification(certification_id)
        cert.is_verified = True
        self.db.flush()
        return cert

    def delete_certification(self, certification_id: uuid.UUID) -> None:
        """Soft delete a certification."""
        cert = self.get_certification(certification_id)
        cert.is_active = False
        self.db.flush()

    def get_expiring_certifications(
        self,
        days_until_expiry: int = 30,
    ) -> list[EmployeeCertification]:
        """Get certifications expiring within specified days."""
        from datetime import timedelta

        cutoff = date.today()
        end_date = cutoff + timedelta(days=days_until_expiry)

        query = (
            select(EmployeeCertification)
            .where(
                EmployeeCertification.organization_id == self.organization_id,
                EmployeeCertification.is_active.is_(True),
                EmployeeCertification.does_not_expire == False,
                EmployeeCertification.expiry_date != None,
                EmployeeCertification.expiry_date >= cutoff,
                EmployeeCertification.expiry_date <= end_date,
            )
            .options(selectinload(EmployeeCertification.employee))
        )

        return list(self.db.scalars(query).all())

    def get_certifications_needing_reminder(self) -> list[EmployeeCertification]:
        """Get certifications that need renewal reminders."""
        query = (
            select(EmployeeCertification)
            .where(
                EmployeeCertification.organization_id == self.organization_id,
                EmployeeCertification.is_active.is_(True),
                EmployeeCertification.does_not_expire == False,
                EmployeeCertification.expiry_date != None,
                EmployeeCertification.expiry_date >= date.today(),
            )
            .options(selectinload(EmployeeCertification.employee))
        )

        certs = list(self.db.scalars(query).all())
        return [c for c in certs if c.needs_renewal_reminder]


# =============================================================================
# EmployeeDependentService
# =============================================================================


class EmployeeDependentService:
    """Service for managing employee dependents."""

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        principal: Principal | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    def _get_employee(self, employee_id: uuid.UUID) -> Employee:
        employee = self.db.scalar(
            select(Employee).where(
                Employee.employee_id == employee_id,
                Employee.organization_id == self.organization_id,
                Employee.status != EmployeeStatus.TERMINATED,
            )
        )
        if not employee:
            raise EmployeeExtendedDataError(f"Employee {employee_id} not found")
        return employee

    @staticmethod
    def _validate_payload(
        *,
        full_name: str,
        relationship: RelationshipType,
        email: str | None = None,
        emergency_contact_priority: int | None = None,
        beneficiary_percentage: float | None = None,
        notes: str | None = None,
    ) -> None:
        del relationship
        full_name_clean = _clean_text(full_name)
        if not full_name_clean:
            raise EmployeeExtendedDataError("Full name is required")
        _require_length(full_name_clean, "full_name", 200)
        _require_length(_clean_text(email), "email", 255)
        _require_length(_clean_text(notes), "notes", 5000)
        if email and "@" not in email:
            raise EmployeeExtendedDataError("Email must be valid")
        if emergency_contact_priority is not None and emergency_contact_priority < 1:
            raise EmployeeExtendedDataError(
                "Emergency contact priority must be at least 1"
            )
        if beneficiary_percentage is not None and not (
            0 <= beneficiary_percentage <= 100
        ):
            raise EmployeeExtendedDataError(
                "Beneficiary percentage must be between 0 and 100"
            )

    def list_dependents(
        self,
        employee_id: uuid.UUID,
        relationship: RelationshipType | None = None,
        emergency_contacts_only: bool = False,
        beneficiaries_only: bool = False,
    ) -> list[EmployeeDependent]:
        """List dependents for an employee."""
        query = select(EmployeeDependent).where(
            EmployeeDependent.organization_id == self.organization_id,
            EmployeeDependent.employee_id == employee_id,
            EmployeeDependent.is_active.is_(True),
        )

        if relationship:
            query = query.where(EmployeeDependent.relation_type == relationship)
        if emergency_contacts_only:
            query = query.where(EmployeeDependent.is_emergency_contact == True)
        if beneficiaries_only:
            query = query.where(EmployeeDependent.is_beneficiary == True)

        query = query.order_by(
            EmployeeDependent.is_emergency_contact.desc(),
            EmployeeDependent.emergency_contact_priority.asc().nullslast(),
            EmployeeDependent.full_name,
        )
        return list(self.db.scalars(query).all())

    def get_dependent(
        self,
        dependent_id: uuid.UUID,
    ) -> EmployeeDependent:
        """Get a dependent by ID."""
        dep = self.db.scalar(
            select(EmployeeDependent).where(
                EmployeeDependent.dependent_id == dependent_id,
                EmployeeDependent.organization_id == self.organization_id,
                EmployeeDependent.is_active.is_(True),
            )
        )
        if not dep:
            raise DependentNotFoundError(f"Dependent {dependent_id} not found")
        return dep

    def create_dependent(
        self,
        employee_id: uuid.UUID,
        full_name: str,
        relationship: RelationshipType,
        date_of_birth: date | None = None,
        gender: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        address: str | None = None,
        is_emergency_contact: bool = False,
        emergency_contact_priority: int | None = None,
        is_beneficiary: bool = False,
        beneficiary_percentage: float | None = None,
        is_covered_under_insurance: bool = False,
        insurance_id: str | None = None,
        notes: str | None = None,
    ) -> EmployeeDependent:
        """Create a new dependent record."""
        self._get_employee(employee_id)
        self._validate_payload(
            full_name=full_name,
            relationship=relationship,
            email=email,
            emergency_contact_priority=emergency_contact_priority,
            beneficiary_percentage=beneficiary_percentage,
            notes=notes,
        )
        dep = EmployeeDependent(
            organization_id=self.organization_id,
            employee_id=employee_id,
            full_name=full_name,
            relationship=relationship,
            date_of_birth=date_of_birth,
            gender=DependentGender(gender) if gender else None,
            phone=phone,
            email=email,
            address=address,
            is_emergency_contact=is_emergency_contact,
            emergency_contact_priority=emergency_contact_priority,
            is_beneficiary=is_beneficiary,
            beneficiary_percentage=beneficiary_percentage,
            is_covered_under_insurance=is_covered_under_insurance,
            insurance_id=insurance_id,
            notes=notes,
        )
        self.db.add(dep)
        self.db.flush()
        return dep

    def update_dependent(
        self,
        dependent_id: uuid.UUID,
        **kwargs,
    ) -> EmployeeDependent:
        """Update a dependent."""
        dep = self.get_dependent(dependent_id)
        relationship = kwargs.get("relationship", dep.relation_type)
        if isinstance(relationship, str):
            relationship = RelationshipType(relationship)
        self._validate_payload(
            full_name=kwargs.get("full_name", dep.full_name),
            relationship=relationship,
            email=kwargs.get("email", dep.email),
            emergency_contact_priority=kwargs.get(
                "emergency_contact_priority",
                dep.emergency_contact_priority,
            ),
            beneficiary_percentage=kwargs.get(
                "beneficiary_percentage",
                float(dep.beneficiary_percentage)
                if dep.beneficiary_percentage is not None
                else None,
            ),
            notes=kwargs.get("notes", dep.notes),
        )
        allowed_fields = {
            "full_name",
            "relationship",
            "date_of_birth",
            "gender",
            "phone",
            "email",
            "address",
            "is_emergency_contact",
            "emergency_contact_priority",
            "is_beneficiary",
            "beneficiary_percentage",
            "is_covered_under_insurance",
            "insurance_id",
            "notes",
        }
        for key, value in kwargs.items():
            if key in allowed_fields and value is not None:
                if key == "relationship" and isinstance(value, str):
                    value = RelationshipType(value)
                if key == "gender" and isinstance(value, str):
                    value = DependentGender(value)
                setattr(dep, key, value)
        self.db.flush()
        return dep

    def delete_dependent(self, dependent_id: uuid.UUID) -> None:
        """Soft delete a dependent."""
        dep = self.get_dependent(dependent_id)
        dep.is_active = False
        self.db.flush()

    def get_emergency_contacts(
        self,
        employee_id: uuid.UUID,
    ) -> list[EmployeeDependent]:
        """Get emergency contacts for an employee, ordered by priority."""
        return self.list_dependents(
            employee_id=employee_id,
            emergency_contacts_only=True,
        )


# =============================================================================
# SkillService (Catalog)
# =============================================================================


class SkillService:
    """Service for managing the skill catalog."""

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        principal: Principal | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    def list_skills(
        self,
        category: SkillCategory | None = None,
        search: str | None = None,
        active_only: bool = True,
    ) -> list[Skill]:
        """List skills in the catalog."""
        query = select(Skill).where(
            Skill.organization_id == self.organization_id,
            Skill.is_active.is_(True),
        )

        if category:
            query = query.where(Skill.category == category)
        if active_only:
            query = query.where(Skill.is_active == True)
        if search:
            query = query.where(Skill.skill_name.ilike(f"%{search}%"))

        query = query.order_by(Skill.category, Skill.skill_name)
        return list(self.db.scalars(query).all())

    def get_skill(self, skill_id: uuid.UUID) -> Skill:
        """Get a skill by ID."""
        skill = self.db.scalar(
            select(Skill).where(
                Skill.skill_id == skill_id,
                Skill.organization_id == self.organization_id,
                Skill.is_active.is_(True),
            )
        )
        if not skill:
            raise SkillNotFoundError(f"Skill {skill_id} not found")
        return skill

    def create_skill(
        self,
        skill_name: str,
        category: SkillCategory,
        description: str | None = None,
        is_language: bool = False,
    ) -> Skill:
        """Create a new skill in the catalog."""
        skill = Skill(
            organization_id=self.organization_id,
            skill_name=skill_name,
            category=category,
            description=description,
            is_language=is_language,
        )
        self.db.add(skill)
        self.db.flush()
        return skill

    def update_skill(
        self,
        skill_id: uuid.UUID,
        skill_name: str | None = None,
        category: SkillCategory | None = None,
        description: str | None = None,
        is_active: bool | None = None,
    ) -> Skill:
        """Update a skill."""
        skill = self.get_skill(skill_id)
        if skill_name is not None:
            skill.skill_name = skill_name
        if category is not None:
            skill.category = category
        if description is not None:
            skill.description = description
        if is_active is not None:
            skill.is_active = is_active
        self.db.flush()
        return skill

    def delete_skill(self, skill_id: uuid.UUID) -> None:
        """Soft delete a skill."""
        skill = self.get_skill(skill_id)
        skill.is_active = False
        self.db.flush()


# =============================================================================
# EmployeeSkillService
# =============================================================================


class EmployeeSkillService:
    """Service for managing employee skills."""

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        principal: Principal | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    def _get_employee(self, employee_id: uuid.UUID) -> Employee:
        employee = self.db.scalar(
            select(Employee).where(
                Employee.employee_id == employee_id,
                Employee.organization_id == self.organization_id,
                Employee.status != EmployeeStatus.TERMINATED,
            )
        )
        if not employee:
            raise EmployeeExtendedDataError(f"Employee {employee_id} not found")
        return employee

    def _get_skill(self, skill_id: uuid.UUID) -> Skill:
        skill = self.db.scalar(
            select(Skill).where(
                Skill.skill_id == skill_id,
                Skill.organization_id == self.organization_id,
                Skill.is_active.is_(True),
            )
        )
        if not skill:
            raise SkillNotFoundError(f"Skill {skill_id} not found")
        return skill

    def _ensure_unique_skill(
        self,
        employee_id: uuid.UUID,
        skill_id: uuid.UUID,
        *,
        exclude_employee_skill_id: uuid.UUID | None = None,
    ) -> None:
        query = select(func.count(EmployeeSkill.employee_skill_id)).where(
            EmployeeSkill.organization_id == self.organization_id,
            EmployeeSkill.employee_id == employee_id,
            EmployeeSkill.skill_id == skill_id,
        )
        if exclude_employee_skill_id:
            query = query.where(
                EmployeeSkill.employee_skill_id != exclude_employee_skill_id
            )
        if (self.db.scalar(query) or 0) > 0:
            raise EmployeeExtendedDataError("This skill is already assigned")

    def list_employee_skills(
        self,
        employee_id: uuid.UUID,
        category: SkillCategory | None = None,
        primary_only: bool = False,
        min_proficiency: int | None = None,
    ) -> list[EmployeeSkill]:
        """List skills for an employee."""
        query = (
            select(EmployeeSkill)
            .where(
                EmployeeSkill.organization_id == self.organization_id,
                EmployeeSkill.employee_id == employee_id,
            )
            .options(selectinload(EmployeeSkill.skill))
        )

        if primary_only:
            query = query.where(EmployeeSkill.is_primary == True)
        if min_proficiency:
            query = query.where(EmployeeSkill.proficiency_level >= min_proficiency)
        if category:
            query = query.join(Skill).where(Skill.category == category)

        query = query.order_by(
            EmployeeSkill.is_primary.desc(),
            EmployeeSkill.proficiency_level.desc(),
        )
        return list(self.db.scalars(query).all())

    def get_employee_skill(
        self,
        employee_skill_id: uuid.UUID,
    ) -> EmployeeSkill:
        """Get an employee skill by ID."""
        emp_skill = self.db.scalar(
            select(EmployeeSkill)
            .where(
                EmployeeSkill.employee_skill_id == employee_skill_id,
                EmployeeSkill.organization_id == self.organization_id,
            )
            .options(selectinload(EmployeeSkill.skill))
        )
        if not emp_skill:
            raise EmployeeSkillNotFoundError(
                f"Employee skill {employee_skill_id} not found"
            )
        return emp_skill

    def add_skill(
        self,
        employee_id: uuid.UUID,
        skill_id: uuid.UUID,
        proficiency_level: int,
        years_experience: float | None = None,
        last_used_date: date | None = None,
        is_primary: bool = False,
        is_certified: bool = False,
        is_self_assessed: bool = True,
        assessed_by_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> EmployeeSkill:
        """Add a skill to an employee."""
        # Validate proficiency level
        if not 1 <= proficiency_level <= 5:
            raise ValueError("Proficiency level must be between 1 and 5")
        if years_experience is not None and years_experience < 0:
            raise EmployeeExtendedDataError("Years of experience cannot be negative")
        self._get_employee(employee_id)
        self._get_skill(skill_id)
        self._ensure_unique_skill(employee_id, skill_id)

        emp_skill = EmployeeSkill(
            organization_id=self.organization_id,
            employee_id=employee_id,
            skill_id=skill_id,
            proficiency_level=proficiency_level,
            years_experience=Decimal(str(years_experience))
            if years_experience is not None
            else None,
            last_used_date=last_used_date,
            is_primary=is_primary,
            is_certified=is_certified,
            is_self_assessed=is_self_assessed,
            assessed_by_id=assessed_by_id,
            assessed_at=datetime.now(UTC) if assessed_by_id else None,
            notes=notes,
        )
        self.db.add(emp_skill)
        self.db.flush()
        return emp_skill

    def update_employee_skill(
        self,
        employee_skill_id: uuid.UUID,
        skill_id: uuid.UUID | None = None,
        proficiency_level: int | None = None,
        years_experience: float | None = None,
        last_used_date: date | None = None,
        is_primary: bool | None = None,
        is_certified: bool | None = None,
        notes: str | None = None,
    ) -> EmployeeSkill:
        """Update an employee skill."""
        emp_skill = self.get_employee_skill(employee_skill_id)
        target_skill_id = skill_id or emp_skill.skill_id

        self._get_skill(target_skill_id)
        if proficiency_level is not None:
            if not 1 <= proficiency_level <= 5:
                raise ValueError("Proficiency level must be between 1 and 5")
            emp_skill.proficiency_level = proficiency_level
        if years_experience is not None and years_experience < 0:
            raise EmployeeExtendedDataError("Years of experience cannot be negative")
        self._ensure_unique_skill(
            emp_skill.employee_id,
            target_skill_id,
            exclude_employee_skill_id=employee_skill_id,
        )
        if skill_id is not None:
            emp_skill.skill_id = target_skill_id
        if years_experience is not None:
            emp_skill.years_experience = Decimal(str(years_experience))
        if last_used_date is not None:
            emp_skill.last_used_date = last_used_date
        if is_primary is not None:
            emp_skill.is_primary = is_primary
        if is_certified is not None:
            emp_skill.is_certified = is_certified
        if notes is not None:
            emp_skill.notes = notes

        self.db.flush()
        return emp_skill

    def assess_skill(
        self,
        employee_skill_id: uuid.UUID,
        assessed_by_id: uuid.UUID,
        proficiency_level: int,
        notes: str | None = None,
    ) -> EmployeeSkill:
        """Record a skill assessment by another person."""
        emp_skill = self.get_employee_skill(employee_skill_id)
        emp_skill.proficiency_level = proficiency_level
        emp_skill.is_self_assessed = False
        emp_skill.assessed_by_id = assessed_by_id
        emp_skill.assessed_at = datetime.now(UTC)
        if notes:
            emp_skill.notes = notes
        self.db.flush()
        return emp_skill

    def remove_skill(self, employee_skill_id: uuid.UUID) -> None:
        """Remove a skill from an employee (hard delete)."""
        emp_skill = self.get_employee_skill(employee_skill_id)
        self.db.delete(emp_skill)
        self.db.flush()

    def get_employees_with_skill(
        self,
        skill_id: uuid.UUID,
        min_proficiency: int = 1,
    ) -> list[EmployeeSkill]:
        """Find employees with a specific skill."""
        query = (
            select(EmployeeSkill)
            .where(
                EmployeeSkill.organization_id == self.organization_id,
                EmployeeSkill.skill_id == skill_id,
                EmployeeSkill.proficiency_level >= min_proficiency,
            )
            .options(selectinload(EmployeeSkill.employee))
            .order_by(EmployeeSkill.proficiency_level.desc())
        )
        return list(self.db.scalars(query).all())


class EmployeeExtendedSelfServiceService:
    """Ownership-enforcing wrappers for employee self-service extended profile access."""

    def __init__(self, db: Session, organization_id: uuid.UUID) -> None:
        self.db = db
        self.organization_id = organization_id
        self.qualification_service = EmployeeQualificationService(db, organization_id)
        self.certification_service = EmployeeCertificationService(db, organization_id)
        self.dependent_service = EmployeeDependentService(db, organization_id)
        self.skill_service = EmployeeSkillService(db, organization_id)
        self.catalog_service = SkillService(db, organization_id)
        self.document_service = EmployeeDocumentService(db, organization_id)

    def get_employee_for_person(self, person_id: uuid.UUID) -> Employee:
        employee = self.db.scalar(
            select(Employee).where(
                Employee.organization_id == self.organization_id,
                Employee.person_id == person_id,
                Employee.status != EmployeeStatus.TERMINATED,
            )
        )
        if not employee:
            raise EmployeeExtendedDataError("Employee profile not found")
        return employee

    def list_profile(self, employee_id: uuid.UUID) -> dict[str, list[Any]]:
        return {
            "qualifications": self.qualification_service.list_qualifications(
                employee_id
            ),
            "certifications": self.certification_service.list_certifications(
                employee_id
            ),
            "dependents": self.dependent_service.list_dependents(employee_id),
            "skills": self.skill_service.list_employee_skills(employee_id),
            "skill_catalog": self.catalog_service.list_skills(active_only=True),
        }

    def get_owned_qualification(
        self,
        employee_id: uuid.UUID,
        qualification_id: uuid.UUID,
    ) -> EmployeeQualification:
        qualification = self.qualification_service.get_qualification(qualification_id)
        if qualification.employee_id != employee_id:
            raise QualificationNotFoundError(
                f"Qualification {qualification_id} not found"
            )
        return qualification

    def get_owned_certification(
        self,
        employee_id: uuid.UUID,
        certification_id: uuid.UUID,
    ) -> EmployeeCertification:
        certification = self.certification_service.get_certification(certification_id)
        if certification.employee_id != employee_id:
            raise CertificationNotFoundError(
                f"Certification {certification_id} not found"
            )
        return certification

    def get_owned_dependent(
        self,
        employee_id: uuid.UUID,
        dependent_id: uuid.UUID,
    ) -> EmployeeDependent:
        dependent = self.dependent_service.get_dependent(dependent_id)
        if dependent.employee_id != employee_id:
            raise DependentNotFoundError(f"Dependent {dependent_id} not found")
        return dependent

    def get_owned_employee_skill(
        self,
        employee_id: uuid.UUID,
        employee_skill_id: uuid.UUID,
    ) -> EmployeeSkill:
        employee_skill = self.skill_service.get_employee_skill(employee_skill_id)
        if employee_skill.employee_id != employee_id:
            raise EmployeeSkillNotFoundError(
                f"Employee skill {employee_skill_id} not found"
            )
        return employee_skill

    def get_owned_document(
        self,
        employee_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> EmployeeDocument:
        document = self.document_service.get_document(document_id)
        if document.employee_id != employee_id:
            raise DocumentNotFoundError(f"Document {document_id} not found")
        return document
