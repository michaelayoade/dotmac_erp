"""
Training Web Service - Modular web view services for training module.

Usage:
    from app.services.people.training.web import training_web_service
"""

from .base import (
    parse_uuid,
    parse_date,
    parse_time,
    parse_int,
    parse_decimal,
    parse_program_status,
    parse_event_status,
    program_status_label,
    event_status_label,
    EVENT_TYPES,
    TRAINING_TYPES,
)

from .program_web import ProgramWebService
from .event_web import EventWebService
from .report_web import ReportWebService


class TrainingWebService(
    ProgramWebService,
    EventWebService,
    ReportWebService,
):
    """
    Unified Training Web Service facade.

    Combines program, event, and report web services into a single interface.
    """

    pass


# Module-level singleton
training_web_service = TrainingWebService()


__all__ = [
    # Utilities
    "parse_uuid",
    "parse_date",
    "parse_time",
    "parse_int",
    "parse_decimal",
    "parse_program_status",
    "parse_event_status",
    "program_status_label",
    "event_status_label",
    # Constants
    "EVENT_TYPES",
    "TRAINING_TYPES",
    # Services
    "ProgramWebService",
    "EventWebService",
    "ReportWebService",
    "TrainingWebService",
    "training_web_service",
]
