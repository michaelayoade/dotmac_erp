"""
Appraisal Model - Performance Schema.

Individual employee appraisals within a cycle.
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
    JSON,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin, StatusTrackingMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.perf.appraisal_cycle import AppraisalCycle
    from app.models.people.perf.appraisal_template import AppraisalTemplate
    from app.models.people.perf.kra import KRA


class AppraisalStatus(str, enum.Enum):
    """Appraisal workflow status."""

    DRAFT = "DRAFT"
    SELF_ASSESSMENT = "SELF_ASSESSMENT"  # Employee filling in
    PENDING_REVIEW = "PENDING_REVIEW"  # Submitted for manager review
    UNDER_REVIEW = "UNDER_REVIEW"  # Manager is reviewing
    PENDING_CALIBRATION = "PENDING_CALIBRATION"
    CALIBRATION = "CALIBRATION"  # HR calibrating
    PENDING_COUNTERSIGN = "PENDING_COUNTERSIGN"
    COUNTERSIGNED = "COUNTERSIGNED"
    PENDING_COMMITTEE = "PENDING_COMMITTEE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


_JSON = JSON().with_variant(JSONB, "postgresql")


class Appraisal(Base, AuditMixin, StatusTrackingMixin, ERPNextSyncMixin):
    """
    Appraisal - individual employee performance appraisal.

    Tracks self-assessment, manager review, and final scores.
    """

    __tablename__ = "appraisal"
    __table_args__ = (
        Index("idx_appraisal_employee", "employee_id"),
        Index("idx_appraisal_cycle", "cycle_id"),
        Index("idx_appraisal_manager", "manager_id"),
        Index("idx_appraisal_status", "organization_id", "status"),
        {"schema": "perf"},
    )

    appraisal_id: Mapped[uuid.UUID] = mapped_column(
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

    # Employee & Cycle
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.appraisal_cycle.cycle_id"),
        nullable=False,
    )

    # Template
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.appraisal_template.template_id"),
        nullable=True,
    )

    # Manager
    manager_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
        comment="Reviewing manager (may differ from current manager)",
    )

    # Status
    status: Mapped[AppraisalStatus] = mapped_column(
        Enum(AppraisalStatus, name="appraisal_status"),
        default=AppraisalStatus.DRAFT,
    )

    # Self Assessment
    self_assessment_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    self_overall_rating: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Employee's self-rating (1-5)",
    )
    self_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    achievements: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    challenges: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    development_needs: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Manager Review
    manager_review_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    manager_overall_rating: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Manager's rating (1-5)",
    )
    manager_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    manager_recommendations: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Calibration
    calibration_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    calibrated_rating: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="HR-calibrated final rating",
    )
    calibration_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Final Scores
    final_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Weighted average score",
    )
    final_rating: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Final rating (1-5)",
    )
    rating_label: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Exceptional, Exceeds, Meets, Below, etc.",
    )

    # Completion
    completed_on: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    # --- OHCSF PMS fields (only used when pms_ohcsf_enabled) ---

    # Counter-signing & committee
    counter_signer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    counter_signer_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    counter_signer_comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    committee_review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    committee_decision: Mapped[str | None] = mapped_column(String(50), nullable=True)
    committee_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_quarterly: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quarterly_rating: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )

    # Process scoring (10% bucket)
    process_self_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    process_manager_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    process_final_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    process_comments: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Composite breakdown
    objective_weighted_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    competency_weighted_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    process_weighted_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )

    # Approved absence carryover
    is_prior_year_carryover: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    carryover_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.appraisal.appraisal_id"),
        nullable=True,
    )
    absence_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_absence_evidence: Mapped[dict | None] = mapped_column(_JSON, nullable=True)

    # Probation
    is_probation_appraisal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    confirmation_recommendation: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )

    # Secondment
    is_secondment_appraisal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    secondment_org_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    parent_org_notified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    parent_org_notified_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Debrief
    debrief_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    debrief_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    debrief_acknowledged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Reward nomination
    reward_nominated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    reward_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reward_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )
    manager: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[manager_id],
    )
    cycle: Mapped["AppraisalCycle"] = relationship(
        "AppraisalCycle",
        back_populates="appraisals",
    )
    template: Mapped[Optional["AppraisalTemplate"]] = relationship("AppraisalTemplate")
    kra_scores: Mapped[list["AppraisalKRAScore"]] = relationship(
        "AppraisalKRAScore",
        back_populates="appraisal",
    )
    feedback: Mapped[list["AppraisalFeedback"]] = relationship(
        "AppraisalFeedback",
        back_populates="appraisal",
    )

    def __repr__(self) -> str:
        return f"<Appraisal {self.employee_id} in {self.cycle_id}>"


class AppraisalKRAScore(Base):
    """
    Appraisal KRA Score - individual KRA scores within an appraisal.
    """

    __tablename__ = "appraisal_kra_score"
    __table_args__ = (
        Index("idx_kra_score_appraisal", "appraisal_id"),
        Index("idx_kra_score_kra", "kra_id"),
        {"schema": "perf"},
    )

    score_id: Mapped[uuid.UUID] = mapped_column(
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
    appraisal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.appraisal.appraisal_id"),
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
    )

    # Scores
    self_rating: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    self_comments: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    manager_rating: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    manager_comments: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Final
    final_rating: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    weighted_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="(final_rating / max_rating) * weightage",
    )

    # OHCSF per-KPI criteria thresholds
    target_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    achievement_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    outstanding_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    excellent_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    good_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    fair_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    poor_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    actual_achievement: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    raw_score_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
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
    appraisal: Mapped["Appraisal"] = relationship(
        "Appraisal",
        back_populates="kra_scores",
    )
    kra: Mapped["KRA"] = relationship("KRA")

    def __repr__(self) -> str:
        return f"<AppraisalKRAScore {self.appraisal_id}:{self.kra_id}>"


class AppraisalFeedback(Base, AuditMixin):
    """
    Appraisal Feedback - 360-degree feedback from peers/subordinates.
    """

    __tablename__ = "appraisal_feedback"
    __table_args__ = (
        Index("idx_feedback_appraisal", "appraisal_id"),
        Index("idx_feedback_from", "feedback_from_id"),
        {"schema": "perf"},
    )

    feedback_id: Mapped[uuid.UUID] = mapped_column(
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
    appraisal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.appraisal.appraisal_id"),
        nullable=False,
    )
    feedback_from_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )

    # Feedback type
    feedback_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="PEER, SUBORDINATE, EXTERNAL",
    )

    # Ratings
    overall_rating: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Feedback content
    strengths: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    areas_for_improvement: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    general_comments: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Status
    is_anonymous: Mapped[bool] = mapped_column(
        default=False,
    )
    submitted_on: Mapped[date | None] = mapped_column(
        Date,
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
    appraisal: Mapped["Appraisal"] = relationship(
        "Appraisal",
        back_populates="feedback",
    )
    feedback_from: Mapped["Employee"] = relationship("Employee")

    def __repr__(self) -> str:
        return f"<AppraisalFeedback from {self.feedback_from_id}>"
