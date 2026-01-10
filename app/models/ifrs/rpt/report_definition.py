"""
Report Definition Model - Reporting Schema.
"""
import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ReportType(str, enum.Enum):
    BALANCE_SHEET = "BALANCE_SHEET"
    INCOME_STATEMENT = "INCOME_STATEMENT"
    CASH_FLOW = "CASH_FLOW"
    CHANGES_IN_EQUITY = "CHANGES_IN_EQUITY"
    TRIAL_BALANCE = "TRIAL_BALANCE"
    GENERAL_LEDGER = "GENERAL_LEDGER"
    SUBLEDGER = "SUBLEDGER"
    AGING = "AGING"
    BUDGET_VS_ACTUAL = "BUDGET_VS_ACTUAL"
    TAX = "TAX"
    REGULATORY = "REGULATORY"
    CUSTOM = "CUSTOM"


class ReportDefinition(Base):
    """
    Report template/definition.
    """

    __tablename__ = "report_definition"
    __table_args__ = (
        UniqueConstraint("organization_id", "report_code", name="uq_report_definition"),
        {"schema": "rpt"},
    )

    report_def_id: Mapped[uuid.UUID] = mapped_column(
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

    report_code: Mapped[str] = mapped_column(String(50), nullable=False)
    report_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    report_type: Mapped[ReportType] = mapped_column(
        Enum(ReportType, name="report_type"),
        nullable=False,
    )

    # Report category/grouping
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    subcategory: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Output formats
    default_format: Mapped[str] = mapped_column(String(20), nullable=False, default="PDF")
    supported_formats: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # Report structure
    report_structure: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    column_definitions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    row_definitions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    filter_definitions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Data source
    data_source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    data_source_config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Template
    template_file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Security
    required_permissions: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    is_system_report: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
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
