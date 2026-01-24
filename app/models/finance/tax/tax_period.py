"""
Tax Period Model - Tax Schema.
"""
import enum
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TaxPeriodStatus(str, enum.Enum):
    OPEN = "OPEN"
    FILED = "FILED"
    PAID = "PAID"
    CLOSED = "CLOSED"


class TaxPeriodFrequency(str, enum.Enum):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUAL = "ANNUAL"


class TaxPeriod(Base):
    """
    Tax reporting period.
    """

    __tablename__ = "tax_period"
    __table_args__ = (
        UniqueConstraint("organization_id", "jurisdiction_id", "period_name", name="uq_tax_period"),
        Index("idx_tax_period_dates", "start_date", "end_date"),
        {"schema": "tax"},
    )

    period_id: Mapped[uuid.UUID] = mapped_column(
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
    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.tax_jurisdiction.jurisdiction_id"),
        nullable=False,
    )
    fiscal_period_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    period_name: Mapped[str] = mapped_column(String(30), nullable=False)
    frequency: Mapped[TaxPeriodFrequency] = mapped_column(
        Enum(TaxPeriodFrequency, name="tax_period_frequency"),
        nullable=False,
    )

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[TaxPeriodStatus] = mapped_column(
        Enum(TaxPeriodStatus, name="tax_period_status"),
        nullable=False,
        default=TaxPeriodStatus.OPEN,
    )

    is_extension_filed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extended_due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

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
