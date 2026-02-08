"""
SequenceService - Atomic document number generation.

.. deprecated::
    Use ``SyncNumberingService`` from ``app.services.finance.common.numbering``
    for new code. This class delegates ``get_next_number()`` to it and remains
    for backward compatibility of ``configure_sequence()``, ``reset_sequence()``,
    and read helpers.
"""

import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.core_config.numbering_sequence import (
    NumberingSequence,
    SequenceType,
)
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


class SequenceService(ListResponseMixin):
    """
    Service for atomic document number generation.

    .. deprecated::
        Prefer ``SyncNumberingService`` for new code.  ``get_next_number``
        now delegates to it.  Other methods are kept for backward compat.
    """

    @staticmethod
    def get_next_number(
        db: Session,
        organization_id: UUID,
        sequence_type: SequenceType,
        fiscal_year_id: UUID | None = None,
    ) -> str:
        """
        Get the next number in a sequence atomically.

        Delegates to SyncNumberingService which handles auto-init,
        date-aware reset, and SELECT FOR UPDATE locking.

        Args:
            db: Database session
            organization_id: Organization scope
            sequence_type: Type of sequence (INVOICE, JOURNAL, etc.)
            fiscal_year_id: Deprecated — ignored, kept for backward compat

        Returns:
            Formatted sequence number (e.g., "INV202602-0001")
        """
        if fiscal_year_id is not None:
            logger.debug(
                "fiscal_year_id param is deprecated and ignored; "
                "SyncNumberingService handles resets automatically"
            )

        from app.services.finance.common.numbering import SyncNumberingService

        return SyncNumberingService(db).generate_next_number(
            coerce_uuid(organization_id), sequence_type
        )

    @staticmethod
    def configure_sequence(
        db: Session,
        organization_id: UUID,
        sequence_type: SequenceType,
        prefix: str | None = None,
        suffix: str | None = None,
        min_digits: int = 6,
        fiscal_year_reset: bool = False,
        fiscal_year_id: UUID | None = None,
        start_number: int = 0,
    ) -> NumberingSequence:
        """
        Configure or update a numbering sequence.

        Args:
            db: Database session
            organization_id: Organization scope
            sequence_type: Type of sequence
            prefix: Optional prefix (e.g., "INV-")
            suffix: Optional suffix
            min_digits: Minimum digit padding (default: 6)
            fiscal_year_reset: Whether to reset on fiscal year change
            fiscal_year_id: Fiscal year ID if reset enabled
            start_number: Starting number (default: 0)

        Returns:
            Created or updated NumberingSequence
        """
        org_id = coerce_uuid(organization_id)
        fy_id = coerce_uuid(fiscal_year_id) if fiscal_year_id else None

        # Check if sequence already exists
        stmt = select(NumberingSequence).where(
            NumberingSequence.organization_id == org_id,
            NumberingSequence.sequence_type == sequence_type,
        )

        if fy_id:
            stmt = stmt.where(NumberingSequence.fiscal_year_id == fy_id)
        else:
            stmt = stmt.where(NumberingSequence.fiscal_year_id.is_(None))

        existing = db.scalar(stmt)

        if existing:
            # Update existing sequence
            if prefix is not None:
                existing.prefix = prefix
            if suffix is not None:
                existing.suffix = suffix
            existing.min_digits = min_digits
            existing.fiscal_year_reset = fiscal_year_reset
            db.flush()
            return existing

        # Create new sequence
        sequence = NumberingSequence(
            organization_id=org_id,
            sequence_type=sequence_type,
            prefix=prefix,
            suffix=suffix,
            current_number=start_number,
            min_digits=min_digits,
            fiscal_year_reset=fiscal_year_reset,
            fiscal_year_id=fy_id,
        )

        db.add(sequence)
        db.flush()
        return sequence

    @staticmethod
    def reset_sequence(
        db: Session,
        organization_id: UUID,
        sequence_type: SequenceType,
        fiscal_year_id: UUID,
        start_number: int = 0,
    ) -> NumberingSequence:
        """
        Reset a sequence for a new fiscal year.

        Creates a new sequence record for the fiscal year if it doesn't exist,
        or resets the existing one.

        Args:
            db: Database session
            organization_id: Organization scope
            sequence_type: Type of sequence
            fiscal_year_id: New fiscal year ID
            start_number: Starting number (default: 0)

        Returns:
            Reset NumberingSequence
        """
        org_id = coerce_uuid(organization_id)
        fy_id = coerce_uuid(fiscal_year_id)

        # Get the base sequence configuration (without fiscal year)
        base_stmt = select(NumberingSequence).where(
            NumberingSequence.organization_id == org_id,
            NumberingSequence.sequence_type == sequence_type,
            NumberingSequence.fiscal_year_id.is_(None),
        )
        base_sequence = db.scalar(base_stmt)

        # Check if sequence for this fiscal year already exists
        fy_stmt = select(NumberingSequence).where(
            NumberingSequence.organization_id == org_id,
            NumberingSequence.sequence_type == sequence_type,
            NumberingSequence.fiscal_year_id == fy_id,
        )
        fy_sequence = db.scalar(fy_stmt)

        if fy_sequence:
            # Reset existing sequence
            fy_sequence.current_number = start_number
            fy_sequence.last_used_at = None
            db.flush()
            return fy_sequence

        # Create new fiscal year sequence (copying settings from base if available)
        prefix = base_sequence.prefix if base_sequence else None
        suffix = base_sequence.suffix if base_sequence else None
        min_digits = base_sequence.min_digits if base_sequence else 6

        sequence = NumberingSequence(
            organization_id=org_id,
            sequence_type=sequence_type,
            prefix=prefix,
            suffix=suffix,
            current_number=start_number,
            min_digits=min_digits,
            fiscal_year_reset=True,
            fiscal_year_id=fy_id,
        )

        db.add(sequence)
        db.flush()
        return sequence

    @staticmethod
    def get_current_number(
        db: Session,
        organization_id: UUID,
        sequence_type: SequenceType,
        fiscal_year_id: UUID | None = None,
    ) -> int:
        """
        Get the current number without incrementing.

        Args:
            db: Database session
            organization_id: Organization scope
            sequence_type: Type of sequence
            fiscal_year_id: Optional fiscal year

        Returns:
            Current sequence number

        Raises:
            HTTPException(404): If sequence not found
        """
        org_id = coerce_uuid(organization_id)
        fy_id = coerce_uuid(fiscal_year_id) if fiscal_year_id else None

        stmt = select(NumberingSequence).where(
            NumberingSequence.organization_id == org_id,
            NumberingSequence.sequence_type == sequence_type,
        )

        if fy_id:
            stmt = stmt.where(NumberingSequence.fiscal_year_id == fy_id)
        else:
            stmt = stmt.where(NumberingSequence.fiscal_year_id.is_(None))

        sequence = db.scalar(stmt)

        if not sequence:
            raise HTTPException(
                status_code=404,
                detail=f"Sequence not found for {sequence_type.value}",
            )

        return sequence.current_number

    @staticmethod
    def get(
        db: Session,
        sequence_id: str,
        organization_id: UUID | None = None,
    ) -> NumberingSequence:
        """
        Get a sequence by ID.

        Args:
            db: Database session
            sequence_id: Sequence ID

        Returns:
            NumberingSequence

        Raises:
            HTTPException(404): If not found
        """
        sequence = db.get(NumberingSequence, coerce_uuid(sequence_id))
        if not sequence:
            raise HTTPException(status_code=404, detail="Sequence not found")
        if organization_id is not None and sequence.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Sequence not found")
        return sequence

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        sequence_type: SequenceType | None = None,
        fiscal_year_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[NumberingSequence]:
        """
        List numbering sequences.

        Args:
            db: Database session
            organization_id: Filter by organization
            sequence_type: Filter by sequence type
            fiscal_year_id: Filter by fiscal year
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of NumberingSequence objects
        """
        stmt = select(NumberingSequence)

        if organization_id:
            stmt = stmt.where(
                NumberingSequence.organization_id == coerce_uuid(organization_id)
            )

        if sequence_type:
            stmt = stmt.where(NumberingSequence.sequence_type == sequence_type)

        if fiscal_year_id:
            stmt = stmt.where(
                NumberingSequence.fiscal_year_id == coerce_uuid(fiscal_year_id)
            )

        stmt = stmt.order_by(NumberingSequence.sequence_type)
        stmt = stmt.limit(limit).offset(offset)
        return list(db.scalars(stmt).all())

    @staticmethod
    def preview_next_number(
        db: Session,
        organization_id: UUID,
        sequence_type: SequenceType,
        fiscal_year_id: UUID | None = None,
    ) -> str:
        """
        Preview what the next number would be without incrementing.

        Args:
            db: Database session
            organization_id: Organization scope
            sequence_type: Type of sequence
            fiscal_year_id: Optional fiscal year

        Returns:
            Formatted preview of next number

        Raises:
            HTTPException(404): If sequence not found
        """
        org_id = coerce_uuid(organization_id)
        fy_id = coerce_uuid(fiscal_year_id) if fiscal_year_id else None

        stmt = select(NumberingSequence).where(
            NumberingSequence.organization_id == org_id,
            NumberingSequence.sequence_type == sequence_type,
        )

        if fy_id:
            stmt = stmt.where(NumberingSequence.fiscal_year_id == fy_id)
        else:
            stmt = stmt.where(NumberingSequence.fiscal_year_id.is_(None))

        sequence = db.scalar(stmt)

        if not sequence:
            raise HTTPException(
                status_code=404,
                detail=f"Sequence not found for {sequence_type.value}",
            )

        next_number = sequence.current_number + 1
        number_str = str(next_number).zfill(sequence.min_digits)
        result = f"{sequence.prefix or ''}{number_str}{sequence.suffix or ''}"

        return result


# Module-level singleton instance
sequence_service = SequenceService()
