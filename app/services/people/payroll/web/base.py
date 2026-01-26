"""
Payroll Web Service - Base utilities and common functions.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional, cast
from uuid import UUID

from app.models.people.payroll.salary_component import SalaryComponentType
from app.models.people.payroll.salary_slip import SalarySlipStatus
from app.models.people.payroll.salary_structure import PayrollFrequency
from app.models.people.payroll.payroll_entry import PayrollEntryStatus
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


DEFAULT_PAGE_SIZE = 20


def parse_uuid(value: Optional[str]) -> Optional[UUID]:
    """Parse a string to UUID, returning None on failure."""
    if not value:
        return None
    try:
        return cast(Optional[UUID], coerce_uuid(value))
    except Exception:
        return None


def parse_date(value: Optional[str]) -> Optional[date]:
    """Parse a date string to date object."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_int(value: Optional[str]) -> Optional[int]:
    """Parse a string to int, returning None on failure."""
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_decimal(value: Optional[str]) -> Optional[Decimal]:
    """Parse a string to Decimal, returning None on failure."""
    if not value:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def parse_bool(value: Optional[str], default: bool = False) -> bool:
    """Parse boolean from string."""
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "on", "yes"}


def parse_component_type(value: Optional[str]) -> Optional[SalaryComponentType]:
    """Parse component type string to enum."""
    if not value:
        return None
    try:
        return SalaryComponentType(value.upper())
    except ValueError:
        return None


def parse_slip_status(value: Optional[str]) -> Optional[SalarySlipStatus]:
    """Parse slip status string to enum."""
    if not value:
        return None
    try:
        return SalarySlipStatus(value.upper())
    except ValueError:
        return None


def parse_payroll_frequency(value: Optional[str]) -> Optional[PayrollFrequency]:
    """Parse payroll frequency string to enum."""
    if not value:
        return None
    try:
        return PayrollFrequency(value.upper())
    except ValueError:
        return None


def parse_entry_status(value: Optional[str]) -> Optional[PayrollEntryStatus]:
    """Parse payroll entry status string to enum."""
    if not value:
        return None
    try:
        return PayrollEntryStatus(value.upper())
    except ValueError:
        return None


# Constants
COMPONENT_TYPES = [t.value for t in SalaryComponentType]
SLIP_STATUSES = [s.value for s in SalarySlipStatus]
PAYROLL_FREQUENCIES = [f.value for f in PayrollFrequency]
ENTRY_STATUSES = [s.value for s in PayrollEntryStatus]
