"""Employee Extended Data Services.

Services for managing employee documents, qualifications, certifications,
dependents, and skills.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session, selectinload

from app.models.people.hr import (
    Employee,
    EmployeeDocument,
    EmployeeQualification,
    EmployeeCertification,
    EmployeeDependent,
    EmployeeSkill,
    Skill,
    DocumentType,
    QualificationType,
    RelationshipType,
    SkillCategory,
)
from app.services.common import PaginatedResult, PaginationParams, paginate

if TYPE_CHECKING:
    from app.auth import Principal

__all__ = [
    "EmployeeDocumentService",
    "EmployeeQualificationService",
    "EmployeeCertificationService",
    "EmployeeDependentService",
    "SkillService",
    "EmployeeSkillService",
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


# =============================================================================
# EmployeeDocumentService
# =============================================================================


class EmployeeDocumentService:
    """Service for managing employee documents."""

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        principal: Optional["Principal"] = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    def list_documents(
        self,
        employee_id: uuid.UUID,
        document_type: Optional[DocumentType] = None,
        is_verified: Optional[bool] = None,
        include_expired: bool = True,
    ) -> List[EmployeeDocument]:
        """List documents for an employee."""
        query = select(EmployeeDocument).where(
            EmployeeDocument.organization_id == self.organization_id,
            EmployeeDocument.employee_id == employee_id,
            EmployeeDocument.is_deleted == False,
        )

        if document_type:
            query = query.where(EmployeeDocument.document_type == document_type)
        if is_verified is not None:
            query = query.where(EmployeeDocument.is_verified == is_verified)
        if not include_expired:
            query = query.where(
                (EmployeeDocument.expiry_date == None) |
                (EmployeeDocument.expiry_date >= date.today())
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
                EmployeeDocument.is_deleted == False,
            )
        )
        if not doc:
            raise DocumentNotFoundError(f"Document {document_id} not found")
        return doc

    def create_document(
        self,
        employee_id: uuid.UUID,
        document_type: DocumentType,
        document_name: str,
        file_path: str,
        file_name: str,
        file_size: Optional[int] = None,
        mime_type: Optional[str] = None,
        description: Optional[str] = None,
        issue_date: Optional[date] = None,
        expiry_date: Optional[date] = None,
    ) -> EmployeeDocument:
        """Create a new document record."""
        doc = EmployeeDocument(
            organization_id=self.organization_id,
            employee_id=employee_id,
            document_type=document_type,
            document_name=document_name,
            file_path=file_path,
            file_name=file_name,
            file_size=file_size,
            mime_type=mime_type,
            description=description,
            issue_date=issue_date,
            expiry_date=expiry_date,
        )
        self.db.add(doc)
        self.db.flush()
        return doc

    def update_document(
        self,
        document_id: uuid.UUID,
        document_name: Optional[str] = None,
        description: Optional[str] = None,
        issue_date: Optional[date] = None,
        expiry_date: Optional[date] = None,
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

    def verify_document(
        self,
        document_id: uuid.UUID,
        verified_by_id: uuid.UUID,
        notes: Optional[str] = None,
    ) -> EmployeeDocument:
        """Mark document as verified."""
        doc = self.get_document(document_id)
        doc.is_verified = True
        doc.verified_by_id = verified_by_id
        doc.verified_at = datetime.now(timezone.utc)
        doc.verification_notes = notes
        self.db.flush()
        return doc

    def delete_document(self, document_id: uuid.UUID) -> None:
        """Soft delete a document."""
        doc = self.get_document(document_id)
        doc.is_deleted = True
        doc.deleted_at = datetime.now(timezone.utc)
        self.db.flush()

    def get_expiring_documents(
        self,
        days_until_expiry: int = 30,
    ) -> List[EmployeeDocument]:
        """Get documents expiring within specified days."""
        cutoff = date.today()
        end_date = date.today()
        from datetime import timedelta
        end_date = cutoff + timedelta(days=days_until_expiry)

        query = select(EmployeeDocument).where(
            EmployeeDocument.organization_id == self.organization_id,
            EmployeeDocument.is_deleted == False,
            EmployeeDocument.expiry_date != None,
            EmployeeDocument.expiry_date >= cutoff,
            EmployeeDocument.expiry_date <= end_date,
        ).options(selectinload(EmployeeDocument.employee))

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
        principal: Optional["Principal"] = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    def list_qualifications(
        self,
        employee_id: uuid.UUID,
        qualification_type: Optional[QualificationType] = None,
    ) -> List[EmployeeQualification]:
        """List qualifications for an employee."""
        query = select(EmployeeQualification).where(
            EmployeeQualification.organization_id == self.organization_id,
            EmployeeQualification.employee_id == employee_id,
            EmployeeQualification.is_deleted == False,
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
                EmployeeQualification.is_deleted == False,
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
        field_of_study: Optional[str] = None,
        institution_location: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        is_ongoing: bool = False,
        grade: Optional[str] = None,
        score: Optional[float] = None,
        max_score: Optional[float] = None,
        document_id: Optional[uuid.UUID] = None,
        notes: Optional[str] = None,
    ) -> EmployeeQualification:
        """Create a new qualification record."""
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
        allowed_fields = {
            "qualification_type", "qualification_name", "institution_name",
            "field_of_study", "institution_location", "start_date", "end_date",
            "is_ongoing", "grade", "score", "max_score", "document_id", "notes",
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
        qual.is_deleted = True
        qual.deleted_at = datetime.now(timezone.utc)
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
        principal: Optional["Principal"] = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    def list_certifications(
        self,
        employee_id: uuid.UUID,
        include_expired: bool = True,
    ) -> List[EmployeeCertification]:
        """List certifications for an employee."""
        query = select(EmployeeCertification).where(
            EmployeeCertification.organization_id == self.organization_id,
            EmployeeCertification.employee_id == employee_id,
            EmployeeCertification.is_deleted == False,
        )

        if not include_expired:
            query = query.where(
                (EmployeeCertification.does_not_expire == True) |
                (EmployeeCertification.expiry_date == None) |
                (EmployeeCertification.expiry_date >= date.today())
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
                EmployeeCertification.is_deleted == False,
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
        expiry_date: Optional[date] = None,
        does_not_expire: bool = False,
        credential_id: Optional[str] = None,
        credential_url: Optional[str] = None,
        renewal_reminder_days: int = 30,
        document_id: Optional[uuid.UUID] = None,
        notes: Optional[str] = None,
    ) -> EmployeeCertification:
        """Create a new certification record."""
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
        allowed_fields = {
            "certification_name", "issuing_authority", "issue_date",
            "expiry_date", "does_not_expire", "credential_id", "credential_url",
            "renewal_reminder_days", "document_id", "notes",
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
        cert.is_deleted = True
        cert.deleted_at = datetime.now(timezone.utc)
        self.db.flush()

    def get_expiring_certifications(
        self,
        days_until_expiry: int = 30,
    ) -> List[EmployeeCertification]:
        """Get certifications expiring within specified days."""
        from datetime import timedelta
        cutoff = date.today()
        end_date = cutoff + timedelta(days=days_until_expiry)

        query = select(EmployeeCertification).where(
            EmployeeCertification.organization_id == self.organization_id,
            EmployeeCertification.is_deleted == False,
            EmployeeCertification.does_not_expire == False,
            EmployeeCertification.expiry_date != None,
            EmployeeCertification.expiry_date >= cutoff,
            EmployeeCertification.expiry_date <= end_date,
        ).options(selectinload(EmployeeCertification.employee))

        return list(self.db.scalars(query).all())

    def get_certifications_needing_reminder(self) -> List[EmployeeCertification]:
        """Get certifications that need renewal reminders."""
        query = select(EmployeeCertification).where(
            EmployeeCertification.organization_id == self.organization_id,
            EmployeeCertification.is_deleted == False,
            EmployeeCertification.does_not_expire == False,
            EmployeeCertification.expiry_date != None,
            EmployeeCertification.expiry_date >= date.today(),
        ).options(selectinload(EmployeeCertification.employee))

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
        principal: Optional["Principal"] = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    def list_dependents(
        self,
        employee_id: uuid.UUID,
        relationship: Optional[RelationshipType] = None,
        emergency_contacts_only: bool = False,
        beneficiaries_only: bool = False,
    ) -> List[EmployeeDependent]:
        """List dependents for an employee."""
        query = select(EmployeeDependent).where(
            EmployeeDependent.organization_id == self.organization_id,
            EmployeeDependent.employee_id == employee_id,
            EmployeeDependent.is_deleted == False,
        )

        if relationship:
            query = query.where(EmployeeDependent.relationship == relationship)
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
                EmployeeDependent.is_deleted == False,
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
        date_of_birth: Optional[date] = None,
        gender: Optional[str] = None,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        address: Optional[str] = None,
        is_emergency_contact: bool = False,
        emergency_contact_priority: Optional[int] = None,
        is_beneficiary: bool = False,
        beneficiary_percentage: Optional[float] = None,
        is_covered_under_insurance: bool = False,
        insurance_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> EmployeeDependent:
        """Create a new dependent record."""
        from app.models.people.hr.employee_extended import Gender as DepGender

        dep = EmployeeDependent(
            organization_id=self.organization_id,
            employee_id=employee_id,
            full_name=full_name,
            relationship=relationship,
            date_of_birth=date_of_birth,
            gender=DepGender(gender) if gender else None,
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
        allowed_fields = {
            "full_name", "relationship", "date_of_birth", "gender",
            "phone", "email", "address", "is_emergency_contact",
            "emergency_contact_priority", "is_beneficiary",
            "beneficiary_percentage", "is_covered_under_insurance",
            "insurance_id", "notes",
        }
        for key, value in kwargs.items():
            if key in allowed_fields and value is not None:
                setattr(dep, key, value)
        self.db.flush()
        return dep

    def delete_dependent(self, dependent_id: uuid.UUID) -> None:
        """Soft delete a dependent."""
        dep = self.get_dependent(dependent_id)
        dep.is_deleted = True
        dep.deleted_at = datetime.now(timezone.utc)
        self.db.flush()

    def get_emergency_contacts(
        self,
        employee_id: uuid.UUID,
    ) -> List[EmployeeDependent]:
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
        principal: Optional["Principal"] = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    def list_skills(
        self,
        category: Optional[SkillCategory] = None,
        search: Optional[str] = None,
        active_only: bool = True,
    ) -> List[Skill]:
        """List skills in the catalog."""
        query = select(Skill).where(
            Skill.organization_id == self.organization_id,
            Skill.is_deleted == False,
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
                Skill.is_deleted == False,
            )
        )
        if not skill:
            raise SkillNotFoundError(f"Skill {skill_id} not found")
        return skill

    def create_skill(
        self,
        skill_name: str,
        category: SkillCategory,
        description: Optional[str] = None,
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
        skill_name: Optional[str] = None,
        category: Optional[SkillCategory] = None,
        description: Optional[str] = None,
        is_active: Optional[bool] = None,
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
        skill.is_deleted = True
        skill.deleted_at = datetime.now(timezone.utc)
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
        principal: Optional["Principal"] = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    def list_employee_skills(
        self,
        employee_id: uuid.UUID,
        category: Optional[SkillCategory] = None,
        primary_only: bool = False,
        min_proficiency: Optional[int] = None,
    ) -> List[EmployeeSkill]:
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
        years_experience: Optional[float] = None,
        last_used_date: Optional[date] = None,
        is_primary: bool = False,
        is_certified: bool = False,
        is_self_assessed: bool = True,
        assessed_by_id: Optional[uuid.UUID] = None,
        notes: Optional[str] = None,
    ) -> EmployeeSkill:
        """Add a skill to an employee."""
        # Validate proficiency level
        if not 1 <= proficiency_level <= 5:
            raise ValueError("Proficiency level must be between 1 and 5")

        emp_skill = EmployeeSkill(
            organization_id=self.organization_id,
            employee_id=employee_id,
            skill_id=skill_id,
            proficiency_level=proficiency_level,
            years_experience=years_experience,
            last_used_date=last_used_date,
            is_primary=is_primary,
            is_certified=is_certified,
            is_self_assessed=is_self_assessed,
            assessed_by_id=assessed_by_id,
            assessed_at=datetime.now(timezone.utc) if assessed_by_id else None,
            notes=notes,
        )
        self.db.add(emp_skill)
        self.db.flush()
        return emp_skill

    def update_employee_skill(
        self,
        employee_skill_id: uuid.UUID,
        proficiency_level: Optional[int] = None,
        years_experience: Optional[float] = None,
        last_used_date: Optional[date] = None,
        is_primary: Optional[bool] = None,
        is_certified: Optional[bool] = None,
        notes: Optional[str] = None,
    ) -> EmployeeSkill:
        """Update an employee skill."""
        emp_skill = self.get_employee_skill(employee_skill_id)

        if proficiency_level is not None:
            if not 1 <= proficiency_level <= 5:
                raise ValueError("Proficiency level must be between 1 and 5")
            emp_skill.proficiency_level = proficiency_level
        if years_experience is not None:
            emp_skill.years_experience = years_experience
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
        notes: Optional[str] = None,
    ) -> EmployeeSkill:
        """Record a skill assessment by another person."""
        emp_skill = self.get_employee_skill(employee_skill_id)
        emp_skill.proficiency_level = proficiency_level
        emp_skill.is_self_assessed = False
        emp_skill.assessed_by_id = assessed_by_id
        emp_skill.assessed_at = datetime.now(timezone.utc)
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
    ) -> List[EmployeeSkill]:
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
