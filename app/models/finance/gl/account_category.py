"""
Account Category Model - GL Schema.
IFRS classification hierarchy.
"""

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
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
from app.models.mixins import ERPNextSyncMixin


class IFRSCategory(str, enum.Enum):
    ASSETS = "ASSETS"
    LIABILITIES = "LIABILITIES"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSES = "EXPENSES"
    OTHER_COMPREHENSIVE_INCOME = "OTHER_COMPREHENSIVE_INCOME"


class AccountCategory(Base, ERPNextSyncMixin):
    """
    Account category for chart of accounts hierarchy.
    """

    __tablename__ = "account_category"
    __table_args__ = (
        UniqueConstraint("organization_id", "category_code", name="uq_category_code"),
        {"schema": "gl"},
    )

    category_id: Mapped[uuid.UUID] = mapped_column(
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

    category_code: Mapped[str] = mapped_column(String(20), nullable=False)
    category_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # IFRS classification
    ifrs_category: Mapped[IFRSCategory] = mapped_column(
        Enum(IFRSCategory, name="ifrs_category"),
        nullable=False,
    )

    # Hierarchy
    parent_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account_category.category_id"),
        nullable=True,
    )
    hierarchy_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Statement mapping
    financial_statement_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    parent_category: Mapped[Optional["AccountCategory"]] = relationship(
        "AccountCategory",
        remote_side=[category_id],
        foreign_keys=[parent_category_id],
    )
    child_categories: Mapped[list["AccountCategory"]] = relationship(
        "AccountCategory",
        back_populates="parent_category",
    )
    accounts: Mapped[list["Account"]] = relationship(
        "Account",
        back_populates="category",
    )


# Forward reference
from app.models.finance.gl.account import Account  # noqa: E402
