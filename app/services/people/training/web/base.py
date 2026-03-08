"""
Training Web Service - Base utilities and common functions.
"""

from __future__ import annotations

import logging
from typing import cast
from uuid import UUID

from app.models.people.training import TrainingEventStatus, TrainingProgramStatus
from app.services.common import coerce_uuid
from app.services.formatters import parse_date as parse_date  # noqa: F401
from app.services.formatters import parse_decimal as parse_decimal  # noqa: F401
from app.services.formatters import parse_int as parse_int  # noqa: F401
from app.services.formatters import parse_time as parse_time  # noqa: F401

logger = logging.getLogger(__name__)


def parse_uuid(value: str | None) -> UUID | None:
    """Parse a string to UUID, returning None on failure."""
    if not value:
        return None
    try:
        return cast(UUID | None, coerce_uuid(value))
    except Exception:
        return None


def parse_program_status(value: str | None) -> TrainingProgramStatus | None:
    """Parse program status string to enum."""
    if not value:
        return None
    try:
        return TrainingProgramStatus(value)
    except ValueError:
        return None


def parse_event_status(value: str | None) -> TrainingEventStatus | None:
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


EVENT_TYPES = ["IN_PERSON", "ONLINE", "HYBRID"]
TRAINING_TYPES = ["INTERNAL", "EXTERNAL", "ONLINE", "CERTIFICATION"]
