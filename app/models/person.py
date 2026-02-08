import enum
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Gender(enum.Enum):
    unknown = "unknown"
    female = "female"
    male = "male"
    non_binary = "non_binary"
    other = "other"


class ContactMethod(enum.Enum):
    email = "email"
    phone = "phone"
    sms = "sms"
    push = "push"


class PersonStatus(enum.Enum):
    active = "active"
    inactive = "inactive"
    archived = "archived"


class Person(Base):
    __tablename__ = "people"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Multi-tenant: links person to their organization (required for tenant isolation)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    bio: Mapped[str | None] = mapped_column(Text)

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    phone: Mapped[str | None] = mapped_column(String(40))

    date_of_birth: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[Gender] = mapped_column(Enum(Gender), default=Gender.unknown)

    preferred_contact_method: Mapped[ContactMethod | None] = mapped_column(
        Enum(ContactMethod)
    )
    locale: Mapped[str | None] = mapped_column(String(16))
    timezone: Mapped[str | None] = mapped_column(String(64))

    address_line1: Mapped[str | None] = mapped_column(String(120))
    address_line2: Mapped[str | None] = mapped_column(String(120))
    city: Mapped[str | None] = mapped_column(String(80))
    region: Mapped[str | None] = mapped_column(String(80))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country_code: Mapped[str | None] = mapped_column(String(2))

    status: Mapped[PersonStatus] = mapped_column(
        Enum(PersonStatus), default=PersonStatus.active
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)

    notes: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", MutableDict.as_mutable(JSON)
    )

    # Batch operation tracking (for audit trail of bulk imports/scripts)
    batch_operation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("batch_operations.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    @property
    def name(self) -> str:
        """Get full name for display."""
        if self.display_name:
            return self.display_name
        return f"{self.first_name} {self.last_name}".strip()
