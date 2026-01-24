"""
Expected Credit Loss Model - AR Schema.
IFRS 9 ECL.
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Index, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ECLMethodology(str, enum.Enum):
    SIMPLIFIED = "SIMPLIFIED"
    GENERAL = "GENERAL"
    PURCHASED_CREDIT_IMPAIRED = "PURCHASED_CREDIT_IMPAIRED"


class ECLStage(str, enum.Enum):
    STAGE_1 = "STAGE_1"
    STAGE_2 = "STAGE_2"
    STAGE_3 = "STAGE_3"


class ExpectedCreditLoss(Base):
    """
    IFRS 9 Expected Credit Loss calculation.
    """

    __tablename__ = "expected_credit_loss"
    __table_args__ = (
        Index("idx_ecl_org_period", "organization_id", "fiscal_period_id"),
        Index("idx_ecl_customer", "customer_id"),
        {"schema": "ar"},
    )

    ecl_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    calculation_date: Mapped[date] = mapped_column(Date, nullable=False)
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Methodology
    methodology: Mapped[ECLMethodology] = mapped_column(
        Enum(ECLMethodology, name="ecl_methodology"),
        nullable=False,
        default=ECLMethodology.SIMPLIFIED,
    )

    # Scope (NULL customer_id = portfolio approach)
    customer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.customer.customer_id"),
        nullable=True,
    )
    portfolio_segment: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Aging bucket
    aging_bucket: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
        comment="CURRENT, 1_30_DAYS, 31_60_DAYS, etc.",
    )

    # ECL inputs
    gross_carrying_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    historical_loss_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 5),
        nullable=True,
    )
    forward_looking_adjustment: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 5),
        nullable=True,
    )

    # PD/LGD/EAD model (for general approach)
    probability_of_default: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 5),
        nullable=True,
    )
    loss_given_default: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 5),
        nullable=True,
    )
    exposure_at_default: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )

    # ECL output
    ecl_12_month: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)
    ecl_lifetime: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)
    ecl_stage: Mapped[ECLStage] = mapped_column(
        Enum(ECLStage, name="ecl_stage"),
        nullable=False,
        default=ECLStage.STAGE_1,
    )

    # SICR
    credit_risk_rating: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    significant_increase_indicator: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Provision
    provision_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    provision_movement: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Accounting
    journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    posting_batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    calculation_details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
