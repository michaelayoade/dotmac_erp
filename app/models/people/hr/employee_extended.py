"""
Employee Extended Data Models - HR Schema.

Additional employee information models:
- EmployeeDocument: Uploaded documents (contracts, certificates, IDs)
- EmployeeQualification: Educational qualifications
- EmployeeCertification: Professional certifications and licenses
- EmployeeDependent: Family members and beneficiaries
- Skill: Skill catalog
- EmployeeSkill: Employee skill proficiency tracking
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, SoftDeleteMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee


# =============================================================================
# Enums
# =============================================================================


class DocumentType(str, enum.Enum):
    """Types of employee documents."""
    CONTRACT = "CONTRACT"
    OFFER_LETTER = "OFFER_LETTER"
    ID_PROOF = "ID_PROOF"
    PASSPORT = "PASSPORT"
    VISA = "VISA"
    WORK_PERMIT = "WORK_PERMIT"
    EDUCATIONAL = "EDUCATIONAL"
    PROFESSIONAL = "PROFESSIONAL"
    MEDICAL = "MEDICAL"
    BACKGROUND_CHECK = "BACKGROUND_CHECK"
    TAX_FORM = "TAX_FORM"
    BANK_DETAILS = "BANK_DETAILS"
    OTHER = "OTHER"


class QualificationType(str, enum.Enum):
    """Types of educational qualifications."""
    HIGH_SCHOOL = "HIGH_SCHOOL"
    DIPLOMA = "DIPLOMA"
    ASSOCIATE = "ASSOCIATE"
    BACHELORS = "BACHELORS"
    MASTERS = "MASTERS"
    DOCTORATE = "DOCTORATE"
    PROFESSIONAL = "PROFESSIONAL"
    CERTIFICATION = "CERTIFICATION"
    OTHER = "OTHER"


class RelationshipType(str, enum.Enum):
    """Types of dependent relationships."""
    SPOUSE = "SPOUSE"
    CHILD = "CHILD"
    PARENT = "PARENT"
    SIBLING = "SIBLING"
    DOMESTIC_PARTNER = "DOMESTIC_PARTNER"
    GUARDIAN = "GUARDIAN"
    OTHER = "OTHER"


class Gender(str, enum.Enum):
    """Gender options for dependents."""
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"
    PREFER_NOT_TO_SAY = "PREFER_NOT_TO_SAY"


class SkillCategory(str, enum.Enum):
    """Categories for skills."""
    TECHNICAL = "TECHNICAL"
    SOFT_SKILL = "SOFT_SKILL"
    LANGUAGE = "LANGUAGE"
    MANAGEMENT = "MANAGEMENT"
    DOMAIN = "DOMAIN"
    TOOL = "TOOL"
    OTHER = "OTHER"


# =============================================================================
# EmployeeDocument
# =============================================================================


class EmployeeDocument(Base, AuditMixin, SoftDeleteMixin):
    """
    Employee document storage.

    Stores metadata about uploaded documents for an employee.
    Actual file storage is handled externally (S3, local filesystem, etc.)
    """

    __tablename__ = "employee_document"
    __table_args__ = (
        Index("idx_emp_doc_employee", "employee_id"),
        Index("idx_emp_doc_type", "organization_id", "document_type"),
        Index("idx_emp_doc_expiry", "organization_id", "expiry_date"),
        {"schema": "hr"},
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )

    # Document metadata
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, name="document_type"),
        nullable=False,
    )
    document_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # File information
    file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Storage path or URL",
    )
    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Original filename",
    )
    file_size: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="File size in bytes",
    )
    mime_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    # Dates
    issue_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    expiry_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    # Verification
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    verified_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
    )
    verification_notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
        back_populates="documents",
    )
    verified_by: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[verified_by_id],
    )

    @property
    def is_expired(self) -> bool:
        """Check if document has expired."""
        if self.expiry_date is None:
            return False
        return date.today() > self.expiry_date

    @property
    def days_until_expiry(self) -> Optional[int]:
        """Days until document expires (negative if already expired)."""
        if self.expiry_date is None:
            return None
        return (self.expiry_date - date.today()).days

    def __repr__(self) -> str:
        return f"<EmployeeDocument {self.document_name} ({self.document_type.value})>"


# =============================================================================
# EmployeeQualification
# =============================================================================


class EmployeeQualification(Base, AuditMixin, SoftDeleteMixin):
    """
    Employee educational qualifications.

    Tracks degrees, diplomas, and other educational achievements.
    """

    __tablename__ = "employee_qualification"
    __table_args__ = (
        Index("idx_emp_qual_employee", "employee_id"),
        Index("idx_emp_qual_type", "organization_id", "qualification_type"),
        {"schema": "hr"},
    )

    qualification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )

    # Qualification details
    qualification_type: Mapped[QualificationType] = mapped_column(
        Enum(QualificationType, name="qualification_type"),
        nullable=False,
    )
    qualification_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Degree/Diploma name",
    )
    field_of_study: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        comment="Major/Specialization",
    )
    institution_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    institution_location: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
    )

    # Dates
    start_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    end_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    is_ongoing: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    # Achievement
    grade: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Grade/GPA/Classification",
    )
    score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Numeric score if applicable",
    )
    max_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    # Verification
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee_document.document_id"),
        nullable=True,
        comment="Link to uploaded certificate",
    )

    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    employee: Mapped["Employee"] = relationship(
        "Employee",
        back_populates="qualifications",
    )
    document: Mapped[Optional["EmployeeDocument"]] = relationship(
        "EmployeeDocument",
    )

    def __repr__(self) -> str:
        return f"<EmployeeQualification {self.qualification_name} from {self.institution_name}>"


# =============================================================================
# EmployeeCertification
# =============================================================================


class EmployeeCertification(Base, AuditMixin, SoftDeleteMixin):
    """
    Employee professional certifications and licenses.

    Tracks certifications, licenses, and professional credentials
    that may have expiry dates and require renewal.
    """

    __tablename__ = "employee_certification"
    __table_args__ = (
        Index("idx_emp_cert_employee", "employee_id"),
        Index("idx_emp_cert_expiry", "organization_id", "expiry_date"),
        {"schema": "hr"},
    )

    certification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )

    # Certification details
    certification_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    issuing_authority: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    credential_id: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Certificate/License number",
    )
    credential_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Verification URL",
    )

    # Dates
    issue_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    expiry_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    does_not_expire: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    # Renewal tracking
    renewal_reminder_days: Mapped[int] = mapped_column(
        Integer,
        default=30,
        comment="Days before expiry to send reminder",
    )
    last_reminder_sent: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
    )

    # Verification
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee_document.document_id"),
        nullable=True,
    )

    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    employee: Mapped["Employee"] = relationship(
        "Employee",
        back_populates="certifications",
    )
    document: Mapped[Optional["EmployeeDocument"]] = relationship(
        "EmployeeDocument",
    )

    @property
    def is_expired(self) -> bool:
        """Check if certification has expired."""
        if self.does_not_expire or self.expiry_date is None:
            return False
        return date.today() > self.expiry_date

    @property
    def days_until_expiry(self) -> Optional[int]:
        """Days until certification expires."""
        if self.does_not_expire or self.expiry_date is None:
            return None
        return (self.expiry_date - date.today()).days

    @property
    def needs_renewal_reminder(self) -> bool:
        """Check if renewal reminder should be sent."""
        if self.is_expired or self.does_not_expire:
            return False
        days = self.days_until_expiry
        if days is None:
            return False
        return days <= self.renewal_reminder_days

    def __repr__(self) -> str:
        return f"<EmployeeCertification {self.certification_name}>"


# =============================================================================
# EmployeeDependent
# =============================================================================


class EmployeeDependent(Base, AuditMixin, SoftDeleteMixin):
    """
    Employee dependents and beneficiaries.

    Tracks family members for benefits, emergency contacts,
    and beneficiary designations.
    """

    __tablename__ = "employee_dependent"
    __table_args__ = (
        Index("idx_emp_dep_employee", "employee_id"),
        Index("idx_emp_dep_emergency", "employee_id", "is_emergency_contact"),
        {"schema": "hr"},
    )

    dependent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )

    # Personal information
    full_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    relation_type: Mapped[RelationshipType] = mapped_column(
        "relationship",  # Keep DB column name for backward compatibility
        Enum(RelationshipType, name="relationship_type"),
        nullable=False,
    )
    date_of_birth: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    gender: Mapped[Optional[Gender]] = mapped_column(
        Enum(Gender, name="dependent_gender"),
        nullable=True,
    )

    # Contact information
    phone: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
    )
    email: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    address: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Emergency contact designation
    is_emergency_contact: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    emergency_contact_priority: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Priority order for emergency contacts (1 = primary)",
    )

    # Beneficiary information
    is_beneficiary: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    beneficiary_percentage: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Percentage for benefit allocation",
    )

    # Insurance/Benefits coverage
    is_covered_under_insurance: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    insurance_id: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    employee: Mapped["Employee"] = relationship(
        "Employee",
        back_populates="dependents",
    )

    @property
    def age(self) -> Optional[int]:
        """Calculate dependent's age."""
        if self.date_of_birth is None:
            return None
        today = date.today()
        return today.year - self.date_of_birth.year - (
            (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
        )

    def __repr__(self) -> str:
        return f"<EmployeeDependent {self.full_name} ({self.relation_type.value})>"


# =============================================================================
# Skill (Catalog)
# =============================================================================


class Skill(Base, AuditMixin, SoftDeleteMixin):
    """
    Skill catalog.

    Organization-wide catalog of skills that can be assigned to employees.
    """

    __tablename__ = "skill"
    __table_args__ = (
        Index("idx_skill_org_category", "organization_id", "category"),
        Index("idx_skill_name", "organization_id", "skill_name"),
        {"schema": "hr"},
    )

    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )

    skill_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    category: Mapped[SkillCategory] = mapped_column(
        Enum(SkillCategory, name="skill_category"),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # For language skills
    is_language: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )

    def __repr__(self) -> str:
        return f"<Skill {self.skill_name} ({self.category.value})>"


