"""
Shift Scheduling Services.

This module provides services for managing shift patterns, pattern assignments,
schedule generation, and shift swap requests.
"""

from app.services.people.scheduling.scheduling_service import (
    SchedulingService,
    SchedulingServiceError,
    ShiftPatternNotFoundError,
    PatternAssignmentNotFoundError,
    ShiftScheduleNotFoundError,
)
from app.services.people.scheduling.schedule_generator import (
    ScheduleGenerator,
    ScheduleGeneratorError,
)
from app.services.people.scheduling.swap_service import (
    SwapService,
    SwapServiceError,
    SwapRequestNotFoundError,
    InvalidSwapTransitionError,
)

__all__ = [
    "SchedulingService",
    "SchedulingServiceError",
    "ShiftPatternNotFoundError",
    "PatternAssignmentNotFoundError",
    "ShiftScheduleNotFoundError",
    "ScheduleGenerator",
    "ScheduleGeneratorError",
    "SwapService",
    "SwapServiceError",
    "SwapRequestNotFoundError",
    "InvalidSwapTransitionError",
]
