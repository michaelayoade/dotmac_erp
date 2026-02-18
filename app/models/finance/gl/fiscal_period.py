"""
Fiscal Period Model - GL Schema.
Document 08: Period management.
"""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class PeriodStatus(str, enum.Enum):
    FUTURE = "FUTURE"
    OPEN = "OPEN"
    SOFT_CLOSED = "SOFT_CLOSED"
    HARD_CLOSED = "HARD_CLOSED"
    REOPENED = "REOPENED"

    @classmethod
    def accepts_postings(cls) -> frozenset["PeriodStatus"]:
        """Statuses where new journal entries can be posted to this period."""
        return frozenset({cls.OPEN, cls.REOPENED})

    @classmethod
    def closed(cls) -> frozenset["PeriodStatus"]:
        """Statuses where the period is locked for posting."""
        return frozenset({cls.SOFT_CLOSED, cls.HARD_CLOSED})


class FiscalPeriod(Base):
    """
    Fiscal period (typically monthly) for period controls.
    Document 08: Period management.
    """

    __tablename__ = "fiscal_period"
    __table_args__ = (
        UniqueConstraint("fiscal_year_id", "period_number", name="uq_fiscal_period"),
        Index("idx_period_status", "organization_id", "status"),
        Index("idx_period_dates", "start_date", "end_date"),
        {"schema": "gl"},
    )

    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
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
    fiscal_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_year.fiscal_year_id"),
        nullable=False,
    )

    period_number: Mapped[int] = mapped_column(Integer, nullable=False)
    period_name: Mapped[str] = mapped_column(String(30), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Period type
    is_adjustment_period: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_closing_period: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Status management (Document 08)
    status: Mapped[PeriodStatus] = mapped_column(
        Enum(PeriodStatus, name="period_status"),
        nullable=False,
        default=PeriodStatus.FUTURE,
    )

    # Soft close
    soft_closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    soft_closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Hard close
    hard_closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    hard_closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Reopen tracking
    reopen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_reopen_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    fiscal_year: Mapped["FiscalYear"] = relationship(
        "FiscalYear",
        back_populates="periods",
    )


# Forward reference
from app.models.finance.gl.fiscal_year import FiscalYear  # noqa: E402
