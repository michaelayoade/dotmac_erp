"""
Disclosure Checklist Model - Reporting Schema.
"""

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DisclosureStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    REVIEWED = "REVIEWED"


class DisclosureChecklist(Base):
    """
    IFRS disclosure checklist item.
    """

    __tablename__ = "disclosure_checklist"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "fiscal_period_id",
            "disclosure_code",
            name="uq_disclosure_checklist",
        ),
        {"schema": "rpt"},
    )

    checklist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )

    # Disclosure identification
    disclosure_code: Mapped[str] = mapped_column(String(50), nullable=False)
    disclosure_name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # IFRS standard reference
    ifrs_standard: Mapped[str] = mapped_column(String(50), nullable=False)
    paragraph_reference: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )

    # Hierarchy
    parent_checklist_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rpt.disclosure_checklist.checklist_id"),
        nullable=True,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    indent_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Requirements
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    applicability_criteria: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Status
    status: Mapped[DisclosureStatus] = mapped_column(
        Enum(DisclosureStatus, name="disclosure_status"),
        nullable=False,
        default=DisclosureStatus.NOT_STARTED,
    )

    # Completion details
    disclosure_location: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Review
    completed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
