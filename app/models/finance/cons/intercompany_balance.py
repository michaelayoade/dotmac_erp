"""
Intercompany Balance Model - Consolidation Schema.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
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


class IntercompanyBalance(Base):
    """
    Intercompany balance between entities.
    """

    __tablename__ = "intercompany_balance"
    __table_args__ = (
        UniqueConstraint(
            "fiscal_period_id",
            "from_entity_id",
            "to_entity_id",
            "balance_type",
            name="uq_intercompany_balance",
        ),
        Index("idx_ic_balance_period", "fiscal_period_id"),
        {"schema": "cons"},
    )

    balance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )
    balance_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Entities
    from_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cons.legal_entity.entity_id"),
        nullable=False,
    )
    to_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cons.legal_entity.entity_id"),
        nullable=False,
    )

    # Balance details
    balance_type: Mapped[str] = mapped_column(String(50), nullable=False)
    balance_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # From entity perspective
    from_entity_gl_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    from_entity_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    from_entity_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    from_entity_functional_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )

    # To entity perspective
    to_entity_gl_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    to_entity_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    to_entity_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    to_entity_functional_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )

    # Reporting currency
    reporting_currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    reporting_currency_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )

    # Matching
    is_matched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    difference_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    difference_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Elimination
    is_eliminated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    elimination_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
