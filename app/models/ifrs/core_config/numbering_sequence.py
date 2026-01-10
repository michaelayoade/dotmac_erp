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
    ASSET = "ASSET"
    LEASE = "LEASE"
    GOODS_RECEIPT = "GOODS_RECEIPT"


class NumberingSequence(Base):
    """
    Numbering sequence for document generation.
    """

    __tablename__ = "numbering_sequence"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "sequence_type",
            "fiscal_year_id",
            name="uq_sequence",
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

    prefix: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    suffix: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    current_number: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    min_digits: Mapped[int] = mapped_column(Integer, nullable=False, default=6)

    # Reset behavior
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
