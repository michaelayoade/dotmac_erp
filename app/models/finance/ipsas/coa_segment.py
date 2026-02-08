"""
Chart of Accounts Segment Models - IPSAS Schema.
Government CoA segment definitions and valid values.
Overlay on existing gl.Account.account_code for validation/parsing.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.finance.ipsas.enums import CoASegmentType


class CoASegmentDefinition(Base):
    """
    Defines the structure of government chart of accounts segments.
    Up to 6 segments: Administrative, Economic, Fund, Functional, Program, Project.
    """

    __tablename__ = "coa_segment_definition"
    __table_args__ = (
        UniqueConstraint("organization_id", "segment_type", name="uq_coa_segment_def"),
        Index("idx_coa_seg_def_org", "organization_id"),
        {"schema": "ipsas"},
    )

    segment_def_id: Mapped[uuid.UUID] = mapped_column(
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

    # Segment definition
    segment_type: Mapped[CoASegmentType] = mapped_column(
        Enum(CoASegmentType, name="coa_segment_type", schema="ipsas"),
        nullable=False,
    )
    segment_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Position in account code string
    code_position_start: Mapped[int] = mapped_column(Integer, nullable=False)
    code_length: Mapped[int] = mapped_column(Integer, nullable=False)
    separator: Mapped[str] = mapped_column(String(1), nullable=False, default="-")

    # Config
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    values: Mapped[list["CoASegmentValue"]] = relationship(
        "CoASegmentValue",
        back_populates="segment_definition",
        cascade="all, delete-orphan",
    )


class CoASegmentValue(Base):
    """
    Valid values for a CoA segment type.
    Hierarchical (parent_segment_value_id) for drill-down.
    """

    __tablename__ = "coa_segment_value"
    __table_args__ = (
        UniqueConstraint("segment_def_id", "segment_code", name="uq_coa_segment_value"),
        Index("idx_coa_seg_val_def", "segment_def_id"),
        Index("idx_coa_seg_val_org", "organization_id"),
        {"schema": "ipsas"},
    )

    segment_value_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    segment_def_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ipsas.coa_segment_definition.segment_def_id"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )

    # Value
    segment_code: Mapped[str] = mapped_column(String(20), nullable=False)
    segment_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Hierarchy
    parent_segment_value_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ipsas.coa_segment_value.segment_value_id"),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    segment_definition: Mapped["CoASegmentDefinition"] = relationship(
        "CoASegmentDefinition",
        back_populates="values",
    )
    parent_value: Mapped[Optional["CoASegmentValue"]] = relationship(
        "CoASegmentValue",
        remote_side=[segment_value_id],
        foreign_keys=[parent_segment_value_id],
    )
