"""
Financial Statement Line Model - Reporting Schema.
"""
import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class StatementType(str, enum.Enum):
    BALANCE_SHEET = "BALANCE_SHEET"
    INCOME_STATEMENT = "INCOME_STATEMENT"
    CASH_FLOW = "CASH_FLOW"
    CHANGES_IN_EQUITY = "CHANGES_IN_EQUITY"
    COMPREHENSIVE_INCOME = "COMPREHENSIVE_INCOME"


class FinancialStatementLine(Base):
    """
    Financial statement line item definition.
    """

    __tablename__ = "financial_statement_line"
    __table_args__ = (
        UniqueConstraint("organization_id", "statement_type", "line_code", name="uq_fs_line"),
        {"schema": "rpt"},
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
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

    statement_type: Mapped[StatementType] = mapped_column(
        Enum(StatementType, name="statement_type"),
        nullable=False,
    )
    line_code: Mapped[str] = mapped_column(String(50), nullable=False)
    line_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Hierarchy
    parent_line_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rpt.financial_statement_line.line_id"),
        nullable=True,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    indent_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Line type
    is_header: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_total: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_subtotal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_separator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Calculation
    calculation_type: Mapped[str] = mapped_column(String(30), nullable=False, default="SUM_ACCOUNTS")
    calculation_formula: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Account mapping
    account_codes: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    account_categories: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    exclude_account_codes: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # Sign convention
    normal_balance: Mapped[str] = mapped_column(String(10), nullable=False, default="DEBIT")
    display_sign: Mapped[str] = mapped_column(String(20), nullable=False, default="NATURAL")

    # XBRL mapping
    xbrl_element: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Formatting
    formatting: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

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
