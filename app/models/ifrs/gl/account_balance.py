"""
Account Balance Model - GL Schema.
Derived from posted_ledger_line for query efficiency.
"""
import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BalanceType(str, enum.Enum):
    ACTUAL = "ACTUAL"
    BUDGET = "BUDGET"
    ENCUMBRANCE = "ENCUMBRANCE"
    FORECAST = "FORECAST"


class AccountBalance(Base):
    """
    Pre-aggregated account balances by period.
    Derived from posted_ledger_line.
    """

    __tablename__ = "account_balance"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "account_id",
            "fiscal_period_id",
            "balance_type",
            "business_unit_id",
            "cost_center_id",
            "project_id",
            "segment_id",
            "currency_code",
            name="uq_account_balance",
        ),
        Index("idx_balance_account", "account_id", "fiscal_period_id"),
        Index("idx_balance_lookup", "organization_id", "fiscal_period_id", "balance_type"),
        {"schema": "gl"},
    )

    balance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=False,
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )

    balance_type: Mapped[BalanceType] = mapped_column(
        Enum(BalanceType, name="balance_type"),
        nullable=False,
        default=BalanceType.ACTUAL,
    )

    # Currency
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
    )

    # Dimensions (NULL = all)
    business_unit_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    segment_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Balances
    opening_debit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    opening_credit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    period_debit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    period_credit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    closing_debit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    closing_credit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Net balance (for reporting)
    net_balance: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
        comment="Debit - Credit (positive = debit balance)",
    )
    ytd_net_balance: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Metadata
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    transaction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
