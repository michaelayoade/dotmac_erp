"""Generic configurable form engine models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class FormStatus(str, enum.Enum):
    """Form/version lifecycle status."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class FormSubmissionStatus(str, enum.Enum):
    """Form submission lifecycle status."""

    SUBMITTED = "SUBMITTED"
    VOIDED = "VOIDED"


class FormFieldType(str, enum.Enum):
    """Supported configurable form field types."""

    TEXT = "TEXT"
    LONG_TEXT = "LONG_TEXT"
    NUMBER = "NUMBER"
    DATE = "DATE"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    URL = "URL"
    SINGLE_CHOICE = "SINGLE_CHOICE"
    MULTI_CHOICE = "MULTI_CHOICE"
    DROPDOWN = "DROPDOWN"
    CHECKBOX = "CHECKBOX"
    YES_NO = "YES_NO"
    FILE = "FILE"
    IMAGE = "IMAGE"
    PDF = "PDF"
    CONSENT = "CONSENT"
    RATING = "RATING"


class DynamicForm(Base):
    """Top-level reusable form container."""

    __tablename__ = "form"
    __table_args__ = (
        Index("idx_forms_form_org_type", "organization_id", "form_type"),
        {"schema": "forms"},
    )

    form_id: Mapped[uuid.UUID] = mapped_column(
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
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    form_type: Mapped[str] = mapped_column(String(60), nullable=False)
    owner_entity_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    owner_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    versions: Mapped[list[DynamicFormVersion]] = relationship(
        "DynamicFormVersion",
        back_populates="form",
        cascade="all, delete-orphan",
    )


class DynamicFormVersion(Base):
    """Immutable-ish version of a form definition."""

    __tablename__ = "form_version"
    __table_args__ = (
        UniqueConstraint("form_id", "version_number", name="uq_form_version_number"),
        Index("idx_forms_version_org_status", "organization_id", "status"),
        {"schema": "forms"},
    )

    form_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    form_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("forms.form.form_id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[FormStatus] = mapped_column(
        Enum(FormStatus, name="form_status"),
        nullable=False,
        default=FormStatus.DRAFT,
    )
    settings_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    form: Mapped[DynamicForm] = relationship("DynamicForm", back_populates="versions")
    sections: Mapped[list[DynamicFormSection]] = relationship(
        "DynamicFormSection",
        back_populates="form_version",
        cascade="all, delete-orphan",
        order_by="DynamicFormSection.sort_order",
    )


class DynamicFormSection(Base):
    """Section grouping fields in a form version."""

    __tablename__ = "form_section"
    __table_args__ = (
        Index("idx_forms_section_version", "form_version_id", "sort_order"),
        {"schema": "forms"},
    )

    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    form_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("forms.form_version.form_version_id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    form_version: Mapped[DynamicFormVersion] = relationship(
        "DynamicFormVersion",
        back_populates="sections",
    )
    fields: Mapped[list[DynamicFormField]] = relationship(
        "DynamicFormField",
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="DynamicFormField.sort_order",
    )


class DynamicFormField(Base):
    """Configurable field/question in a form section."""

    __tablename__ = "form_field"
    __table_args__ = (
        UniqueConstraint(
            "form_version_id",
            "field_key",
            name="uq_form_field_version_key",
        ),
        Index("idx_forms_field_version", "form_version_id", "sort_order"),
        {"schema": "forms"},
    )

    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    form_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("forms.form_version.form_version_id", ondelete="CASCADE"),
        nullable=False,
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("forms.form_section.section_id", ondelete="CASCADE"),
        nullable=False,
    )
    field_key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(240), nullable=False)
    field_type: Mapped[FormFieldType] = mapped_column(
        Enum(FormFieldType, name="form_field_type"),
        nullable=False,
    )
    help_text: Mapped[str | None] = mapped_column(Text)
    placeholder: Mapped[str | None] = mapped_column(String(240))
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    show_in_list: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_filterable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    system_mapping: Mapped[str | None] = mapped_column(String(60))
    settings_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    validation_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    visibility_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    section: Mapped[DynamicFormSection] = relationship(
        "DynamicFormSection",
        back_populates="fields",
    )
    options: Mapped[list[DynamicFormFieldOption]] = relationship(
        "DynamicFormFieldOption",
        back_populates="field",
        cascade="all, delete-orphan",
        order_by="DynamicFormFieldOption.sort_order",
    )


class DynamicFormFieldOption(Base):
    """Selectable option for choice fields."""

    __tablename__ = "form_field_option"
    __table_args__ = (
        Index("idx_forms_option_field", "field_id", "sort_order"),
        {"schema": "forms"},
    )

    option_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("forms.form_field.field_id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(240), nullable=False)
    value: Mapped[str] = mapped_column(String(160), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    field: Mapped[DynamicFormField] = relationship(
        "DynamicFormField",
        back_populates="options",
    )


class DynamicFormSubmission(Base):
    """A submitted response to a form version."""

    __tablename__ = "form_submission"
    __table_args__ = (
        Index("idx_forms_submission_org_form", "organization_id", "form_version_id"),
        Index("idx_forms_submission_subject", "subject_type", "subject_id"),
        {"schema": "forms"},
    )

    submission_id: Mapped[uuid.UUID] = mapped_column(
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
    form_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("forms.form_version.form_version_id"),
        nullable=False,
    )
    subject_type: Mapped[str | None] = mapped_column(String(80))
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[FormSubmissionStatus] = mapped_column(
        Enum(FormSubmissionStatus, name="form_submission_status"),
        nullable=False,
        default=FormSubmissionStatus.SUBMITTED,
    )
    submitted_by_email: Mapped[str | None] = mapped_column(String(255))
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    answers: Mapped[list[DynamicFormAnswer]] = relationship(
        "DynamicFormAnswer",
        back_populates="submission",
        cascade="all, delete-orphan",
    )


class DynamicFormAnswer(Base):
    """One answer for one submitted dynamic field."""

    __tablename__ = "form_answer"
    __table_args__ = (
        UniqueConstraint("submission_id", "field_id", name="uq_form_answer_field"),
        Index("idx_forms_answer_field", "field_id"),
        {"schema": "forms"},
    )

    answer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("forms.form_submission.submission_id", ondelete="CASCADE"),
        nullable=False,
    )
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("forms.form_field.field_id"),
        nullable=False,
    )
    field_key_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    field_label_snapshot: Mapped[str] = mapped_column(String(240), nullable=False)
    field_type_snapshot: Mapped[str] = mapped_column(String(40), nullable=False)
    value_json: Mapped[Any] = mapped_column(JSONB, nullable=True)
    display_value: Mapped[str | None] = mapped_column(Text)
    file_url: Mapped[str | None] = mapped_column(String(500))
    file_name: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    submission: Mapped[DynamicFormSubmission] = relationship(
        "DynamicFormSubmission",
        back_populates="answers",
    )
    field: Mapped[DynamicFormField] = relationship("DynamicFormField")
