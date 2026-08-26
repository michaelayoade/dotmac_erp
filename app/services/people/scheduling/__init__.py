"""
Shift Scheduling Services.

This module provides services for managing shift patterns, pattern assignments,
schedule generation, and shift swap requests.
"""

from app.services.people.scheduling.schedule_generator import (
    ScheduleGenerator,
    ScheduleGeneratorError,
)
from app.services.people.scheduling.scheduling_service import (
    PatternAssignmentNotFoundError,
    SchedulingService,
    SchedulingServiceError,
    ShiftPatternNotFoundError,
    ShiftScheduleNotFoundError,
)
from app.services.people.scheduling.swap_service import (
    InvalidSwapTransitionError,
    SwapRequestNotFoundError,
    SwapService,
    SwapServiceError,
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

from app.services.people.scheduling.access import SchedulerAccessError, SchedulerAccessService
from app.services.people.scheduling.resolver import ResolvedShift, ScheduleResolver
from app.services.people.scheduling.rules import ScheduleRuleEvaluator, ScheduleRuleIssue, ScheduleRuleResult
from app.services.people.scheduling.workspace_service import ScheduleConcurrencyError, ScheduleWorkflowError, ScheduleWorkspaceService
