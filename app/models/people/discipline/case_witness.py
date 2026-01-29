"""
Case Witness Model - HR Schema.

Tracks witnesses associated with disciplinary cases.
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.discipline.case import DisciplinaryCase


class CaseWitness(Base):
    """
    Case Witness Model.

    Records witnesses to incidents or hearings, with optional statements.
    """

    __tablename__ = "case_witness"
    __table_args__ = {"schema": "hr"}

    # Primary key
    witness_id: Mapped[uuid.UUID] = mapped_column(
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

    # Witness (can be internal employee or external)
    employee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Internal employee witness",
    )
    external_name: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        comment="External witness name if not an employee",
    )
    external_contact: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="External witness contact details",
    )

    # Witness statement
    statement: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Written statement from witness",
    )
    statement_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Date statement was provided",
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
        back_populates="witnesses",
    )
    employee: Mapped[Optional["Employee"]] = relationship("Employee")

    def __repr__(self) -> str:
        name = self.external_name or f"Employee {self.employee_id}"
        return f"<CaseWitness {self.witness_id} - {name}>"
