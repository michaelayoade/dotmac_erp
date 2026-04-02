"""
PMS Governance Models.

Adds governance action logging, grievances, and stakeholder feedback records
for the OHCSF institutional workflow.
"""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.perf.appraisal import Appraisal
    from app.models.people.perf.institutional_performance import InstitutionalPerformance


class InstitutionalGovernanceAction(Base, AuditMixin):
    """Audit trail for institutional workflow actions."""

    __tablename__ = "institutional_governance_action"
    __table_args__ = (
        Index("idx_inst_gov_action_inst_perf", "organization_id", "inst_perf_id"),
        {"schema": "perf"},
    )

    action_id: Mapped[uuid.UUID] = mapped_column(
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
    inst_perf_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.institutional_performance.inst_perf_id"),
        nullable=False,
    )
    actor_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    actor_role: Mapped[str] = mapped_column(String(50), nullable=False)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    from_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    to_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True, onupdate=func.now())

    institutional_performance: Mapped["InstitutionalPerformance"] = relationship(
        "InstitutionalPerformance",
        foreign_keys=[inst_perf_id],
        back_populates="governance_actions",
    )
    actor: Mapped["Employee | None"] = relationship(
        "Employee",
        foreign_keys=[actor_employee_id],
    )


class PMSGovernanceGrievance(Base, AuditMixin):
    """Formal PMS grievance/dispute record for employees."""

    __tablename__ = "pms_governance_grievance"
    __table_args__ = (
        Index("idx_pms_grievance_org_status", "organization_id", "status"),
        {"schema": "perf"},
    )

    grievance_id: Mapped[uuid.UUID] = mapped_column(
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
    appraisal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.appraisal.appraisal_id"),
        nullable=True,
    )
    inst_perf_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.institutional_performance.inst_perf_id"),
        nullable=True,
    )
    raised_by_employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    assigned_to_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False, default="INTERNAL")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="OPEN")
    committee_level: Mapped[str | None] = mapped_column(String(30), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    escalated_to_fcsc: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    escalated_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    raised_date: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    resolved_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True, onupdate=func.now())

    appraisal: Mapped["Appraisal | None"] = relationship(
        "Appraisal",
        foreign_keys=[appraisal_id],
    )
    institutional_performance: Mapped["InstitutionalPerformance | None"] = relationship(
        "InstitutionalPerformance",
        foreign_keys=[inst_perf_id],
    )
    raised_by: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[raised_by_employee_id],
    )
    assigned_to: Mapped["Employee | None"] = relationship(
        "Employee",
        foreign_keys=[assigned_to_employee_id],
    )


class PMSStakeholderFeedback(Base, AuditMixin):
    """Stakeholder/citizen feedback linked to PMS governance records."""

    __tablename__ = "pms_stakeholder_feedback"
    __table_args__ = (
        Index("idx_pms_feedback_org_status", "organization_id", "status"),
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
    inst_perf_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.institutional_performance.inst_perf_id"),
        nullable=True,
    )
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, default="SERVICOM")
    channel: Mapped[str] = mapped_column(String(40), nullable=False, default="PORTAL")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="RECEIVED")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    feedback_text: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_by_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    submitted_by_contact: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    owner_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    action_taken: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_date: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    closed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True, onupdate=func.now())

    institutional_performance: Mapped["InstitutionalPerformance | None"] = relationship(
        "InstitutionalPerformance",
        foreign_keys=[inst_perf_id],
    )
    owner: Mapped["Employee | None"] = relationship(
        "Employee",
        foreign_keys=[owner_employee_id],
    )
