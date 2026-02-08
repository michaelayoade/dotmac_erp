"""
Remita RRR Model.

Represents a Remita Retrieval Reference for tracking government payment transactions.
Used for PAYE, NHF, Pension, NSITF, taxes, procurement fees, and other statutory payments.
"""

import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as SAUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.person import Person


class RRRStatus(str, enum.Enum):
    """Status of the RRR payment."""

    pending = "pending"
    paid = "paid"
    expired = "expired"
    failed = "failed"
    cancelled = "cancelled"


class RemitaRRR(Base):
    """
    Remita Retrieval Reference (RRR) entity.

    Stores generated RRRs for tracking government and statutory payments.
    Each RRR is linked to a specific biller/service and can optionally
    reference a source entity (e.g., payroll run, tax return).
    """

    __tablename__ = "remita_rrr"
    __table_args__ = (
        UniqueConstraint("rrr", name="uq_remita_rrr_number"),
        Index("ix_remita_rrr_org_status", "organization_id", "status"),
        Index("ix_remita_rrr_source", "source_type", "source_id"),
        {"schema": "payments"},
    )

    # Primary key
    id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # Organization (multi-tenant)
    organization_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # The RRR itself
    rrr: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
        index=True,
    )
    order_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    # Payer information
    payer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    payer_email: Mapped[str] = mapped_column(String(255), nullable=False)
    payer_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Remita biller/service details
    biller_id: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )  # e.g., "FIRS", "FMBN", "BPP"
    biller_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )  # e.g., "Federal Inland Revenue Service"
    service_type_id: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # Remita service code
    service_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )  # e.g., "PAYE Tax"

    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Generic source linking (caller decides what to put)
    source_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )  # e.g., "payroll_paye", "stamp_duty", "pension"
    source_id: Mapped[UUID | None] = mapped_column(
        SAUUID(as_uuid=True),
        nullable=True,
    )

    # Status
    status: Mapped[RRRStatus] = mapped_column(
        Enum(RRRStatus, name="rrr_status", schema="payments"),
        nullable=False,
        default=RRRStatus.pending,
    )

    # Timestamps
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Payment info (populated when paid)
    payment_reference: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    payment_channel: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )  # e.g., "Bank", "Card", "USSD"

    # API response (for debugging/audit)
    api_response: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    last_status_check: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_status_response: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Audit fields
    created_by_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Relationships
    created_by: Mapped[Optional["Person"]] = relationship(
        "Person",
        foreign_keys=[created_by_id],
        lazy="joined",
    )

    def __repr__(self) -> str:
        return f"<RemitaRRR {self.rrr} - {self.biller_id}/{self.service_name}>"

    @property
    def is_pending(self) -> bool:
        """Check if RRR is still pending payment."""
        return self.status == RRRStatus.pending

    @property
    def is_paid(self) -> bool:
        """Check if RRR has been paid."""
        return self.status == RRRStatus.paid

    @property
    def is_expired(self) -> bool:
        """Check if RRR has expired."""
        if self.status == RRRStatus.expired:
            return True
        return bool(self.expires_at and datetime.utcnow() > self.expires_at)
