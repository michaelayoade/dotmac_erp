"""
Performance Web Service - Base utilities and common functions.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Optional, cast
from uuid import UUID

from app.models.people.perf import AppraisalStatus, KPIStatus
from app.models.people.perf.appraisal_cycle import AppraisalCycleStatus
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


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
    except Exception:
        return None


def parse_appraisal_status(value: Optional[str]) -> Optional[AppraisalStatus]:
    """Parse appraisal status string to enum."""
    if not value:
        return None
    try:
        return AppraisalStatus(value)
    except ValueError:
        return None


def parse_kpi_status(value: Optional[str]) -> Optional[KPIStatus]:
    """Parse KPI status string to enum."""
    if not value:
        return None
    try:
        return KPIStatus(value)
    except ValueError:
        return None


def parse_cycle_status(value: Optional[str]) -> Optional[AppraisalCycleStatus]:
    """Parse cycle status string to enum."""
    if not value:
        return None
    try:
        return AppraisalCycleStatus(value)
    except ValueError:
        return None


def parse_bool(value: Optional[str]) -> Optional[bool]:
    """Parse boolean from string."""
    if value == "true":
        return True
    elif value == "false":
        return False
    return None


# Constants
FEEDBACK_TYPES = ["PEER", "SUBORDINATE", "EXTERNAL"]
KPI_MEASUREMENT_TYPES = ["PERCENTAGE", "NUMBER", "CURRENCY", "RATING", "BOOLEAN"]
