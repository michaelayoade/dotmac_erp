"""
Case Response Model - HR Schema.

Stores employee responses to disciplinary queries.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.people.discipline.case import DisciplinaryCase


class CaseResponse(Base):
    """
    Case Response Model.

    Records employee responses to queries and at different stages
    of the disciplinary process.
    """

    __tablename__ = "case_response"
    __table_args__ = {"schema": "hr"}

    # Primary key
    response_id: Mapped[uuid.UUID] = mapped_column(
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

    # Response content
    response_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Employee's response text",
    )

    # Flags
    is_initial_response: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Whether this is the initial query response",
    )
    is_appeal_response: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether this is an appeal response",
    )

    # Timestamps
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When response was submitted",
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When HR acknowledged the response",
    )

    # Relationships
    case: Mapped["DisciplinaryCase"] = relationship(
        "DisciplinaryCase",
        back_populates="responses",
    )

    def __repr__(self) -> str:
        return f"<CaseResponse {self.response_id} - Case {self.case_id}>"
