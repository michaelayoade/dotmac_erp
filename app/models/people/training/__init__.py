"""
Training Management Models.

This module contains models for training programs, events, and attendance.
"""

from app.models.people.training.training_program import TrainingProgram, TrainingProgramStatus
from app.models.people.training.training_event import (
    TrainingEvent,
    TrainingEventStatus,
    TrainingAttendee,
    AttendeeStatus,
)

__all__ = [
    "TrainingProgram",
    "TrainingProgramStatus",
    "TrainingEvent",
    "TrainingEventStatus",
    "TrainingAttendee",
    "AttendeeStatus",
]
