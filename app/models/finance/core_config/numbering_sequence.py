"""
Numbering Sequence Model - Core Config.
"""
import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SequenceType(str, enum.Enum):
    INVOICE = "INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"
    PAYMENT = "PAYMENT"
    RECEIPT = "RECEIPT"
    JOURNAL = "JOURNAL"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    SUPPLIER_INVOICE = "SUPPLIER_INVOICE"
    ITEM = "ITEM"
    ASSET = "ASSET"
    LEASE = "LEASE"
    GOODS_RECEIPT = "GOODS_RECEIPT"
    QUOTE = "QUOTE"
    SALES_ORDER = "SALES_ORDER"
    SHIPMENT = "SHIPMENT"
    EXPENSE = "EXPENSE"


class ResetFrequency(str, enum.Enum):
    """When to reset the sequence counter."""
    NEVER = "NEVER"
    YEARLY = "YEARLY"
    MONTHLY = "MONTHLY"


class NumberingSequence(Base):
    """
    Numbering sequence for document generation.

    Supports flexible formatting with patterns like:
    - {PREFIX}{YYYY}{MM}-{SEQ} -> INV202501-0001
    - {PREFIX}-{SEQ} -> INV-000001
    - {PREFIX}{YY}-{SEQ}{SUFFIX} -> QT25-0001A
    """

    __tablename__ = "numbering_sequence"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "sequence_type",
            name="uq_sequence_type",
        ),
        {"schema": "core_config"},
    )

    sequence_id: Mapped[uuid.UUID] = mapped_column(
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

    sequence_type: Mapped[SequenceType] = mapped_column(
        Enum(SequenceType, name="sequence_type"),
        nullable=False,
    )

    # Format components
    prefix: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    suffix: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    separator: Mapped[str] = mapped_column(String(5), nullable=False, default="-")
    min_digits: Mapped[int] = mapped_column(Integer, nullable=False, default=4)

    # Date inclusion
    include_year: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_month: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    year_format: Mapped[int] = mapped_column(Integer, nullable=False, default=4)  # 2 or 4

    # Current sequence tracking
    current_number: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    current_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Reset behavior
    reset_frequency: Mapped[ResetFrequency] = mapped_column(
        Enum(ResetFrequency, name="reset_frequency"),
        nullable=False,
        default=ResetFrequency.MONTHLY,
    )

    # Legacy field (for backward compatibility)
    fiscal_year_reset: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fiscal_year_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    last_used_at: Mapped[Optional[datetime]] = mapped_column(
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

    @property
    def preview(self) -> str:
        """Generate a preview of the number format."""
        from datetime import date
        today = date.today()
        parts = []

        if self.prefix:
            parts.append(self.prefix)

        if self.include_year:
            if self.year_format == 2:
                parts.append(str(today.year)[-2:])
            else:
                parts.append(str(today.year))

        if self.include_month:
            parts.append(f"{today.month:02d}")

        # Add separator before sequence if we have date parts
        if self.include_year or self.include_month:
            seq_part = f"{self.separator}{'0' * self.min_digits}"
        else:
            seq_part = '0' * self.min_digits

        parts.append(seq_part[1:] if seq_part.startswith(self.separator) else seq_part)

        if self.suffix:
            parts.append(self.suffix)

        # Build the format
        if self.include_year or self.include_month:
            # prefix + year + month + separator + seq + suffix
            result = self.prefix
            if self.include_year:
                result += str(today.year) if self.year_format == 4 else str(today.year)[-2:]
            if self.include_month:
                result += f"{today.month:02d}"
            result += self.separator + "0001"
            if self.suffix:
                result += self.suffix
            return result
        else:
            return f"{self.prefix}{'0' * (self.min_digits - 1)}1{self.suffix}"
