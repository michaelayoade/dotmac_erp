"""
Numbering Sequence Service.

Generates document numbers based on configurable sequences.
"""

import logging
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.finance.core_config import (
    NumberingSequence,
    ResetFrequency,
    SequenceType,
)
from app.services.finance.common.sequence_utils import format_number, should_reset

logger = logging.getLogger(__name__)

# Default prefixes for each sequence type
DEFAULT_PREFIXES = {
    SequenceType.INVOICE: "INV",
    SequenceType.CREDIT_NOTE: "CN",
    SequenceType.PAYMENT: "PMT",
    SequenceType.RECEIPT: "RCT",
    SequenceType.JOURNAL: "JE",
    SequenceType.PURCHASE_ORDER: "PO",
    SequenceType.SUPPLIER_INVOICE: "SINV",
    SequenceType.ITEM: "ITEM",
    SequenceType.ASSET: "FA",
    SequenceType.LEASE: "LS",
    SequenceType.GOODS_RECEIPT: "GR",
    SequenceType.QUOTE: "QT",
    SequenceType.SALES_ORDER: "SO",
    SequenceType.SHIPMENT: "SHP",
    SequenceType.EXPENSE: "EXP",
    SequenceType.SUPPORT_TICKET: "",
    SequenceType.PROJECT: "PROJ",
    SequenceType.TASK: "TASK-",
    SequenceType.MATERIAL_REQUEST: "MR",
}

DEFAULT_SEQUENCE_CONFIGS = {
    SequenceType.SUPPORT_TICKET: {
        "prefix": "",
        "separator": "-",
        "min_digits": 4,
        "include_year": False,
        "include_month": False,
        "year_format": 4,
        "reset_frequency": ResetFrequency.NEVER,
    },
    SequenceType.PROJECT: {
        "prefix": "PROJ",
        "separator": "-",
        "min_digits": 4,
        "include_year": False,
        "include_month": False,
        "year_format": 4,
        "reset_frequency": ResetFrequency.NEVER,
    },
    SequenceType.TASK: {
        "prefix": "TASK-",
        "separator": "-",
        "min_digits": 5,
        "include_year": True,
        "include_month": False,
        "year_format": 4,
        "reset_frequency": ResetFrequency.YEARLY,
    },
}


def _default_sequence_kwargs(sequence_type: SequenceType) -> dict:
    defaults = DEFAULT_SEQUENCE_CONFIGS.get(sequence_type)
    if defaults:
        return {
            "prefix": defaults["prefix"],
            "suffix": "",
            "separator": defaults["separator"],
            "min_digits": defaults["min_digits"],
            "include_year": defaults["include_year"],
            "include_month": defaults["include_month"],
            "year_format": defaults["year_format"],
            "current_number": 0,
            "reset_frequency": defaults["reset_frequency"],
        }
    return {
        "prefix": DEFAULT_PREFIXES.get(sequence_type, "DOC"),
        "suffix": "",
        "separator": "-",
        "min_digits": 4,
        "include_year": True,
        "include_month": True,
        "year_format": 4,
        "current_number": 0,
        "reset_frequency": ResetFrequency.MONTHLY,
    }


