"""
Case Action Model - HR Schema.

Records disciplinary actions taken against employees.
"""
import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.discipline.case import DisciplinaryCase


class ActionType(str, enum.Enum):
    """Types of disciplinary actions."""

    VERBAL_WARNING = "VERBAL_WARNING"  # Verbal warning
    WRITTEN_WARNING = "WRITTEN_WARNING"  # Formal written warning
    FINAL_WARNING = "FINAL_WARNING"  # Final written warning
    SUSPENSION_PAID = "SUSPENSION_PAID"  # Suspension with pay
    SUSPENSION_UNPAID = "SUSPENSION_UNPAID"  # Suspension without pay
    DEMOTION = "DEMOTION"  # Demotion to lower grade
    SALARY_REDUCTION = "SALARY_REDUCTION"  # Temporary salary reduction
    TRANSFER = "TRANSFER"  # Transfer to different department
    MANDATORY_TRAINING = "MANDATORY_TRAINING"  # Required training
    PROBATION = "PROBATION"  # Placed on probation
    TERMINATION = "TERMINATION"  # Employment termination
    NO_ACTION = "NO_ACTION"  # Case closed, no action
    EXONERATED = "EXONERATED"  # Employee cleared of charges


class CaseAction(Base):
    """
    Case Action Model.

    Records actions/outcomes from disciplinary proceedings.
    Multiple actions can be recorded per case.
    """

    __tablename__ = "case_action"
    __table_args__ = {"schema": "hr"}

    # Primary key
    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Parent case
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.disciplinary_case.case_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Action details
    action_type: Mapped[ActionType] = mapped_column(
        Enum(ActionType, name="action_type", schema="hr"),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Details about the action",
    )

    # Duration (for suspensions, warnings, etc.)
    effective_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        default=date.today,
        comment="Date action takes effect",
    )
    end_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
        comment="End date for temporary actions",
    )

    # For warnings
    warning_expiry_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
        comment="Date warning expires from record",
    )

    # Integration flags
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Whether action is currently active",
    )
    payroll_processed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether payroll deduction has been processed",
    )
    lifecycle_triggered: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether lifecycle action has been triggered",
    )

    # Issued by
    issued_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="HR officer who issued the action",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    case: Mapped["DisciplinaryCase"] = relationship(
        "DisciplinaryCase",
        back_populates="actions",
    )
    issued_by: Mapped[Optional["Employee"]] = relationship("Employee")

    def __repr__(self) -> str:
        return f"<CaseAction {self.action_id} - {self.action_type.value}>"

    @property
    def is_suspension(self) -> bool:
        """Check if action is a suspension."""
        return self.action_type in (ActionType.SUSPENSION_PAID, ActionType.SUSPENSION_UNPAID)

    @property
    def is_termination(self) -> bool:
        """Check if action is termination."""
        return self.action_type == ActionType.TERMINATION

    @property
    def requires_payroll_deduction(self) -> bool:
        """Check if action requires payroll deduction."""
        return self.action_type == ActionType.SUSPENSION_UNPAID
