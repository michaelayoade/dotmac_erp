"""
Posted Ledger Line Model - GL Schema.
Document 07: Append-only, immutable ledger.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PostedLedgerLine(Base):
    """
    Immutable posted ledger line.
    Document 07: APPEND-ONLY - never UPDATE or DELETE.
    Partitioned by posting_year for performance.
    """

    __tablename__ = "posted_ledger_line"
    __table_args__ = (
        Index(
            "idx_pll_account_period",
            "organization_id",
            "account_id",
            "fiscal_period_id",
        ),
        Index("idx_pll_journal", "journal_entry_id"),
        Index("idx_pll_posting_date", "posting_date"),
        Index(
            "idx_pll_dimensions",
            "cost_center_id",
            "project_id",
            "segment_id",
            postgresql_where="cost_center_id IS NOT NULL OR project_id IS NOT NULL OR segment_id IS NOT NULL",
        ),
        {"schema": "gl"},
    )

    # Composite primary key for partitioning
    ledger_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    posting_year: Mapped[int] = mapped_column(
        nullable=False,
        primary_key=True,
        comment="Partition key",
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    journal_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    posting_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Account
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    account_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Denormalized for query performance",
    )

    # Dates
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    posting_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Description
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    journal_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Amounts (functional currency)
    debit_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    credit_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Original currency
    original_currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    original_debit_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    original_credit_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    exchange_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 10), nullable=True
    )

    # Dimensions
    business_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Source tracking
    source_module: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_document_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Audit
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    posted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
