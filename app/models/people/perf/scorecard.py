"""
Scorecard Model - Performance Schema.

Employee performance scorecards and balanced scorecards.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee


class Scorecard(Base, AuditMixin, ERPNextSyncMixin):
    """
    Scorecard - employee performance scorecard.

    Aggregates KPIs and appraisal results into a summary view.
    """

    __tablename__ = "scorecard"
    __table_args__ = (
        Index("idx_scorecard_employee", "employee_id"),
        Index("idx_scorecard_period", "organization_id", "period_start", "period_end"),
        {"schema": "perf"},
    )

    scorecard_id: Mapped[uuid.UUID] = mapped_column(
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

    # Employee
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )

    # Period
    period_start: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    period_end: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    period_label: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Q1 2024, FY 2024, etc.",
    )

    # Scores by perspective (Balanced Scorecard)
    financial_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Financial/Revenue metrics",
    )
    customer_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Customer satisfaction metrics",
    )
    process_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Internal process metrics",
    )
    learning_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Learning & growth metrics",
    )

    # Overall
    overall_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )
    overall_rating: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    rating_label: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    # Trend
    previous_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )
    score_change: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="overall_score - previous_score",
    )

    # Notes
    summary: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Status
    is_finalized: Mapped[bool] = mapped_column(
        default=False,
    )
    finalized_on: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    employee: Mapped["Employee"] = relationship("Employee")
    items: Mapped[list["ScorecardItem"]] = relationship(
        "ScorecardItem",
        back_populates="scorecard",
    )

    def __repr__(self) -> str:
        return f"<Scorecard {self.employee_id} {self.period_label}>"


class ScorecardItem(Base):
    """
    Scorecard Item - individual metric within a scorecard.
    """

    __tablename__ = "scorecard_item"
    __table_args__ = (
        Index("idx_scorecard_item_scorecard", "scorecard_id"),
        {"schema": "perf"},
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
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

    # Scorecard
    scorecard_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.scorecard.scorecard_id"),
        nullable=False,
    )

    # Item details
    perspective: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="FINANCIAL, CUSTOMER, PROCESS, LEARNING",
    )
    metric_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Target & Actual
    target_value: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    actual_value: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    unit_of_measure: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
    )

    # Score
    weightage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("0.00"),
    )
    score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )
    weighted_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    # Status
    status: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="ON_TRACK, AT_RISK, OFF_TRACK, ACHIEVED",
    )

    # Order
    sequence: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    scorecard: Mapped["Scorecard"] = relationship(
        "Scorecard",
        back_populates="items",
    )

    def __repr__(self) -> str:
        return f"<ScorecardItem {self.metric_name}>"
