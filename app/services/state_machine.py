"""
Shared state machine helper for workflow transitions.
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable

from app.services.common import ValidationError


class StateMachine:
    """Validate transitions for Enum-based workflows."""

    def __init__(self, transitions: dict[Enum, Iterable[Enum]]):
        self.transitions = transitions

    def validate(self, current: Enum, target: Enum) -> None:
        if target not in self.transitions.get(current, []):
            current_value = getattr(current, "value", current)
            target_value = getattr(target, "value", target)
            raise ValidationError(
                f"Cannot transition from {current_value} to {target_value}"
            )
