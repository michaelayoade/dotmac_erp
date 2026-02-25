"""
Fiscal Position Models - Tax Schema.

Fiscal positions automatically remap tax codes and GL accounts based on
partner characteristics (customer/supplier type, jurisdiction).
For example, a "Government Customer" fiscal position can remap
VAT 7.5% → VAT 0% (zero-rated) on all invoice lines.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class FiscalPosition(Base):
    """
    Fiscal position defines tax/account mapping rules for a partner category.

    When auto_apply is True, the system matches partners by customer_type,
    supplier_type, country_code, and state_code. The lowest priority value
    wins when multiple positions match.
    """

    __tablename__ = "fiscal_position"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_fiscal_position_name"),
        {"schema": "tax"},
    )

    fiscal_position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_apply: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    country_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    customer_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    supplier_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tax_maps: Mapped[list[FiscalPositionTaxMap]] = relationship(
        "FiscalPositionTaxMap",
        back_populates="fiscal_position",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    account_maps: Mapped[list[FiscalPositionAccountMap]] = relationship(
        "FiscalPositionAccountMap",
        back_populates="fiscal_position",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class FiscalPositionTaxMap(Base):
    """
    Maps a source tax code to a destination tax code within a fiscal position.

    If tax_dest_id is NULL, the source tax is removed (tax exemption).
    """

    __tablename__ = "fiscal_position_tax_map"
    __table_args__ = ({"schema": "tax"},)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    fiscal_position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.fiscal_position.fiscal_position_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tax_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.tax_code.tax_code_id"),
        nullable=False,
    )
    tax_dest_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.tax_code.tax_code_id"),
        nullable=True,
    )

    # Relationships
    fiscal_position: Mapped[FiscalPosition] = relationship(
        "FiscalPosition", back_populates="tax_maps"
    )


class FiscalPositionAccountMap(Base):
    """
    Maps a source GL account to a destination GL account within a fiscal position.

    Used to remap revenue/expense accounts for specific partner categories
    (e.g., export revenue account instead of domestic revenue).
    """

    __tablename__ = "fiscal_position_account_map"
    __table_args__ = ({"schema": "tax"},)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    fiscal_position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.fiscal_position.fiscal_position_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=False,
    )
    account_dest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=False,
    )

    # Relationships
    fiscal_position: Mapped[FiscalPosition] = relationship(
        "FiscalPosition", back_populates="account_maps"
    )