class NumberingService:
    """Service for generating document numbers from sequences."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_sequence(
        self,
        organization_id: uuid.UUID,
        sequence_type: SequenceType,
    ) -> Optional[NumberingSequence]:
        """Get sequence configuration for an organization and type."""
        result = await self.db.execute(
            select(NumberingSequence).where(
                NumberingSequence.organization_id == organization_id,
                NumberingSequence.sequence_type == sequence_type,
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create_sequence(
        self,
        organization_id: uuid.UUID,
        sequence_type: SequenceType,
    ) -> NumberingSequence:
        """Get or create sequence configuration."""
        sequence = await self.get_sequence(organization_id, sequence_type)
        if sequence:
            return sequence

        # Create default sequence
        sequence = NumberingSequence(
            organization_id=organization_id,
            sequence_type=sequence_type,
            **_default_sequence_kwargs(sequence_type),
        )
        self.db.add(sequence)
        await self.db.flush()
        return sequence

    async def _get_or_create_sequence_for_update(
        self,
        organization_id: uuid.UUID,
        sequence_type: SequenceType,
    ) -> NumberingSequence:
        stmt = (
            select(NumberingSequence)
            .where(
                NumberingSequence.organization_id == organization_id,
                NumberingSequence.sequence_type == sequence_type,
            )
            .with_for_update()
        )
        result = await self.db.execute(stmt)
        sequence = result.scalar_one_or_none()
        if sequence:
            return sequence
        sequence = NumberingSequence(
            organization_id=organization_id,
            sequence_type=sequence_type,
            **_default_sequence_kwargs(sequence_type),
        )
        self.db.add(sequence)
        await self.db.flush()
        return sequence

    async def get_sequence_by_id(
        self,
        sequence_id: uuid.UUID,
    ) -> Optional[NumberingSequence]:
        """Get sequence configuration by ID."""
        result = await self.db.execute(
            select(NumberingSequence).where(
                NumberingSequence.sequence_id == sequence_id
            )
        )
        return result.scalar_one_or_none()

    async def generate_next_number(
        self,
        organization_id: uuid.UUID,
        sequence_type: SequenceType,
        reference_date: Optional[date] = None,
    ) -> str:
        """
        Generate the next document number.

        Args:
            organization_id: The organization UUID
            sequence_type: Type of document (INVOICE, QUOTE, etc.)
            reference_date: Date to use for year/month in number (defaults to today)

        Returns:
            Generated document number string
        """
        if reference_date is None:
            reference_date = date.today()

        sequence = await self._get_or_create_sequence_for_update(
            organization_id, sequence_type
        )

        if should_reset(sequence, reference_date):
            sequence.current_number = 0
            sequence.current_year = reference_date.year
            sequence.current_month = reference_date.month

        # Increment the sequence
        sequence.current_number += 1
        sequence.last_used_at = datetime.now()

        # Generate the formatted number
        number = format_number(sequence, reference_date)

        await self.db.flush()
        return number

    def _should_reset(self, sequence: NumberingSequence, reference_date: date) -> bool:
        return should_reset(sequence, reference_date)

    def _format_number(self, sequence: NumberingSequence, reference_date: date) -> str:
        return format_number(sequence, reference_date)

    def preview_format(
        self, sequence: NumberingSequence, sample_number: int = 1
    ) -> str:
        """Generate a preview of what a number would look like."""
        today = date.today()
        parts = []

        if sequence.prefix:
            parts.append(sequence.prefix)

        if sequence.include_year:
            if sequence.year_format == 2:
                parts.append(str(today.year)[-2:])
            else:
                parts.append(str(today.year))

        if sequence.include_month:
            parts.append(f"{today.month:02d}")

        date_str = "".join(parts)
        seq_str = str(sample_number).zfill(sequence.min_digits)

        if date_str:
            result = f"{date_str}{sequence.separator}{seq_str}"
        else:
            result = seq_str

        if sequence.suffix:
            result += sequence.suffix

        return result

    async def get_all_sequences(
        self,
        organization_id: uuid.UUID,
    ) -> list[NumberingSequence]:
        """Get all sequences for an organization."""
        result = await self.db.execute(
            select(NumberingSequence)
            .where(NumberingSequence.organization_id == organization_id)
            .order_by(NumberingSequence.sequence_type)
        )
        return list(result.scalars().all())

    async def update_sequence(
        self,
        sequence_id: uuid.UUID,
        prefix: Optional[str] = None,
        suffix: Optional[str] = None,
        separator: Optional[str] = None,
        min_digits: Optional[int] = None,
        include_year: Optional[bool] = None,
        include_month: Optional[bool] = None,
        year_format: Optional[int] = None,
        reset_frequency: Optional[ResetFrequency] = None,
    ) -> Optional[NumberingSequence]:
        """Update a sequence configuration."""
        result = await self.db.execute(
            select(NumberingSequence).where(
                NumberingSequence.sequence_id == sequence_id
            )
        )
        sequence = result.scalar_one_or_none()

        if not sequence:
            return None

        if prefix is not None:
            sequence.prefix = prefix
        if suffix is not None:
            sequence.suffix = suffix
        if separator is not None:
            sequence.separator = separator
        if min_digits is not None:
            sequence.min_digits = min_digits
        if include_year is not None:
            sequence.include_year = include_year
        if include_month is not None:
            sequence.include_month = include_month
        if year_format is not None:
            sequence.year_format = year_format
        if reset_frequency is not None:
            sequence.reset_frequency = reset_frequency

        await self.db.flush()
        return sequence

    async def reset_sequence_counter(
        self,
        sequence_id: uuid.UUID,
        new_value: int = 0,
    ) -> Optional[NumberingSequence]:
        """Reset a sequence counter to a specific value."""
        result = await self.db.execute(
            select(NumberingSequence).where(
                NumberingSequence.sequence_id == sequence_id
            )
        )
        sequence = result.scalar_one_or_none()

        if not sequence:
            return None

        sequence.current_number = new_value
        sequence.current_year = date.today().year
        sequence.current_month = date.today().month

        await self.db.flush()
        return sequence

    async def initialize_all_sequences(
        self,
        organization_id: uuid.UUID,
    ) -> list[NumberingSequence]:
        """Initialize all sequence types for an organization."""
        sequences = []
        for seq_type in SequenceType:
            sequence = await self.get_or_create_sequence(organization_id, seq_type)
            sequences.append(sequence)
        return sequences


class SyncNumberingService:
    """Synchronous service for generating document numbers from sequences."""

    def __init__(self, db: Session):
        self.db = db

    def get_sequence(
        self,
        organization_id: uuid.UUID,
        sequence_type: SequenceType,
    ) -> Optional[NumberingSequence]:
        """Get sequence configuration for an organization and type."""
        return (
            self.db.query(NumberingSequence)
            .filter(
                NumberingSequence.organization_id == organization_id,
                NumberingSequence.sequence_type == sequence_type,
            )
            .first()
        )

    def get_or_create_sequence(
        self,
        organization_id: uuid.UUID,
        sequence_type: SequenceType,
    ) -> NumberingSequence:
        """Get or create sequence configuration."""
        sequence = self.get_sequence(organization_id, sequence_type)
        if sequence:
            return sequence

        # Create default sequence
        sequence = NumberingSequence(
            organization_id=organization_id,
            sequence_type=sequence_type,
            **_default_sequence_kwargs(sequence_type),
        )
        self.db.add(sequence)
        self.db.flush()
        return sequence

    def generate_next_number(
        self,
        organization_id: uuid.UUID,
        sequence_type: SequenceType,
        reference_date: Optional[date] = None,
    ) -> str:
        """
        Generate the next document number.

        Args:
            organization_id: The organization UUID
            sequence_type: Type of document (INVOICE, QUOTE, etc.)
            reference_date: Date to use for year/month in number (defaults to today)

        Returns:
            Generated document number string
        """
        if reference_date is None:
            reference_date = date.today()

        sequence = (
            self.db.query(NumberingSequence)
            .filter(
                NumberingSequence.organization_id == organization_id,
                NumberingSequence.sequence_type == sequence_type,
            )
            .with_for_update()
            .first()
        )
        if not sequence:
            sequence = NumberingSequence(
                organization_id=organization_id,
                sequence_type=sequence_type,
                **_default_sequence_kwargs(sequence_type),
            )
            self.db.add(sequence)
            self.db.flush()

        if should_reset(sequence, reference_date):
            sequence.current_number = 0
            sequence.current_year = reference_date.year
            sequence.current_month = reference_date.month

        # Increment the sequence
        sequence.current_number += 1
        sequence.last_used_at = datetime.now()

        # Generate the formatted number
        number = format_number(sequence, reference_date)

        self.db.flush()
        return number

    def _should_reset(self, sequence: NumberingSequence, reference_date: date) -> bool:
        return should_reset(sequence, reference_date)

    def _format_number(self, sequence: NumberingSequence, reference_date: date) -> str:
        return format_number(sequence, reference_date)
