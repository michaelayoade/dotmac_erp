"""
Journal Entry Line Model - GL Schema.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class JournalEntryLine(Base):
    """
    Journal entry line item.
    """

    __tablename__ = "journal_entry_line"
    __table_args__ = (
        Index("idx_jel_account", "account_id"),
        Index("idx_jel_dimensions", "cost_center_id", "project_id", "segment_id"),
        {"schema": "gl"},
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.journal_entry.journal_entry_id"),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Account
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=False,
    )

    # Description
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Amounts (original currency)
    debit_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    credit_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Amounts (functional currency)
    debit_amount_functional: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    credit_amount_functional: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Multi-currency
    currency_code: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    exchange_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 10), nullable=True
    )

    # Dimensions
    business_unit_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    segment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Reconciliation
    reconciliation_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    journal_entry: Mapped["JournalEntry"] = relationship(
        "JournalEntry",
        back_populates="lines",
    )
    account: Mapped["Account"] = relationship("Account")


# Forward references
from app.models.finance.gl.journal_entry import JournalEntry  # noqa: E402
from app.models.finance.gl.account import Account  # noqa: E402
