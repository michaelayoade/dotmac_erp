"""Pydantic schemas for the notification API surface."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NotificationRead(BaseModel):
    """A single notification as returned to API clients."""

    model_config = ConfigDict(from_attributes=True)

    notification_id: UUID
    entity_type: str
    entity_id: UUID
    notification_type: str
    channel: str
    title: str
    message: str
    action_url: str | None = None
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime


class NotificationListResponse(BaseModel):
    """Paginated notification list with unread badge count."""

    items: list[NotificationRead]
    unread_count: int
    offset: int
    limit: int
    has_more: bool
