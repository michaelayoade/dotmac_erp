"""
Shared state machine helper for workflow transitions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Generic, TypeVar

from app.services.common import ValidationError

E = TypeVar("E", bound=Enum)


class StateMachine(Generic[E]):
    """Validate transitions for Enum-based workflows."""

    def __init__(self, transitions: Mapping[E, Iterable[E]]):
        self.transitions = transitions

    def validate(self, current: E, target: E) -> None:
        if target not in self.transitions.get(current, []):
            current_value = getattr(current, "value", current)
            target_value = getattr(target, "value", target)
            raise ValidationError(
                f"Cannot transition from {current_value} to {target_value}"
            )
