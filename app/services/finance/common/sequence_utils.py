"""
Shared helpers for numbering sequences.
"""

from __future__ import annotations

from datetime import date

from app.models.finance.core_config import NumberingSequence, ResetFrequency


def should_reset(sequence: NumberingSequence, reference_date: date) -> bool:
    """Check if the sequence counter should be reset."""
    if sequence.reset_frequency == ResetFrequency.NEVER:
        return False

    if sequence.current_year is None:
        return True

    if sequence.reset_frequency == ResetFrequency.YEARLY:
        return bool(reference_date.year != sequence.current_year)

    if sequence.reset_frequency == ResetFrequency.MONTHLY:
        return bool(
            reference_date.year != sequence.current_year
            or reference_date.month != sequence.current_month
        )

    return False


def format_number(sequence: NumberingSequence, reference_date: date) -> str:
    """Format the document number based on sequence configuration."""
    parts: list[str] = []

    if sequence.prefix:
        parts.append(sequence.prefix)

    if sequence.include_year:
        if sequence.year_format == 2:
            parts.append(str(reference_date.year)[-2:])
        else:
            parts.append(str(reference_date.year))

    if sequence.include_month:
        parts.append(f"{reference_date.month:02d}")

    date_str = "".join(parts)
    seq_str = str(sequence.current_number).zfill(sequence.min_digits)

    if date_str:
        result = f"{date_str}{sequence.separator}{seq_str}"
    else:
        result = seq_str

    if sequence.suffix:
        result += sequence.suffix

    return result