# =============================================================================
# EmployeeSkill
# =============================================================================


class EmployeeSkill(Base, AuditMixin):
    """
    Employee skill proficiency tracking.

    Links employees to skills with proficiency levels.
    """

    __tablename__ = "employee_skill"
    __table_args__ = (
        Index("idx_emp_skill_employee", "employee_id"),
        Index("idx_emp_skill_skill", "skill_id"),
        {"schema": "hr"},
    )

    employee_skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.skill.skill_id"),
        nullable=False,
    )

    # Proficiency
    proficiency_level: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="1=Beginner, 2=Elementary, 3=Intermediate, 4=Advanced, 5=Expert",
    )
    years_experience: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(4, 1),
        nullable=True,
    )
    last_used_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )

    # Assessment
    is_self_assessed: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )
    assessed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    assessed_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
    )

    # Flags
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Primary/core skill for this employee",
    )
    is_certified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Has formal certification for this skill",
    )

    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
        back_populates="skills",
    )
    skill: Mapped["Skill"] = relationship("Skill")
    assessed_by: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[assessed_by_id],
    )

    @property
    def proficiency_label(self) -> str:
        """Get human-readable proficiency level."""
        labels = {
            1: "Beginner",
            2: "Elementary",
            3: "Intermediate",
            4: "Advanced",
            5: "Expert",
        }
        return labels.get(self.proficiency_level, "Unknown")

    def __repr__(self) -> str:
        return f"<EmployeeSkill {self.skill_id} - Level {self.proficiency_level}>"
