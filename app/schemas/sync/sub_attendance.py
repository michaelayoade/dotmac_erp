"""Narrow Selfcare-to-ERP attendance integration contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SelfcareAttendanceState(str, Enum):
    NOT_CHECKED_IN = "not_checked_in"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    INELIGIBLE = "ineligible"


class SelfcareAttendanceLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)
    observed_at: datetime | None = None


class SelfcareAttendanceRead(BaseModel):
    state: SelfcareAttendanceState
    attendance_date: date
    timezone: str
    check_in_at: datetime | None = None
    check_out_at: datetime | None = None
    working_hours: Decimal | None = None
    status: str | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    reason: str | None = None


class SelfcareAttendanceErrorRead(BaseModel):
    code: str
    message: str
