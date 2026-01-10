"""
Hedge Relationship Model - Financial Instruments Schema.
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class HedgeType(str, enum.Enum):
    FAIR_VALUE = "FAIR_VALUE"
    CASH_FLOW = "CASH_FLOW"
    NET_INVESTMENT = "NET_INVESTMENT"


class HedgeStatus(str, enum.Enum):
    DESIGNATED = "DESIGNATED"
    ACTIVE = "ACTIVE"
    DISCONTINUED = "DISCONTINUED"
    MATURED = "MATURED"


class HedgeRelationship(Base):
    """
    Hedge relationship for hedge accounting (IFRS 9).
    """

    __tablename__ = "hedge_relationship"
    __table_args__ = (
        UniqueConstraint("organization_id", "hedge_code", name="uq_hedge_relationship"),
        {"schema": "fin_inst"},
    )

    hedge_id: Mapped[uuid.UUID] = mapped_column(
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

    hedge_code: Mapped[str] = mapped_column(String(50), nullable=False)
    hedge_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    hedge_type: Mapped[HedgeType] = mapped_column(
        Enum(HedgeType, name="hedge_type"),
        nullable=False,
    )

    # Hedging instrument
    hedging_instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fin_inst.financial_instrument.instrument_id"),
        nullable=False,
    )
    hedging_instrument_proportion: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        nullable=False,
        default=1,
    )

    # Hedged item (can be a financial instrument or other exposure)
    hedged_item_type: Mapped[str] = mapped_column(String(50), nullable=False)
    hedged_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    hedged_item_description: Mapped[str] = mapped_column(Text, nullable=False)
    hedged_risk: Mapped[str] = mapped_column(String(100), nullable=False)

    # Hedge ratio
    hedge_ratio: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=1)

    # Dates
    designation_date: Mapped[date] = mapped_column(Date, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    termination_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Status
    status: Mapped[HedgeStatus] = mapped_column(
        Enum(HedgeStatus, name="hedge_status"),
        nullable=False,
        default=HedgeStatus.DESIGNATED,
    )

    # Prospective effectiveness
    prospective_test_method: Mapped[str] = mapped_column(String(50), nullable=False)
    prospective_test_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Retrospective effectiveness
    retrospective_test_method: Mapped[str] = mapped_column(String(50), nullable=False)

    # Cash flow hedge reserve (for cash flow hedges)
    cash_flow_hedge_reserve: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Cost of hedging reserve
    cost_of_hedging_reserve: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Documentation reference
    documentation_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Audit
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
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
