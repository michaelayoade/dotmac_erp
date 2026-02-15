"""
Coach (Intelligence Engine) schemas.

API-facing Pydantic models for insights and feedback.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CoachInsightSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    insight_id: UUID
    organization_id: UUID
    audience: str
    target_employee_id: UUID | None = None
    category: str
    severity: str
    title: str
    summary: str
    coaching_action: str
    confidence: float
    status: str
    valid_until: date
    created_at: datetime


class CoachInsightListResponse(BaseModel):
    items: list[CoachInsightSummary]
    total: int
    page: int
    per_page: int


class CoachInsightFeedbackUpdate(BaseModel):
    feedback: Literal["helpful", "not_relevant", "inaccurate"] = Field(
        ..., description="helpful|not_relevant|inaccurate"
    )
