"""
Staging Tables for ERPNext Data Migration.

Raw data is synced here first for validation and review before
importing to production tables.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class StagingStatus:
    """Validation status constants."""
    PENDING = "PENDING"      # Not yet validated
    VALID = "VALID"          # Passed all validations
    INVALID = "INVALID"      # Has validation errors
    IMPORTED = "IMPORTED"    # Successfully imported to production
    SKIPPED = "SKIPPED"      # Skipped (e.g., already exists)


class StagingEmployee(Base):
    """
    Staging table for ERPNext Employee data.

    Stores raw employee data for validation before import.
    """

    __tablename__ = "staging_employee"
    __table_args__ = (
        Index("idx_staging_emp_org", "organization_id"),
        Index("idx_staging_emp_status", "validation_status"),
        Index("idx_staging_emp_batch", "batch_id"),
        Index("idx_staging_emp_source", "source_name"),
        {"schema": "sync"},
    )

    staging_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Groups records from same sync run",
    )

    # ERPNext source identifiers
    source_name: Mapped[str] = mapped_column(
        String(140), nullable=False, comment="ERPNext document name (e.g., HR-EMP-00001)"
    )
    source_modified: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="ERPNext modified timestamp"
    )

    # Raw ERPNext data (stored as-is for reference)
    raw_data: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, comment="Complete ERPNext document"
    )

    # Mapped fields (transformed from ERPNext)
    employee_code: Mapped[str] = mapped_column(String(30), nullable=False)
    employee_name: Mapped[str] = mapped_column(String(200), nullable=False)
    first_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Contact info
    company_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    personal_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    preferred_email: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="company_email or personal_email"
    )
    cell_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Organization references (ERPNext names, not UUIDs)
    department_name: Mapped[Optional[str]] = mapped_column(String(140), nullable=True)
    designation_name: Mapped[Optional[str]] = mapped_column(String(140), nullable=True)
    employment_type_name: Mapped[Optional[str]] = mapped_column(String(140), nullable=True)
    grade_name: Mapped[Optional[str]] = mapped_column(String(140), nullable=True)
    reports_to_name: Mapped[Optional[str]] = mapped_column(String(140), nullable=True)

    # Employment details
    date_of_joining: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    date_of_leaving: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Bank details
    bank_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    bank_ac_no: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Validation state
    validation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=StagingStatus.PENDING
    )
    validation_errors: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, comment="List of validation error messages"
    )
    validation_warnings: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, comment="List of validation warnings"
    )

    # Import tracking
    imported_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    imported_employee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, comment="Production employee_id after import"
    )
    imported_person_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, comment="Production person_id after import"
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, onupdate=func.now()
    )


class StagingDepartment(Base):
    """Staging table for ERPNext Department data."""

    __tablename__ = "staging_department"
    __table_args__ = (
        Index("idx_staging_dept_org", "organization_id"),
        Index("idx_staging_dept_status", "validation_status"),
        Index("idx_staging_dept_batch", "batch_id"),
        {"schema": "sync"},
    )

    staging_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # ERPNext source
    source_name: Mapped[str] = mapped_column(String(140), nullable=False)
    source_modified: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Mapped fields
    department_code: Mapped[str] = mapped_column(String(50), nullable=False)
    department_name: Mapped[str] = mapped_column(String(140), nullable=False)
    parent_department_name: Mapped[Optional[str]] = mapped_column(String(140), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Validation
    validation_status: Mapped[str] = mapped_column(String(20), nullable=False, default=StagingStatus.PENDING)
    validation_errors: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    validation_warnings: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # Import tracking
    imported_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    imported_department_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class StagingDesignation(Base):
    """Staging table for ERPNext Designation data."""

    __tablename__ = "staging_designation"
    __table_args__ = (
        Index("idx_staging_desg_org", "organization_id"),
        Index("idx_staging_desg_status", "validation_status"),
        {"schema": "sync"},
    )

    staging_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    source_name: Mapped[str] = mapped_column(String(140), nullable=False)
    source_modified: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    designation_code: Mapped[str] = mapped_column(String(50), nullable=False)
    designation_name: Mapped[str] = mapped_column(String(140), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    validation_status: Mapped[str] = mapped_column(String(20), nullable=False, default=StagingStatus.PENDING)
    validation_errors: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    validation_warnings: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    imported_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    imported_designation_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class StagingEmploymentType(Base):
    """Staging table for ERPNext Employment Type data."""

    __tablename__ = "staging_employment_type"
    __table_args__ = (
        Index("idx_staging_emptype_org", "organization_id"),
        {"schema": "sync"},
    )

    staging_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    source_name: Mapped[str] = mapped_column(String(140), nullable=False)
    source_modified: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    type_code: Mapped[str] = mapped_column(String(30), nullable=False)
    type_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    validation_status: Mapped[str] = mapped_column(String(20), nullable=False, default=StagingStatus.PENDING)
    validation_errors: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    imported_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    imported_employment_type_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class StagingEmployeeGrade(Base):
    """Staging table for ERPNext Employee Grade data."""

    __tablename__ = "staging_employee_grade"
    __table_args__ = (
        Index("idx_staging_grade_org", "organization_id"),
        {"schema": "sync"},
    )

    staging_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    source_name: Mapped[str] = mapped_column(String(140), nullable=False)
    source_modified: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    grade_code: Mapped[str] = mapped_column(String(30), nullable=False)
    grade_name: Mapped[str] = mapped_column(String(100), nullable=False)
    default_base_pay: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    validation_status: Mapped[str] = mapped_column(String(20), nullable=False, default=StagingStatus.PENDING)
    validation_errors: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    imported_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    imported_grade_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class StagingSyncBatch(Base):
    """
    Tracks each staging sync batch/run.

    Groups staging records together and tracks overall batch status.
    """

    __tablename__ = "staging_sync_batch"
    __table_args__ = (
        Index("idx_staging_batch_org", "organization_id"),
        Index("idx_staging_batch_status", "status"),
        {"schema": "sync"},
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    source_system: Mapped[str] = mapped_column(String(50), nullable=False, default="erpnext")
    entity_types: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, comment="Entity types included in this batch"
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="SYNCING",
        comment="SYNCING, SYNCED, VALIDATING, VALIDATED, IMPORTING, IMPORTED, FAILED"
    )

    # Counts
    total_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Validation summary
    validation_summary: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, comment="Summary of validation results by type"
    )

    # Timestamps
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    imported_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # User tracking
    initiated_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
