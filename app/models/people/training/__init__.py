"""
Training Management Models.

This module contains models for training programs, events, and attendance.
"""

from app.models.people.training.training_event import (
    AttendeeStatus,
    TrainingAttendee,
    TrainingEvent,
    TrainingEventStatus,
)
from app.models.people.training.training_program import (
    TrainingProgram,
    TrainingProgramStatus,
)

__all__ = [
    "TrainingProgram",
    "TrainingProgramStatus",
    "TrainingEvent",
    "TrainingEventStatus",
    "TrainingAttendee",
    "AttendeeStatus",
]
