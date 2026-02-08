"""
Vendor Prequalification Model - proc Schema.

Vendor eligibility assessment and compliance tracking.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Index,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.procurement.base import ProcurementBaseMixin
from app.models.procurement.enums import PrequalificationStatus


class VendorPrequalification(Base, ProcurementBaseMixin):
    """
    Vendor prequalification record.

    Assesses vendor eligibility based on statutory compliance
    (tax clearance, pension, ITF, NSITF), financial capability,
    and technical competence per Nigerian procurement regulations.
    """

    __tablename__ = "vendor_prequalification"
    __table_args__ = (
        Index("idx_proc_preq_supplier", "supplier_id"),
        Index("idx_proc_preq_status", "organization_id", "status"),
        Index("idx_proc_preq_validity", "organization_id", "valid_from", "valid_to"),
        {"schema": "proc"},
    )

    prequalification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="ap.supplier",
    )
    application_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    status: Mapped[PrequalificationStatus] = mapped_column(
        default=PrequalificationStatus.PENDING,
    )
    categories: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Qualified procurement categories",
    )
    valid_from: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    valid_to: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    # Statutory compliance checks
    documents_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    tax_clearance_valid: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    pension_compliance: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    itf_compliance: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Industrial Training Fund compliance",
    )
    nsitf_compliance: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Nigeria Social Insurance Trust Fund compliance",
    )

    # Capability scores
    financial_capability_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )
    technical_capability_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )
    overall_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    # Review
    review_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Blacklisting
    blacklisted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    blacklist_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
