"""
Recurring Template Model.

Defines templates for recurring transactions (invoices, bills, expenses, journals).
"""

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class RecurringEntityType(str, enum.Enum):
    """Type of entity to generate."""

    INVOICE = "INVOICE"
    BILL = "BILL"
    EXPENSE = "EXPENSE"
    JOURNAL = "JOURNAL"


class RecurringFrequency(str, enum.Enum):
    """Frequency of recurrence."""

    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    BIWEEKLY = "BIWEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    SEMI_ANNUALLY = "SEMI_ANNUALLY"
    ANNUALLY = "ANNUALLY"


class RecurringStatus(str, enum.Enum):
    """Status of recurring template."""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class RecurringTemplate(Base):
    """
    Template for recurring transactions.

    Stores all the data needed to generate a transaction on a schedule.
    Can generate AR Invoices, AP Bills, Expenses, or Journal Entries.
    """

    __tablename__ = "recurring_template"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "template_name", name="uq_recurring_template_name"
        ),
        Index("idx_recurring_template_org", "organization_id"),
        Index("idx_recurring_template_next_run", "next_run_date", "status"),
        Index("idx_recurring_template_entity_type", "entity_type"),
        {"schema": "automation"},
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
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

    # Template identification
    template_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Entity type and template data
    entity_type: Mapped[RecurringEntityType] = mapped_column(
        Enum(RecurringEntityType, name="recurring_entity_type"),
        nullable=False,
    )
    template_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Complete data to generate the entity (customer, lines, amounts, etc.)",
    )

    # Schedule configuration
    frequency: Mapped[RecurringFrequency] = mapped_column(
        Enum(RecurringFrequency, name="recurring_frequency"),
        nullable=False,
    )
    schedule_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
        comment="day_of_month, day_of_week, months, etc.",
    )

    # Date range
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_run_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Occurrence limits
    occurrences_limit: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Max number of occurrences (null = unlimited)",
    )
    occurrences_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Last generation info
    last_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_generated_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="ID of the last generated entity",
    )

    # Automation settings
    auto_post: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Automatically post generated entities",
    )
    auto_send: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Automatically send invoice emails",
    )
    days_before_due: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=30,
        comment="Days before due date for invoices/bills",
    )

    # Notification settings
    notify_on_generation: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    notify_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # Status
    status: Mapped[RecurringStatus] = mapped_column(
        Enum(RecurringStatus, name="recurring_status"),
        nullable=False,
        default=RecurringStatus.ACTIVE,
    )

    # Source reference (original entity this was created from)
    source_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Audit
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    logs: Mapped[list["RecurringLog"]] = relationship(
        "RecurringLog",
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="desc(RecurringLog.generated_at)",
    )

    @property
    def is_due(self) -> bool:
        """Check if template is due for generation."""
        if self.status != RecurringStatus.ACTIVE:
            return False
        if self.next_run_date is None:
            return False
        if self.end_date and date.today() > self.end_date:
            return False
        if self.occurrences_limit and self.occurrences_count >= self.occurrences_limit:
            return False
        return date.today() >= self.next_run_date

    @property
    def remaining_occurrences(self) -> int | None:
        """Get remaining occurrences if limited."""
        if self.occurrences_limit is None:
            return None
        return max(0, self.occurrences_limit - self.occurrences_count)


# Forward reference
from app.models.finance.automation.recurring_log import RecurringLog  # noqa: E402
