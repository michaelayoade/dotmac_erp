"""
Institutional Performance Models - OHCSF Performance Management System.

InstitutionalPerformance captures appraised scores for a ministry/department
within an appraisal cycle, including reconciliation tracking.

InstitutionalCriteriaTemplate defines the criteria catalogue used to assess
a given institution type (MINISTRY, REGULATORY, etc.).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
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
from app.models.people.base import AuditMixin
from app.models.people.perf.pms_enums import InstitutionalPerfStatus, InstitutionType

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.perf.appraisal_cycle import AppraisalCycle
    from app.models.people.perf.pms_governance import InstitutionalGovernanceAction


class InstitutionalPerformance(Base, AuditMixin):
    """
    Institutional Performance — appraisal record for a ministry/department.

    Captures composite scores, per-criteria breakdowns, and a full
    reconciliation workflow. The `criteria_scores` JSON field stores
    individual criterion scores keyed by criterion name or template ID.
    """

    __tablename__ = "institutional_performance"
    __table_args__ = (
        Index("idx_inst_perf_cycle", "cycle_id"),
        Index("idx_inst_perf_dept", "organization_id", "department_id"),
        {"schema": "perf"},
    )

    inst_perf_id: Mapped[uuid.UUID] = mapped_column(
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
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.appraisal_cycle.cycle_id"),
        nullable=False,
    )
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
    )

    # Classification
    institution_type: Mapped[InstitutionType] = mapped_column(
        Enum(InstitutionType, name="institution_type", schema="perf"),
        nullable=False,
    )
    status: Mapped[InstitutionalPerfStatus] = mapped_column(
        Enum(InstitutionalPerfStatus, name="institutional_perf_status", schema="perf"),
        nullable=False,
        default=InstitutionalPerfStatus.DRAFT,
    )

    # Scoring
    criteria_scores: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Per-criterion scores keyed by criterion name or template ID",
    )
    composite_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )
    rating_label: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="E.g. 'Excellent', 'Satisfactory', 'Needs Improvement'",
    )

    # Review
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    review_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Governance workflow
    workflow_stage: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="DRAFT",
        comment="Institutional workflow stage: DRAFT/INTERNAL_REVIEW/CENTRAL_REVIEW/APPROVED/RETURNED/FINAL_SIGNOFF",
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    approver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    submitted_for_review_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    central_review_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    approved_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    returned_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    final_signoff_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    workflow_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Reconciliation
    is_reconciled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    pre_reconciliation_composite: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Composite score captured before reconciliation adjustment",
    )
    reconciled_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    reconciliation_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    reconciliation_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
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
    cycle: Mapped["AppraisalCycle"] = relationship(
        "AppraisalCycle",
        foreign_keys=[cycle_id],
    )
    reviewed_by: Mapped["Employee | None"] = relationship(
        "Employee",
        foreign_keys=[reviewed_by_id],
    )
    reconciled_by: Mapped["Employee | None"] = relationship(
        "Employee",
        foreign_keys=[reconciled_by_id],
    )
    owner: Mapped["Employee | None"] = relationship(
        "Employee",
        foreign_keys=[owner_id],
    )
    reviewer: Mapped["Employee | None"] = relationship(
        "Employee",
        foreign_keys=[reviewer_id],
    )
    approver: Mapped["Employee | None"] = relationship(
        "Employee",
        foreign_keys=[approver_id],
    )
    governance_actions: Mapped[list["InstitutionalGovernanceAction"]] = relationship(
        "InstitutionalGovernanceAction",
        foreign_keys="InstitutionalGovernanceAction.inst_perf_id",
        back_populates="institutional_performance",
    )

    def __repr__(self) -> str:
        return f"<InstitutionalPerformance {self.institution_type}: {self.status}>"


class InstitutionalCriteriaTemplate(Base):
    """
    Institutional Criteria Template — defines assessment criteria for a
    given institution type.

    Each row represents one named criterion with a default weight and
    display order. These templates are used to pre-populate the
    criteria_scores structure on InstitutionalPerformance records.

    NOTE: Does NOT use AuditMixin — administrative reference data.
    """

    __tablename__ = "institutional_criteria_template"
    __table_args__ = (
        Index("idx_criteria_tmpl_type", "organization_id", "institution_type"),
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

    # Reuse the enum type already created by InstitutionalPerformance
    institution_type: Mapped[InstitutionType] = mapped_column(
        Enum(
            InstitutionType, name="institution_type", schema="perf", create_type=False
        ),
        nullable=False,
    )

    criteria_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    default_weight: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<InstitutionalCriteriaTemplate "
            f"{self.criteria_name!r}: {self.institution_type}>"
        )
