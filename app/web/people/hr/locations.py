"""Locations (Branches) routes."""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.people.hr.web.location_web import location_web_service
from app.web.deps import get_db, require_hr_access, WebAuthContext


router = APIRouter()


# =============================================================================
# Locations (Branches)
# =============================================================================


@router.get("/locations", response_class=HTMLResponse)
def list_locations(
    request: Request,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Location list page."""
    return location_web_service.list_locations_response(
        request=request,
        auth=auth,
        db=db,
        search=search,
        page=page,
    )


@router.get("/locations/new", response_class=HTMLResponse)
def new_location_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New location form page."""
    return location_web_service.new_location_form_response(
        request=request,
        auth=auth,
    )


@router.get("/locations/{location_id}/edit", response_class=HTMLResponse)
def edit_location_form(
    request: Request,
    location_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit location form page."""
    return location_web_service.edit_location_form_response(
        request=request,
        location_id=location_id,
        auth=auth,
        db=db,
    )


@router.post("/locations/new")
async def create_location(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle new location form submission."""
    return await location_web_service.create_location_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/locations/{location_id}/edit")
async def update_location(
    request: Request,
    location_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle location update form submission."""
    return await location_web_service.update_location_response(
        request=request,
        location_id=location_id,
        auth=auth,
        db=db,
    )


# =============================================================================
# Geofence Map Editor
# =============================================================================


@router.get("/locations/{location_id}/geofence", response_class=HTMLResponse)
def geofence_editor(
    request: Request,
    location_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Geofence polygon editor page with interactive map."""
    return location_web_service.geofence_editor_response(
        request=request,
        location_id=location_id,
        auth=auth,
        db=db,
    )


@router.post("/locations/{location_id}/geofence")
async def save_geofence(
    request: Request,
    location_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Save geofence settings (polygon or circle)."""
    return await location_web_service.save_geofence_response(
        request=request,
        location_id=location_id,
        auth=auth,
        db=db,
    )


@router.post("/locations/{location_id}/geofence/test")
async def test_geofence(
    request: Request,
    location_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Test if a point is within the geofence."""
    return await location_web_service.test_geofence_response(
        request=request,
        location_id=location_id,
        auth=auth,
        db=db,
    )
