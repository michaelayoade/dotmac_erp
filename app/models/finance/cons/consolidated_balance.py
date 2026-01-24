"""
Consolidated Balance Model - Consolidation Schema.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ConsolidatedBalance(Base):
    """
    Consolidated balance per account.
    """

    __tablename__ = "consolidated_balance"
    __table_args__ = (
        UniqueConstraint(
            "consolidation_run_id",
            "account_id",
            "segment_id",
            name="uq_consolidated_balance",
        ),
        Index("idx_cons_balance_run", "consolidation_run_id"),
        {"schema": "cons"},
    )

    balance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    consolidation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cons.consolidation_run.run_id"),
        nullable=False,
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    segment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    # Sum of subsidiary balances (before elimination)
    subsidiary_balance_sum: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Equity method investments
    equity_method_balance: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Eliminations
    intercompany_eliminations: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    investment_eliminations: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    unrealized_profit_eliminations: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    other_eliminations: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    total_eliminations: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Translation adjustment
    translation_adjustment: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # NCI (for equity accounts)
    nci_share: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Consolidated balance
    consolidated_balance: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Parent share
    parent_share: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
