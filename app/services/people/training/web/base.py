"""
Training Web Service - Base utilities and common functions.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional, cast
from uuid import UUID

from app.models.people.training import TrainingEventStatus, TrainingProgramStatus
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


def parse_time(value: Optional[str]) -> Optional[time]:
    """Parse time from string HH:MM format."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
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


def parse_program_status(value: Optional[str]) -> Optional[TrainingProgramStatus]:
    """Parse program status string to enum."""
    if not value:
        return None
    try:
        return TrainingProgramStatus(value)
    except ValueError:
        return None


def parse_event_status(value: Optional[str]) -> Optional[TrainingEventStatus]:
    """Parse event status string to enum."""
    if not value:
        return None
    try:
        return TrainingEventStatus(value)
    except ValueError:
        return None


def program_status_label(status: TrainingProgramStatus) -> dict:
    """Get display label and color for program status."""
    labels = {
        TrainingProgramStatus.DRAFT: ("Draft", "gray"),
        TrainingProgramStatus.ACTIVE: ("Active", "green"),
        TrainingProgramStatus.ARCHIVED: ("Archived", "red"),
    }
    label, color = labels.get(status, (status.value, "gray"))
    return {"text": label, "color": color}


def event_status_label(status: TrainingEventStatus) -> dict:
    """Get display label and color for event status."""
    labels = {
        TrainingEventStatus.DRAFT: ("Draft", "gray"),
        TrainingEventStatus.SCHEDULED: ("Scheduled", "blue"),
        TrainingEventStatus.IN_PROGRESS: ("In Progress", "yellow"),
        TrainingEventStatus.COMPLETED: ("Completed", "green"),
        TrainingEventStatus.CANCELLED: ("Cancelled", "red"),
    }
    label, color = labels.get(status, (status.value, "gray"))
    return {"text": label, "color": color}


EVENT_TYPES = ["IN_PERSON", "VIRTUAL", "HYBRID", "SELF_PACED"]
TRAINING_TYPES = ["INTERNAL", "EXTERNAL", "ONLINE", "CERTIFICATION"]
