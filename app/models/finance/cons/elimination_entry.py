"""
Elimination Entry Model - Consolidation Schema.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class EliminationType(str, enum.Enum):
    INTERCOMPANY_BALANCE = "INTERCOMPANY_BALANCE"
    INTERCOMPANY_TRANSACTION = "INTERCOMPANY_TRANSACTION"
    INVESTMENT_IN_SUBSIDIARY = "INVESTMENT_IN_SUBSIDIARY"
    DIVIDEND = "DIVIDEND"
    UNREALIZED_PROFIT = "UNREALIZED_PROFIT"
    GOODWILL = "GOODWILL"
    NCI_ADJUSTMENT = "NCI_ADJUSTMENT"
    EQUITY_ELIMINATION = "EQUITY_ELIMINATION"
    OTHER = "OTHER"


class EliminationEntry(Base):
    """
    Consolidation elimination entry.
    """

    __tablename__ = "elimination_entry"
    __table_args__ = (
        Index("idx_elim_run", "consolidation_run_id"),
        {"schema": "cons"},
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(
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

    elimination_type: Mapped[EliminationType] = mapped_column(
        Enum(EliminationType, name="elimination_type"),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Related entities (for intercompany eliminations)
    entity_1_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cons.legal_entity.entity_id"),
        nullable=True,
    )
    entity_2_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cons.legal_entity.entity_id"),
        nullable=True,
    )

    # Source reference
    source_balance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Amounts (in reporting currency)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    debit_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    debit_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    credit_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    credit_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # NCI impact
    nci_debit_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    nci_debit_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    nci_credit_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    nci_credit_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Automatic vs manual
    is_automatic: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
