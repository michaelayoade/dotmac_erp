"""
Payment Terms Model - AR Schema.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PaymentTerms(Base):
    """
    Payment terms definition.
    """

    __tablename__ = "payment_terms"
    __table_args__ = (
        UniqueConstraint("organization_id", "terms_code", name="uq_payment_terms"),
        {"schema": "ar"},
    )

    payment_terms_id: Mapped[uuid.UUID] = mapped_column(
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

    terms_code: Mapped[str] = mapped_column(String(20), nullable=False)
    terms_name: Mapped[str] = mapped_column(String(100), nullable=False)
    due_days: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    discount_percentage: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
