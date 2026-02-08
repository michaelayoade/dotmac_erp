"""
Performance Web Service - Base utilities and common functions.
"""

from __future__ import annotations

import logging
from typing import cast
from uuid import UUID

from app.models.people.perf import AppraisalStatus, KPIStatus
from app.models.people.perf.appraisal_cycle import AppraisalCycleStatus
from app.services.common import coerce_uuid
from app.services.formatters import parse_date as parse_date  # noqa: F401
from app.services.formatters import parse_decimal as parse_decimal  # noqa: F401
from app.services.formatters import parse_int as parse_int  # noqa: F401

logger = logging.getLogger(__name__)


def parse_uuid(value: str | None) -> UUID | None:
    """Parse a string to UUID, returning None on failure."""
    if not value:
        return None
    try:
        return cast(UUID | None, coerce_uuid(value))
    except Exception:
        return None


def parse_appraisal_status(value: str | None) -> AppraisalStatus | None:
    """Parse appraisal status string to enum."""
    if not value:
        return None
    try:
        return AppraisalStatus(value)
    except ValueError:
        return None


def parse_kpi_status(value: str | None) -> KPIStatus | None:
    """Parse KPI status string to enum."""
    if not value:
        return None
    try:
        return KPIStatus(value)
    except ValueError:
        return None


def parse_cycle_status(value: str | None) -> AppraisalCycleStatus | None:
    """Parse cycle status string to enum."""
    if not value:
        return None
    try:
        return AppraisalCycleStatus(value)
    except ValueError:
        return None


def parse_bool(value: str | None) -> bool | None:
    """Parse boolean from string."""
    if value == "true":
        return True
    elif value == "false":
        return False
    return None


# Constants
FEEDBACK_TYPES = ["PEER", "SUBORDINATE", "EXTERNAL"]
KPI_MEASUREMENT_TYPES = ["PERCENTAGE", "NUMBER", "CURRENCY", "RATING", "BOOLEAN"]
