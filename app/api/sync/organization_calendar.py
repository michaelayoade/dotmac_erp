"""Tenant-bound result callback used by Dotmac Integrator calendar delivery."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.service_principal import (
    get_db_with_service_org,
    require_explicit_service_scope,
)
from app.schemas.organization_calendar import (
    CalendarSyncResultRequest,
    CalendarSyncResultResponse,
)
from app.services.organization_calendar import (
    CalendarConflictError,
    CalendarNotFoundError,
    OrganizationCalendarService,
)

router = APIRouter(prefix="/sync/calendar", tags=["organization-calendar-sync"])


@router.post(
    "/events/{event_id}/result",
    response_model=CalendarSyncResultResponse,
)
def record_calendar_sync_result(
    event_id: UUID,
    payload: CalendarSyncResultRequest,
    auth: dict = Depends(require_explicit_service_scope("calendar:sync:write")),
    db: Session = Depends(get_db_with_service_org),
) -> CalendarSyncResultResponse:
    organization_id = auth["organization_id"]
    if not isinstance(organization_id, UUID):
        organization_id = UUID(str(organization_id))
    try:
        applied = OrganizationCalendarService(db, organization_id).apply_sync_result(
            event_id,
            event_version=payload.event_version,
            result=payload.result,
            participant_results=[
                item.model_dump(mode="json") for item in payload.participant_results
            ],
            nextcloud_event_url=payload.nextcloud_event_url,
            calendar_uri=payload.calendar_uri,
            etag=payload.etag,
            error_code=payload.error_code,
            error_message=payload.safe_error_message,
        )
    except CalendarNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CalendarConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CalendarSyncResultResponse(
        event_id=event_id,
        event_version=payload.event_version,
        applied=applied,
        stale=not applied,
    )


__all__ = ["router"]
