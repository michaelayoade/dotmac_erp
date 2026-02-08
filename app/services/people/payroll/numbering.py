"""
Payroll Numbering Service - Thin wrapper around SyncNumberingService.

.. deprecated::
    This module now delegates to ``SyncNumberingService`` from
    ``app.services.finance.common.numbering``.  It is kept for backward
    compatibility of the ``PayrollNumberingService`` class, the
    ``PayrollNumberSequence`` model, and the convenience functions.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import Integer, String, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base

logger = logging.getLogger(__name__)


# Maximum retry attempts for number generation
MAX_RETRIES = 10


class PayrollNumberSequence(Base):
    """
    Tracks reserved payroll numbers to ensure uniqueness.

    Each row represents a reserved number. The unique constraint on
    (organization_id, prefix, year, sequence_number) prevents duplicates.

    .. note::
        Kept for backward compatibility. New numbering goes through
        ``SyncNumberingService`` and the ``numbering_sequence`` table.
    """

    __tablename__ = "payroll_number_sequence"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "prefix",
            "year",
            "sequence_number",
            name="uq_payroll_number_org_prefix_year_seq",
        ),
        {"schema": "people"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    formatted_number: Mapped[str] = mapped_column(String(50), nullable=False)


class PayrollNumberingService:
    """
    Service for generating unique payroll document numbers.

    .. deprecated::
        Delegates to ``SyncNumberingService``. Kept for backward compat.
    """

    # Number format configuration
    SLIP_PREFIX = "SLIP"
    SLIP_PADDING = 5  # SLIP-2026-00001

    ENTRY_PREFIX = "PAY"
    ENTRY_PADDING = 4  # PAY-2026-0001

    def __init__(self, db: Session):
        self.db = db

    def generate_slip_number(
        self,
        organization_id: UUID,
        year: int | None = None,
    ) -> str:
        """
        Generate a unique salary slip number.

        Args:
            organization_id: Organization scope
            year: Deprecated — ignored, SyncNumberingService uses date

        Returns:
            Formatted slip number (e.g., SLIP-2026-00001)
        """
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        return SyncNumberingService(self.db).generate_next_number(
            organization_id, SequenceType.SALARY_SLIP
        )

    def generate_entry_number(
        self,
        organization_id: UUID,
        year: int | None = None,
    ) -> str:
        """
        Generate a unique payroll entry/run number.

        Args:
            organization_id: Organization scope
            year: Deprecated — ignored, SyncNumberingService uses date

        Returns:
            Formatted entry number (e.g., PAY-2026-0001)
        """
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        return SyncNumberingService(self.db).generate_next_number(
            organization_id, SequenceType.PAYROLL_ENTRY
        )

    def peek_next_number(
        self,
        organization_id: UUID,
        prefix: str,
        year: int | None = None,
    ) -> str:
        """
        Preview the next number without reserving it.

        Falls back to reading from the old PayrollNumberSequence table
        for peek (non-reserving) operations.
        """
        if year is None:
            year = datetime.now().year

        max_seq = self.db.scalar(
            select(func.max(PayrollNumberSequence.sequence_number)).where(
                PayrollNumberSequence.organization_id == organization_id,
                PayrollNumberSequence.prefix == prefix,
                PayrollNumberSequence.year == year,
            )
        )
        next_seq = (max_seq or 0) + 1

        if prefix == self.SLIP_PREFIX:
            padding = self.SLIP_PADDING
        else:
            padding = self.ENTRY_PADDING

        return f"{prefix}-{year}-{next_seq:0{padding}d}"


# Convenience functions for backward compatibility
def generate_slip_number(db: Session, organization_id: UUID) -> str:
    """Generate a unique slip number (convenience function)."""
    service = PayrollNumberingService(db)
    return service.generate_slip_number(organization_id)


def generate_entry_number(db: Session, organization_id: UUID, year: int) -> str:
    """Generate a unique payroll entry number (convenience function)."""
    service = PayrollNumberingService(db)
    return service.generate_entry_number(organization_id, year)
