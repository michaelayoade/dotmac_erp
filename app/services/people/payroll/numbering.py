"""
Payroll Numbering Service - Idempotent slip and run number generation.

Provides race-condition-safe number generation using unique constraints
with retry-on-conflict. This pattern works with any database backend
and is safer than MAX+1 or COUNT+1 approaches.

Key design:
- Numbers are reserved atomically via INSERT with unique constraint
- On conflict (concurrent insert), retry with next number
- Maintains per-organization, per-year sequences
- Format: PREFIX-YYYY-NNNNN (e.g., SLIP-2026-00001, PAY-2026-0001)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import Integer, String, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import IntegrityError
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

    Uses a retry-on-conflict pattern with unique constraints to ensure
    idempotent number generation even under concurrent load.

    Usage:
        service = PayrollNumberingService(db)
        slip_number = service.generate_slip_number(org_id)
        entry_number = service.generate_entry_number(org_id)
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
        year: Optional[int] = None,
    ) -> str:
        """
        Generate a unique salary slip number.

        Args:
            organization_id: Organization scope
            year: Year for the number (defaults to current year)

        Returns:
            Formatted slip number (e.g., SLIP-2026-00001)

        Raises:
            RuntimeError: If unable to generate number after max retries
        """
        return self._generate_number(
            organization_id=organization_id,
            prefix=self.SLIP_PREFIX,
            padding=self.SLIP_PADDING,
            year=year,
        )

    def generate_entry_number(
        self,
        organization_id: UUID,
        year: Optional[int] = None,
    ) -> str:
        """
        Generate a unique payroll entry/run number.

        Args:
            organization_id: Organization scope
            year: Year for the number (defaults to current year)

        Returns:
            Formatted entry number (e.g., PAY-2026-0001)

        Raises:
            RuntimeError: If unable to generate number after max retries
        """
        return self._generate_number(
            organization_id=organization_id,
            prefix=self.ENTRY_PREFIX,
            padding=self.ENTRY_PADDING,
            year=year,
        )

    def _generate_number(
        self,
        organization_id: UUID,
        prefix: str,
        padding: int,
        year: Optional[int] = None,
    ) -> str:
        """
        Core number generation with retry-on-conflict using savepoints.

        Algorithm:
        1. Get current max sequence number for org/prefix/year
        2. Create a savepoint and try to insert next number
        3. On conflict (duplicate), rollback ONLY the savepoint and retry
        4. Return the successfully reserved number

        Uses begin_nested() savepoints to ensure that on conflict, only the
        sequence INSERT is rolled back - not any other pending changes in
        the transaction. This is critical when numbering is called mid-transaction.
        """
        if year is None:
            year = datetime.now().year

        # Get the current max sequence number
        max_seq = self._get_max_sequence(organization_id, prefix, year)
        next_seq = (max_seq or 0) + 1

        for attempt in range(MAX_RETRIES):
            formatted = self._format_number(prefix, year, next_seq, padding)

            # Use savepoint so we only rollback the INSERT on conflict,
            # not any other pending changes in the transaction
            savepoint = self.db.begin_nested()
            try:
                # Try to reserve this number
                sequence_record = PayrollNumberSequence(
                    organization_id=organization_id,
                    prefix=prefix,
                    year=year,
                    sequence_number=next_seq,
                    formatted_number=formatted,
                )
                self.db.add(sequence_record)
                self.db.flush()
                savepoint.commit()

                logger.debug(
                    "Reserved number %s for org %s (attempt %d)",
                    formatted,
                    organization_id,
                    attempt + 1,
                )
                return formatted

            except IntegrityError:
                # Duplicate - another transaction got this number first
                # Rollback only the savepoint, preserving other transaction work
                savepoint.rollback()
                logger.debug(
                    "Number %s already taken, retrying (attempt %d)",
                    formatted,
                    attempt + 1,
                )

                # Refresh max sequence and try next
                max_seq = self._get_max_sequence(organization_id, prefix, year)
                next_seq = (max_seq or 0) + 1

        # Exhausted retries
        raise RuntimeError(
            f"Unable to generate {prefix} number after {MAX_RETRIES} attempts. "
            f"This may indicate extremely high concurrency or a bug."
        )

    def _get_max_sequence(
        self,
        organization_id: UUID,
        prefix: str,
        year: int,
    ) -> Optional[int]:
        """Get the current maximum sequence number for org/prefix/year."""
        result = self.db.scalar(
            select(func.max(PayrollNumberSequence.sequence_number)).where(
                PayrollNumberSequence.organization_id == organization_id,
                PayrollNumberSequence.prefix == prefix,
                PayrollNumberSequence.year == year,
            )
        )
        return result

    def _format_number(
        self,
        prefix: str,
        year: int,
        sequence: int,
        padding: int,
    ) -> str:
        """Format the number with prefix, year, and zero-padded sequence."""
        return f"{prefix}-{year}-{sequence:0{padding}d}"

    def peek_next_number(
        self,
        organization_id: UUID,
        prefix: str,
        year: Optional[int] = None,
    ) -> str:
        """
        Preview the next number without reserving it.

        Useful for displaying "next number will be..." in UI.
        Note: This is NOT guaranteed to be the actual next number
        due to concurrent access.
        """
        if year is None:
            year = datetime.now().year

        max_seq = self._get_max_sequence(organization_id, prefix, year)
        next_seq = (max_seq or 0) + 1

        if prefix == self.SLIP_PREFIX:
            padding = self.SLIP_PADDING
        else:
            padding = self.ENTRY_PADDING

        return self._format_number(prefix, year, next_seq, padding)


# Convenience functions for backward compatibility
def generate_slip_number(db: Session, organization_id: UUID) -> str:
    """Generate a unique slip number (convenience function)."""
    service = PayrollNumberingService(db)
    return service.generate_slip_number(organization_id)


def generate_entry_number(db: Session, organization_id: UUID, year: int) -> str:
    """Generate a unique payroll entry number (convenience function)."""
    service = PayrollNumberingService(db)
    return service.generate_entry_number(organization_id, year)
