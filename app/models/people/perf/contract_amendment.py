"""
Performance Contract Amendment Workflow Model.

Tracks the staged signoff chain for contract amendments:
appraisee -> appraiser -> HoD -> HR Head.
"""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.perf.performance_contract import PerformanceContract


class ContractAmendmentWorkflow(Base, AuditMixin):
    """Approval/signoff workflow for a proposed contract amendment."""

    __tablename__ = "contract_amendment_workflow"
    __table_args__ = (
        Index("idx_contract_amendment_org_status", "organization_id", "status"),
        Index("idx_contract_amendment_contract", "organization_id", "contract_id"),
        {"schema": "perf"},
    )

    amendment_workflow_id: Mapped[uuid.UUID] = mapped_column(
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
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.performance_contract.contract_id"),
        nullable=False,
        comment="Amended contract awaiting staged signoff",
    )
    original_contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.performance_contract.contract_id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="PENDING",
        comment="PENDING / APPROVED / REJECTED",
    )

    appraisee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    appraiser_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    hod_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    hr_head_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )

    appraisee_signed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    appraiser_signed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    hod_signed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    hr_head_signed_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    rejected_by_stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    signoff_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True, onupdate=func.now())

    contract: Mapped["PerformanceContract"] = relationship(
        "PerformanceContract",
        foreign_keys=[contract_id],
    )
    original_contract: Mapped["PerformanceContract"] = relationship(
        "PerformanceContract",
        foreign_keys=[original_contract_id],
    )
    appraisee: Mapped["Employee"] = relationship("Employee", foreign_keys=[appraisee_id])
    appraiser: Mapped["Employee"] = relationship("Employee", foreign_keys=[appraiser_id])
    hod: Mapped["Employee"] = relationship("Employee", foreign_keys=[hod_id])
    hr_head: Mapped["Employee"] = relationship("Employee", foreign_keys=[hr_head_id])
