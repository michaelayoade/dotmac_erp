"""
Payroll Domain Events.

Defines event types for payroll lifecycle changes and a simple dispatcher
for decoupling side effects (notifications, GL posting) from core logic.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID

logger = logging.getLogger(__name__)

# TypeVar for event handlers - allows handlers with specific event subtypes
T_Event = TypeVar("T_Event", bound="PayrollEvent")


# ---------------------------------------------------------------------------
# Base Event
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PayrollEvent:
    """Base class for all payroll domain events."""

    organization_id: UUID
    triggered_by_id: UUID


# ---------------------------------------------------------------------------
# Salary Slip Events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SlipCreated(PayrollEvent):
    """Emitted when a salary slip is created."""

    slip_id: UUID
    employee_id: UUID
    slip_number: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class SlipSubmitted(PayrollEvent):
    """Emitted when a salary slip is submitted for approval."""

    slip_id: UUID
    slip_number: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class SlipApproved(PayrollEvent):
    """Emitted when a salary slip is approved."""

    slip_id: UUID
    slip_number: str
    approved_by_id: UUID
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class SlipPosted(PayrollEvent):
    """Emitted when a salary slip is posted to GL."""

    slip_id: UUID
    slip_number: str
    journal_entry_id: UUID | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class SlipPaid(PayrollEvent):
    """Emitted when a salary slip is marked as paid."""

    slip_id: UUID
    slip_number: str
    payment_reference: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class SlipCancelled(PayrollEvent):
    """Emitted when a salary slip is cancelled."""

    slip_id: UUID
    slip_number: str
    reason: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class SlipRejected(PayrollEvent):
    """Emitted when a salary slip is rejected back to draft."""

    slip_id: UUID
    slip_number: str
    rejected_by_id: UUID
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Payroll Run Events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunCreated(PayrollEvent):
    """Emitted when a payroll run is created."""

    run_id: UUID
    run_number: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class RunSlipsCreated(PayrollEvent):
    """Emitted when salary slips are generated for a run."""

    run_id: UUID
    run_number: str
    slip_count: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class RunSubmitted(PayrollEvent):
    """Emitted when a payroll run is submitted for approval."""

    run_id: UUID
    run_number: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class RunApproved(PayrollEvent):
    """Emitted when a payroll run is approved."""

    run_id: UUID
    run_number: str
    approved_by_id: UUID
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class RunPosted(PayrollEvent):
    """Emitted when a payroll run is posted to GL."""

    run_id: UUID
    run_number: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class RunCancelled(PayrollEvent):
    """Emitted when a payroll run is cancelled."""

    run_id: UUID
    run_number: str
    reason: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Event Dispatcher
# ---------------------------------------------------------------------------


# Using Any for handler to allow specific event subtypes
EventHandler = Callable[[Any], None]


class PayrollEventDispatcher:
    """
    Simple in-process event dispatcher for payroll domain events.

    Handlers are registered by event type and called synchronously when
    events are dispatched. For production, this could be extended to use
    a message queue for async processing.

    Usage:
        dispatcher = PayrollEventDispatcher()
        dispatcher.register(SlipApproved, handle_slip_approved)
        dispatcher.dispatch(SlipApproved(...))
    """

    def __init__(self) -> None:
        self._handlers: dict[type[PayrollEvent], list[EventHandler]] = {}

    def register(
        self,
        event_type: type[T_Event],
        handler: Callable[[T_Event], None],
    ) -> None:
        """Register a handler for an event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
        logger.debug(
            "Registered handler %s for event %s",
            handler.__name__,
            event_type.__name__,
        )

    def unregister(
        self,
        event_type: type[T_Event],
        handler: Callable[[T_Event], None],
    ) -> None:
        """Unregister a handler for an event type."""
        if event_type in self._handlers:
            try:
                self._handlers[event_type].remove(handler)
            except ValueError:
                pass

    def dispatch(self, event: PayrollEvent) -> None:
        """
        Dispatch an event to all registered handlers.

        Handlers are called synchronously. Exceptions in one handler
        do not prevent other handlers from being called.
        """
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])

        if not handlers:
            logger.debug("No handlers for event %s", event_type.__name__)
            return

        logger.info(
            "Dispatching %s to %d handler(s)",
            event_type.__name__,
            len(handlers),
        )

        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Handler %s failed for event %s",
                    handler.__name__,
                    event_type.__name__,
                )

    def clear(self) -> None:
        """Clear all registered handlers (useful for testing)."""
        self._handlers.clear()


# Module-level singleton dispatcher
payroll_dispatcher = PayrollEventDispatcher()
