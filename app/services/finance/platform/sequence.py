"""
SequenceService - Atomic document number generation.

Provides gap-free, thread-safe sequence numbers with optional
fiscal year reset capability.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.finance.core_config.numbering_sequence import NumberingSequence, SequenceType
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


class SequenceService(ListResponseMixin):
    """
    Service for atomic document number generation.

    Provides gap-free, thread-safe sequence numbers with optional
    fiscal year reset capability.
    """

    @staticmethod
    def get_next_number(
        db: Session,
        organization_id: UUID,
        sequence_type: SequenceType,
        fiscal_year_id: Optional[UUID] = None,
    ) -> str:
        """
        Get the next number in a sequence atomically.

        Uses SELECT FOR UPDATE to ensure thread-safety.
        This method does not commit; callers should commit as part of the
        surrounding transaction to preserve gap-free behavior.

        Args:
            db: Database session
            organization_id: Organization scope
            sequence_type: Type of sequence (INVOICE, JOURNAL, etc.)
            fiscal_year_id: Optional fiscal year for year-specific sequences

        Returns:
            Formatted sequence number (e.g., "INV-000001")

        Raises:
            HTTPException(404): If sequence not configured
        """
        org_id = coerce_uuid(organization_id)
        fy_id = coerce_uuid(fiscal_year_id) if fiscal_year_id else None

        # Use FOR UPDATE to lock the row
        query = db.query(NumberingSequence).filter(
            and_(
                NumberingSequence.organization_id == org_id,
                NumberingSequence.sequence_type == sequence_type,
            )
        )

        if fy_id:
            query = query.filter(NumberingSequence.fiscal_year_id == fy_id)
        else:
            query = query.filter(NumberingSequence.fiscal_year_id.is_(None))

        # Lock the row for update
        sequence = query.with_for_update().first()

        if not sequence:
            raise HTTPException(
                status_code=404,
                detail=f"Sequence not configured for {sequence_type.value}",
            )

        # Increment the number
        sequence.current_number += 1
        sequence.last_used_at = datetime.now(timezone.utc)

        db.flush()

        # Format the number
        number_str = str(sequence.current_number).zfill(sequence.min_digits)
        result = f"{sequence.prefix or ''}{number_str}{sequence.suffix or ''}"

        return result

    @staticmethod
    def configure_sequence(
        db: Session,
        organization_id: UUID,
        sequence_type: SequenceType,
        prefix: Optional[str] = None,
        suffix: Optional[str] = None,
        min_digits: int = 6,
        fiscal_year_reset: bool = False,
        fiscal_year_id: Optional[UUID] = None,
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
        query = db.query(NumberingSequence).filter(
            and_(
                NumberingSequence.organization_id == org_id,
                NumberingSequence.sequence_type == sequence_type,
            )
        )

        if fy_id:
            query = query.filter(NumberingSequence.fiscal_year_id == fy_id)
        else:
            query = query.filter(NumberingSequence.fiscal_year_id.is_(None))

        existing = query.first()

        if existing:
            # Update existing sequence
            existing.prefix = prefix
            existing.suffix = suffix
            existing.min_digits = min_digits
            existing.fiscal_year_reset = fiscal_year_reset
            db.commit()
            db.refresh(existing)
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
        db.commit()
        db.refresh(sequence)
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
        base_sequence = (
            db.query(NumberingSequence)
            .filter(
                and_(
                    NumberingSequence.organization_id == org_id,
                    NumberingSequence.sequence_type == sequence_type,
                    NumberingSequence.fiscal_year_id.is_(None),
                )
            )
            .first()
        )

        # Check if sequence for this fiscal year already exists
        fy_sequence = (
            db.query(NumberingSequence)
            .filter(
                and_(
                    NumberingSequence.organization_id == org_id,
                    NumberingSequence.sequence_type == sequence_type,
                    NumberingSequence.fiscal_year_id == fy_id,
                )
            )
            .first()
        )

        if fy_sequence:
            # Reset existing sequence
            fy_sequence.current_number = start_number
            fy_sequence.last_used_at = None
            db.commit()
            db.refresh(fy_sequence)
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
        db.commit()
        db.refresh(sequence)
        return sequence

    @staticmethod
    def get_current_number(
        db: Session,
        organization_id: UUID,
        sequence_type: SequenceType,
        fiscal_year_id: Optional[UUID] = None,
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

        query = db.query(NumberingSequence).filter(
            and_(
                NumberingSequence.organization_id == org_id,
                NumberingSequence.sequence_type == sequence_type,
            )
        )

        if fy_id:
            query = query.filter(NumberingSequence.fiscal_year_id == fy_id)
        else:
            query = query.filter(NumberingSequence.fiscal_year_id.is_(None))

        sequence = query.first()

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
        return sequence

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        sequence_type: Optional[SequenceType] = None,
        fiscal_year_id: Optional[str] = None,
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
        query = db.query(NumberingSequence)

        if organization_id:
            query = query.filter(
                NumberingSequence.organization_id == coerce_uuid(organization_id)
            )

        if sequence_type:
            query = query.filter(NumberingSequence.sequence_type == sequence_type)

        if fiscal_year_id:
            query = query.filter(
                NumberingSequence.fiscal_year_id == coerce_uuid(fiscal_year_id)
            )

        query = query.order_by(NumberingSequence.sequence_type)
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def preview_next_number(
        db: Session,
        organization_id: UUID,
        sequence_type: SequenceType,
        fiscal_year_id: Optional[UUID] = None,
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

        query = db.query(NumberingSequence).filter(
            and_(
                NumberingSequence.organization_id == org_id,
                NumberingSequence.sequence_type == sequence_type,
            )
        )

        if fy_id:
            query = query.filter(NumberingSequence.fiscal_year_id == fy_id)
        else:
            query = query.filter(NumberingSequence.fiscal_year_id.is_(None))

        sequence = query.first()

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
