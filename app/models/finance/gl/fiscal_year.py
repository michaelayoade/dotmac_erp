"""
Fiscal Year Model - GL Schema.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class FiscalYear(Base):
    """
    Fiscal year definition.
    """

    __tablename__ = "fiscal_year"
    __table_args__ = (
        UniqueConstraint("organization_id", "year_code", name="uq_fiscal_year"),
        {"schema": "gl"},
    )

    fiscal_year_id: Mapped[uuid.UUID] = mapped_column(
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

    year_code: Mapped[str] = mapped_column(String(10), nullable=False)
    year_name: Mapped[str] = mapped_column(String(50), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    is_adjustment_year: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    retained_earnings_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    periods: Mapped[list["FiscalPeriod"]] = relationship(
        "FiscalPeriod",
        back_populates="fiscal_year",
    )


# Forward reference
from app.models.finance.gl.fiscal_period import FiscalPeriod  # noqa: E402
