"""
Depreciation Run Model - FA Schema.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DepreciationRunStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    CALCULATING = "CALCULATING"
    CALCULATED = "CALCULATED"
    POSTING = "POSTING"
    POSTED = "POSTED"
    FAILED = "FAILED"
    REVERSED = "REVERSED"


class DepreciationRun(Base):
    """
    Depreciation calculation run for a fiscal period.
    """

    __tablename__ = "depreciation_run"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "fiscal_period_id",
            "run_number",
            name="uq_depreciation_run",
        ),
        {"schema": "fa"},
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
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

    run_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    run_description: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[DepreciationRunStatus] = mapped_column(
        Enum(DepreciationRunStatus, name="depreciation_run_status"),
        nullable=False,
        default=DepreciationRunStatus.DRAFT,
    )

    # Run statistics
    assets_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_depreciation: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Journal entry reference
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    posting_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Execution tracking
    calculation_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    calculation_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # SoD tracking
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    posted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
