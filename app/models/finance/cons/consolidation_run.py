"""
Consolidation Run Model - Consolidation Schema.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

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


class ConsolidationStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"


class ConsolidationRun(Base):
    """
    Consolidation run header.
    """

    __tablename__ = "consolidation_run"
    __table_args__ = (
        UniqueConstraint(
            "group_id",
            "fiscal_period_id",
            "run_number",
            name="uq_consolidation_run",
        ),
        {"schema": "cons"},
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )
    run_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    run_description: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    reporting_currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    status: Mapped[ConsolidationStatus] = mapped_column(
        Enum(ConsolidationStatus, name="consolidation_status"),
        nullable=False,
        default=ConsolidationStatus.DRAFT,
    )

    # Entities included
    entities_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    subsidiaries_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    associates_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Run statistics
    elimination_entries_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    total_eliminations_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    intercompany_differences: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Translation
    total_translation_adjustment: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # NCI
    total_nci: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Execution tracking
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # SoD tracking
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
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
