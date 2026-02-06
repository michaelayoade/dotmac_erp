"""
Employee Info Change Request Model - Pending employee data updates.

Stores pending changes to employee bank/tax information that require
approval before being applied. Supports the statutory exports feature
by ensuring data changes go through proper review.
"""

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.person import Person


class InfoChangeType(str, enum.Enum):
    """Type of information being changed."""

    BANK_DETAILS = "BANK_DETAILS"  # Bank account number, name, branch
    TAX_INFO = "TAX_INFO"  # TIN, tax state
    PENSION_INFO = "PENSION_INFO"  # RSA PIN, PFA code
    NHF_INFO = "NHF_INFO"  # NHF number
    COMBINED = "COMBINED"  # Multiple types in one request


class InfoChangeStatus(str, enum.Enum):
    """Status of a change request."""

    PENDING = "PENDING"  # Awaiting approval
    APPROVED = "APPROVED"  # Approved and changes applied
    REJECTED = "REJECTED"  # Rejected, not applied
    CANCELLED = "CANCELLED"  # Cancelled by requester
    EXPIRED = "EXPIRED"  # Auto-expired after time limit


class EmployeeInfoChangeRequest(Base):
    """
    Employee Info Change Request - Pending employee data updates.

    When employees update their bank details, tax info, or pension info
    through self-service, changes are stored here for approval before
    being applied to the actual employee/tax_profile records.

    Fields stored in proposed_changes and previous_values as JSON:
    - Bank: bank_name, bank_account_number, bank_account_name, bank_branch_code
    - Tax: tin, tax_state
    - Pension: rsa_pin, pfa_code
    - NHF: nhf_number
    """

    __tablename__ = "employee_info_change_request"
    __table_args__ = (
        Index("idx_info_change_request_org", "organization_id"),
        Index("idx_info_change_request_employee", "employee_id"),
        Index("idx_info_change_request_status", "organization_id", "status"),
        Index(
            "idx_info_change_request_pending", "organization_id", "status", "created_at"
        ),
        {"schema": "hr"},
    )

    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id", ondelete="CASCADE"),
        nullable=False,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Request details
    change_type: Mapped[InfoChangeType] = mapped_column(
        Enum(InfoChangeType, name="info_change_type", schema="hr"),
        nullable=False,
    )
    status: Mapped[InfoChangeStatus] = mapped_column(
        Enum(InfoChangeStatus, name="info_change_status", schema="hr"),
        default=InfoChangeStatus.PENDING,
        nullable=False,
    )

    # The actual changes
    proposed_changes: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Proposed new values as JSON",
    )
    previous_values: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Previous values before change (for audit)",
    )

    # Request metadata
    requester_notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Notes from employee explaining the change",
    )

    # Approval metadata
    reviewer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id", ondelete="SET NULL"),
        nullable=True,
        comment="Person who reviewed (approved/rejected) the request",
    )
    reviewer_notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Notes from reviewer",
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Timestamps
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
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the request expires if not actioned",
    )

    # Relationships
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )
    reviewer: Mapped[Optional["Person"]] = relationship(
        "Person",
        foreign_keys=[reviewer_id],
    )

    @property
    def is_actionable(self) -> bool:
        """Check if request can still be approved/rejected."""
        if self.status != InfoChangeStatus.PENDING:
            return False
        if self.expires_at:
            # Normalize both to UTC for comparison
            now_utc = datetime.now(timezone.utc)
            expires_utc = (
                self.expires_at.replace(tzinfo=timezone.utc)
                if self.expires_at.tzinfo is None
                else self.expires_at.astimezone(timezone.utc)
            )
            if now_utc > expires_utc:
                return False
        return True

    @property
    def change_summary(self) -> str:
        """Human-readable summary of what's being changed."""
        changes = self.proposed_changes
        parts = []

        if "bank_account_number" in changes:
            # Mask account number for display
            acct = changes["bank_account_number"]
            masked = f"****{acct[-4:]}" if len(acct) > 4 else "****"
            parts.append(f"Bank Account: {masked}")

        if "tin" in changes:
            parts.append(f"TIN: {changes['tin']}")

        if "tax_state" in changes:
            parts.append(f"Tax State: {changes['tax_state']}")

        if "rsa_pin" in changes:
            parts.append(f"RSA PIN: {changes['rsa_pin']}")

        if "pfa_code" in changes:
            parts.append(f"PFA: {changes['pfa_code']}")

        if "nhf_number" in changes:
            parts.append(f"NHF: {changes['nhf_number']}")

        return ", ".join(parts) if parts else "No changes"

    def __repr__(self) -> str:
        return f"<InfoChangeRequest {self.request_id} emp={self.employee_id} status={self.status.value}>"
