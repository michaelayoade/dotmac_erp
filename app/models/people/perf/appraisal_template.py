"""
Appraisal Template Model - Performance Schema.

Defines appraisal structure with KRAs and weightages.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin

if TYPE_CHECKING:
    from app.models.people.perf.kra import KRA


class AppraisalTemplate(Base, AuditMixin, ERPNextSyncMixin):
    """
    Appraisal Template - defines structure for appraisals.

    Can be organization-wide, department-specific, or designation-specific.
    """

    __tablename__ = "appraisal_template"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "template_code", name="uq_appraisal_template_code"
        ),
        Index("idx_appraisal_template_dept", "organization_id", "department_id"),
        Index("idx_appraisal_template_desig", "organization_id", "designation_id"),
        {"schema": "perf"},
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
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

    # Identification
    template_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    template_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Scope
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
    )
    designation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.designation.designation_id"),
        nullable=True,
    )

    # Rating scale
    rating_scale_max: Mapped[int] = mapped_column(
        Integer,
        default=5,
        comment="Maximum rating (e.g., 5 for 1-5 scale)",
    )

    # Status
    is_active: Mapped[bool] = mapped_column(
        default=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    kras: Mapped[list["AppraisalTemplateKRA"]] = relationship(
        "AppraisalTemplateKRA",
        back_populates="template",
    )

    def __repr__(self) -> str:
        return f"<AppraisalTemplate {self.template_code}: {self.template_name}>"


class AppraisalTemplateKRA(Base):
    """
    Appraisal Template KRA - links KRAs to templates with weightage.
    """

    __tablename__ = "appraisal_template_kra"
    __table_args__ = (
        Index("idx_template_kra_template", "template_id"),
        Index("idx_template_kra_kra", "kra_id"),
        {"schema": "perf"},
    )

    template_kra_id: Mapped[uuid.UUID] = mapped_column(
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

    # Links
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.appraisal_template.template_id"),
        nullable=False,
    )
    kra_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.kra.kra_id"),
        nullable=False,
    )

    # Weightage
    weightage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        comment="Weight percentage (0-100)",
    )

    # Order
    sequence: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    template: Mapped["AppraisalTemplate"] = relationship(
        "AppraisalTemplate",
        back_populates="kras",
    )
    kra: Mapped["KRA"] = relationship("KRA")

    def __repr__(self) -> str:
        return f"<AppraisalTemplateKRA {self.template_id}:{self.kra_id}>"
