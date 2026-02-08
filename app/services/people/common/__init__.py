"""
Common People Services.

Shared utilities and helper functions used across People modules.

Includes:
- Document numbering helpers
- Date/period calculations
- Currency formatting
- Employee code generation
"""

import uuid
from datetime import date

from sqlalchemy.orm import Session


def generate_employee_code(
    db: Session,
    organization_id: uuid.UUID,
    prefix: str = "EMP",
    year: int | None = None,
) -> str:
    """
    Generate a unique employee code.

    Format: {PREFIX}-{YEAR}-{SEQUENCE}
    Example: EMP-2024-0001

    Args:
        db: Database session
        organization_id: Organization UUID
        prefix: Code prefix (default: EMP)
        year: Year to use (default: current year)

    Returns:
        Generated employee code string
    """
    if year is None:
        year = date.today().year

    # Get next sequence number (this will use a proper sequence table in production)
    # For now, return a placeholder
    return f"{prefix}-{year}-0001"


def calculate_workdays(start_date: date, end_date: date) -> int:
    """
    Calculate number of working days between two dates.

    Excludes weekends (Saturday, Sunday).
    Does not account for holidays - use holiday_list for that.

    Args:
        start_date: Start date (inclusive)
        end_date: End date (inclusive)

    Returns:
        Number of working days
    """
    if end_date < start_date:
        return 0

    (end_date - start_date).days + 1
    workdays = 0

    current = start_date
    from datetime import timedelta

    while current <= end_date:
        # Monday = 0, Sunday = 6
        if current.weekday() < 5:  # Monday to Friday
            workdays += 1
        current += timedelta(days=1)

    return workdays
