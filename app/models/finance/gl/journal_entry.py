"""
Journal Entry Model - GL Schema.
Document 07: Journal entries and posting.
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.mixins import VersionedMixin


class JournalType(str, enum.Enum):
    STANDARD = "STANDARD"
    ADJUSTMENT = "ADJUSTMENT"
    CLOSING = "CLOSING"
    OPENING = "OPENING"
    REVERSAL = "REVERSAL"
    RECURRING = "RECURRING"
    INTERCOMPANY = "INTERCOMPANY"
    REVALUATION = "REVALUATION"
    CONSOLIDATION = "CONSOLIDATION"


class JournalStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    POSTED = "POSTED"
    REVERSED = "REVERSED"
    VOID = "VOID"


class JournalEntry(Base, VersionedMixin):
    """
    Journal entry header.
    Document 07: Immutable after posting.

    Includes optimistic locking via version field (VersionedMixin).
    """

    __tablename__ = "journal_entry"
    __table_args__ = (
        UniqueConstraint("organization_id", "journal_number", name="uq_journal_number"),
        Index("idx_journal_status", "organization_id", "status"),
        Index("idx_journal_period", "fiscal_period_id"),
        Index("idx_journal_correlation", "correlation_id"),
        {"schema": "gl"},
    )

    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
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

    # Identity
    journal_number: Mapped[str] = mapped_column(String(30), nullable=False)
    journal_type: Mapped[JournalType] = mapped_column(
        Enum(JournalType, name="journal_type"),
        nullable=False,
    )

    # Dates
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    posting_date: Mapped[date] = mapped_column(Date, nullable=False)
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )

    # Description
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Currency
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(
        Numeric(20, 10),
        nullable=False,
        default=1.0,
    )
    exchange_rate_type_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Totals
    total_debit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    total_credit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    total_debit_functional: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    total_credit_functional: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Status
    status: Mapped[JournalStatus] = mapped_column(
        Enum(JournalStatus, name="journal_status"),
        nullable=False,
        default=JournalStatus.DRAFT,
    )
    posting_batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Reversal
    is_reversal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reversed_journal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.journal_entry.journal_entry_id"),
        nullable=True,
    )
    reversal_journal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    auto_reverse_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Source
    source_module: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="AR, AP, FA, LEASE, INV, TAX, FIN_INST, CONS",
    )
    source_document_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    source_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Intercompany
    is_intercompany: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    intercompany_org_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    matching_journal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # SoD tracking
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    submitted_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    posted_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    posted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    approval_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Audit
    correlation_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    lines: Mapped[list["JournalEntryLine"]] = relationship(
        "JournalEntryLine",
        back_populates="journal_entry",
        cascade="all, delete-orphan",
    )
    reversed_journal: Mapped[Optional["JournalEntry"]] = relationship(
        "JournalEntry",
        remote_side=[journal_entry_id],
        foreign_keys=[reversed_journal_id],
    )


# Forward reference
from app.models.finance.gl.journal_entry_line import JournalEntryLine  # noqa: E402
