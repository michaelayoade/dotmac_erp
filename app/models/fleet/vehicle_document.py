"""
Vehicle Document Model - Fleet Schema.

Tracks vehicle documents: insurance, registration, permits, etc.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.fleet.base import FleetBaseMixin
from app.models.fleet.enums import DocumentType
from app.models.people.base import AuditMixin

if TYPE_CHECKING:
    from app.models.fleet.vehicle import Vehicle


class VehicleDocument(Base, FleetBaseMixin, AuditMixin):
    """
    Vehicle document record.

    Tracks important documents associated with a vehicle:
    - Insurance policies
    - Registration certificates
    - Inspection certificates
    - Permits and licenses

    Supports expiry tracking and reminder notifications.
    """

    __tablename__ = "vehicle_document"
    __table_args__ = (
        Index("idx_fleet_doc_vehicle_type", "vehicle_id", "document_type"),
        Index("idx_fleet_doc_expiry", "organization_id", "expiry_date"),
        Index(
            "idx_fleet_doc_reminder", "organization_id", "reminder_sent", "expiry_date"
        ),
        {"schema": "fleet"},
    )

    # Primary key
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Reference
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fleet.vehicle.vehicle_id"),
        nullable=False,
    )

    # Document details
    document_type: Mapped[DocumentType] = mapped_column(
        nullable=False,
    )
    document_number: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Policy number, certificate number, etc.",
    )
    description: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    # Validity period
    issue_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    expiry_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    # Insurance-specific fields
    provider_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Insurance company or issuing authority",
    )
    policy_number: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    coverage_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
        comment="Insurance coverage limit",
    )
    premium_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
        comment="Premium paid",
    )

    # File attachment
    file_path: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Path to uploaded document file",
    )
    file_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    # Reminder settings
    reminder_days_before: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=30,
        comment="Days before expiry to send reminder",
    )
    reminder_sent: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether expiry reminder has been sent",
    )

    # Notes
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    vehicle: Mapped["Vehicle"] = relationship(
        "Vehicle",
        back_populates="documents",
    )

    @property
    def is_expired(self) -> bool:
        """Check if document is expired."""
        if not self.expiry_date:
            return False
        return date.today() > self.expiry_date

    @property
    def expires_soon(self) -> bool:
        """Check if document expires within reminder period."""
        if not self.expiry_date:
            return False
        days_until = (self.expiry_date - date.today()).days
        return 0 < days_until <= self.reminder_days_before

    @property
    def days_until_expiry(self) -> int | None:
        """Get days until expiry (negative if expired)."""
        if not self.expiry_date:
            return None
        return (self.expiry_date - date.today()).days

    @property
    def status_label(self) -> str:
        """Get human-readable status."""
        if self.is_expired:
            return "Expired"
        if self.expires_soon:
            return f"Expires in {self.days_until_expiry} days"
        if self.expiry_date:
            return "Valid"
        return "No expiry"
