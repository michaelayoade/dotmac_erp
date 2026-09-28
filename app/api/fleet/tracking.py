"""ERP-authenticated Fleet tracker mapping endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import (
    get_db_with_org,
    require_organization_id,
    require_tenant_permission,
)
from app.schemas.fleet.tracking import (
    TrackerMappingCreate,
    TrackerMappingRead,
    TrackerMappingUpdate,
)
from app.services.common import ConflictError, NotFoundError
from app.services.fleet.tracking.traccar_service import FleetTrackingService

router = APIRouter(prefix="/tracking", tags=["fleet-tracking"])

require_tracking_read = require_tenant_permission("fleet:tracking:read")
require_tracking_manage = require_tenant_permission("fleet:tracking:manage")


@router.get(
    "/vehicles/{vehicle_id}/tracker",
    response_model=TrackerMappingRead,
    dependencies=[Depends(require_tracking_read)],
)
def get_vehicle_tracker(
    vehicle_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
) -> TrackerMappingRead:
    """Return the active tracker mapping for one ERP vehicle."""
    service = FleetTrackingService(db, organization_id)
    try:
        tracker = service.get_active_tracker_or_raise(vehicle_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return TrackerMappingRead.model_validate(tracker)


@router.post(
    "/vehicles/{vehicle_id}/tracker",
    response_model=TrackerMappingRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_tracking_manage)],
)
def create_vehicle_tracker(
    vehicle_id: UUID,
    data: TrackerMappingCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
) -> TrackerMappingRead:
    """Create a durable tracker mapping without calling Traccar."""
    service = FleetTrackingService(db, organization_id)
    try:
        tracker = service.create_mapping(vehicle_id, data)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return TrackerMappingRead.model_validate(tracker)


@router.patch(
    "/vehicles/{vehicle_id}/tracker",
    response_model=TrackerMappingRead,
    dependencies=[Depends(require_tracking_manage)],
)
def update_vehicle_tracker(
    vehicle_id: UUID,
    data: TrackerMappingUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
) -> TrackerMappingRead:
    """Patch mutable fields on the active tracker mapping."""
    service = FleetTrackingService(db, organization_id)
    try:
        tracker = service.update_mapping(vehicle_id, data)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return TrackerMappingRead.model_validate(tracker)


@router.delete(
    "/vehicles/{vehicle_id}/tracker",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_tracking_manage)],
)
def unlink_vehicle_tracker(
    vehicle_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
) -> Response:
    """End the active mapping while retaining its assignment history."""
    service = FleetTrackingService(db, organization_id)
    try:
        service.unlink_mapping(vehicle_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
