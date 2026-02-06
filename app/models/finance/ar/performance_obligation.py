"""
Performance Obligation Model - AR Schema.
IFRS 15 Performance Obligations.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SatisfactionPattern(str, enum.Enum):
    POINT_IN_TIME = "POINT_IN_TIME"
    OVER_TIME = "OVER_TIME"


class PerformanceObligation(Base):
    """
    IFRS 15 Performance obligation within a contract.
    """

    __tablename__ = "performance_obligation"
    __table_args__ = (
        UniqueConstraint("contract_id", "obligation_number", name="uq_obligation"),
        {"schema": "ar"},
    )

    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.contract.contract_id"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    obligation_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Distinctness
    is_distinct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Satisfaction pattern
    satisfaction_pattern: Mapped[SatisfactionPattern] = mapped_column(
        Enum(SatisfactionPattern, name="satisfaction_pattern"),
        nullable=False,
    )
    over_time_method: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="OUTPUT, INPUT, STRAIGHT_LINE",
    )
    progress_measure: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Pricing
    standalone_selling_price: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )
    ssp_determination_method: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="OBSERVABLE, ADJUSTED_MARKET, EXPECTED_COST_PLUS, RESIDUAL",
    )
    allocated_transaction_price: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )

    # Progress
    total_satisfied_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    satisfaction_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=0,
    )

    # Timeline
    expected_completion_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True
    )
    actual_completion_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Status
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="NOT_STARTED",
        comment="NOT_STARTED, IN_PROGRESS, SATISFIED, CANCELLED",
    )

    # Account mappings
    revenue_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    contract_asset_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    contract_liability_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
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
