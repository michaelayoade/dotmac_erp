"""Typed Dotmac Integrator result contract for organization calendars."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CalendarParticipantSyncResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    sync_status: Literal[
        "SYNCED",
        "FAILED_RETRYABLE",
        "FAILED_PERMANENT",
        "STALE",
    ]
    error_code: str | None = Field(default=None, max_length=100)
    safe_error_message: str | None = Field(default=None, max_length=500)


class CalendarSyncResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_version: int = Field(ge=1)
    result: Literal["SYNCED", "PARTIAL_FAILURE", "FAILED"]
    nextcloud_event_url: str | None = Field(default=None, max_length=2000)
    calendar_uri: str | None = Field(default=None, max_length=1000)
    etag: str | None = Field(default=None, max_length=255)
    participant_results: list[CalendarParticipantSyncResult] = Field(
        default_factory=list, max_length=1000
    )
    error_code: str | None = Field(default=None, max_length=100)
    safe_error_message: str | None = Field(default=None, max_length=500)


class CalendarSyncResultResponse(BaseModel):
    event_id: UUID
    event_version: int
    applied: bool
    stale: bool


__all__ = [
    "CalendarParticipantSyncResult",
    "CalendarSyncResultRequest",
    "CalendarSyncResultResponse",
]
