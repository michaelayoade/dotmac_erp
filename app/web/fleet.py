"""
Fleet Web Routes.

Server-rendered HTML routes for fleet management.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.common import NotFoundError
from app.services.fleet.web.fleet_web import FleetWebService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_fleet_access

router = APIRouter(prefix="/fleet", tags=["fleet-web"])


# =============================================================================
# Dashboard
# =============================================================================


@router.get("", response_class=HTMLResponse)
def fleet_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """Fleet management dashboard."""
    context = base_context(request, auth, "Fleet Dashboard", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(web_service.dashboard_context(auth.organization_id))
    return templates.TemplateResponse(request, "fleet/dashboard.html", context)


# =============================================================================
# Vehicles
# =============================================================================


@router.get("/vehicles", response_class=HTMLResponse)
def vehicle_list(
    request: Request,
    status: Optional[str] = None,
    vehicle_type: Optional[str] = None,
    department_id: Optional[UUID] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """List all vehicles."""
    context = base_context(request, auth, "Vehicles", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(
        web_service.vehicle_list_context(
            auth.organization_id,
            status=status,
            vehicle_type=vehicle_type,
            department_id=department_id,
            offset=offset,
            limit=limit,
        )
    )
    return templates.TemplateResponse(request, "fleet/vehicles.html", context)


@router.get("/vehicles/new", response_class=HTMLResponse)
def vehicle_new(
    request: Request,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """New vehicle form."""
    context = base_context(request, auth, "Add Vehicle", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(web_service.vehicle_form_context(auth.organization_id))
    return templates.TemplateResponse(request, "fleet/vehicle_form.html", context)


@router.get("/vehicles/{vehicle_id}", response_class=HTMLResponse)
def vehicle_detail(
    request: Request,
    vehicle_id: UUID,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """Vehicle detail view."""
    context = base_context(request, auth, "Vehicle Details", "fleet", db=db)
    web_service = FleetWebService(db)
    try:
        context.update(web_service.vehicle_detail_context(auth.organization_id, vehicle_id))
        return templates.TemplateResponse(request, "fleet/vehicle_detail.html", context)
    except NotFoundError:
        return RedirectResponse(url="/fleet/vehicles?error=not_found", status_code=303)


@router.get("/vehicles/{vehicle_id}/edit", response_class=HTMLResponse)
def vehicle_edit(
    request: Request,
    vehicle_id: UUID,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """Edit vehicle form."""
    context = base_context(request, auth, "Edit Vehicle", "fleet", db=db)
    web_service = FleetWebService(db)
    try:
        context.update(web_service.vehicle_form_context(auth.organization_id, vehicle_id))
        return templates.TemplateResponse(request, "fleet/vehicle_form.html", context)
    except NotFoundError:
        return RedirectResponse(url="/fleet/vehicles?error=not_found", status_code=303)


# =============================================================================
# Maintenance
# =============================================================================


@router.get("/maintenance", response_class=HTMLResponse)
def maintenance_list(
    request: Request,
    vehicle_id: Optional[UUID] = None,
    status: Optional[str] = None,
    maintenance_type: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """List maintenance records."""
    context = base_context(request, auth, "Maintenance", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(
        web_service.maintenance_list_context(
            auth.organization_id,
            vehicle_id=vehicle_id,
            status=status,
            maintenance_type=maintenance_type,
            offset=offset,
            limit=limit,
        )
    )
    return templates.TemplateResponse(request, "fleet/maintenance.html", context)


@router.get("/maintenance/new", response_class=HTMLResponse)
def maintenance_new(
    request: Request,
    vehicle_id: Optional[UUID] = None,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """New maintenance record form."""
    context = base_context(request, auth, "Schedule Maintenance", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(web_service.maintenance_form_context(auth.organization_id, vehicle_id=vehicle_id))
    return templates.TemplateResponse(request, "fleet/maintenance_form.html", context)


@router.get("/maintenance/{record_id}", response_class=HTMLResponse)
def maintenance_detail(
    request: Request,
    record_id: UUID,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """Maintenance record detail view."""
    context = base_context(request, auth, "Maintenance Details", "fleet", db=db)
    web_service = FleetWebService(db)
    try:
        context.update(web_service.maintenance_detail_context(auth.organization_id, record_id))
        return templates.TemplateResponse(request, "fleet/maintenance_detail.html", context)
    except NotFoundError:
        return RedirectResponse(url="/fleet/maintenance?error=not_found", status_code=303)


# =============================================================================
# Fuel Logs
# =============================================================================


@router.get("/fuel", response_class=HTMLResponse)
def fuel_list(
    request: Request,
    vehicle_id: Optional[UUID] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """List fuel log entries."""
    context = base_context(request, auth, "Fuel Logs", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(
        web_service.fuel_list_context(
            auth.organization_id,
            vehicle_id=vehicle_id,
            offset=offset,
            limit=limit,
        )
    )
    return templates.TemplateResponse(request, "fleet/fuel.html", context)


@router.get("/fuel/new", response_class=HTMLResponse)
def fuel_new(
    request: Request,
    vehicle_id: Optional[UUID] = None,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """New fuel log entry form."""
    context = base_context(request, auth, "Record Fuel Purchase", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(web_service.fuel_form_context(auth.organization_id, vehicle_id=vehicle_id))
    return templates.TemplateResponse(request, "fleet/fuel_form.html", context)


# =============================================================================
# Incidents
# =============================================================================


@router.get("/incidents", response_class=HTMLResponse)
def incident_list(
    request: Request,
    vehicle_id: Optional[UUID] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """List incidents."""
    context = base_context(request, auth, "Incidents", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(
        web_service.incident_list_context(
            auth.organization_id,
            vehicle_id=vehicle_id,
            status=status,
            severity=severity,
            offset=offset,
            limit=limit,
        )
    )
    return templates.TemplateResponse(request, "fleet/incidents.html", context)


@router.get("/incidents/new", response_class=HTMLResponse)
def incident_new(
    request: Request,
    vehicle_id: Optional[UUID] = None,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """New incident report form."""
    context = base_context(request, auth, "Report Incident", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(web_service.incident_form_context(auth.organization_id, vehicle_id=vehicle_id))
    return templates.TemplateResponse(request, "fleet/incident_form.html", context)


@router.get("/incidents/{incident_id}", response_class=HTMLResponse)
def incident_detail(
    request: Request,
    incident_id: UUID,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """Incident detail view."""
    context = base_context(request, auth, "Incident Details", "fleet", db=db)
    web_service = FleetWebService(db)
    try:
        context.update(web_service.incident_detail_context(auth.organization_id, incident_id))
        return templates.TemplateResponse(request, "fleet/incident_detail.html", context)
    except NotFoundError:
        return RedirectResponse(url="/fleet/incidents?error=not_found", status_code=303)


# =============================================================================
# Reservations
# =============================================================================


@router.get("/reservations", response_class=HTMLResponse)
def reservation_list(
    request: Request,
    vehicle_id: Optional[UUID] = None,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """List reservations."""
    context = base_context(request, auth, "Reservations", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(
        web_service.reservation_list_context(
            auth.organization_id,
            vehicle_id=vehicle_id,
            status=status,
            offset=offset,
            limit=limit,
        )
    )
    return templates.TemplateResponse(request, "fleet/reservations.html", context)


@router.get("/reservations/new", response_class=HTMLResponse)
def reservation_new(
    request: Request,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """New reservation form."""
    context = base_context(request, auth, "New Reservation", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(web_service.reservation_form_context(auth.organization_id))
    return templates.TemplateResponse(request, "fleet/reservation_form.html", context)


@router.get("/reservations/{reservation_id}", response_class=HTMLResponse)
def reservation_detail(
    request: Request,
    reservation_id: UUID,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """Reservation detail view."""
    context = base_context(request, auth, "Reservation Details", "fleet", db=db)
    web_service = FleetWebService(db)
    try:
        context.update(web_service.reservation_detail_context(auth.organization_id, reservation_id))
        return templates.TemplateResponse(request, "fleet/reservation_detail.html", context)
    except NotFoundError:
        return RedirectResponse(url="/fleet/reservations?error=not_found", status_code=303)


# =============================================================================
# Documents
# =============================================================================


@router.get("/documents", response_class=HTMLResponse)
def document_list(
    request: Request,
    vehicle_id: Optional[UUID] = None,
    document_type: Optional[str] = None,
    expired_only: bool = False,
    expiring_soon: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """List documents."""
    context = base_context(request, auth, "Documents", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(
        web_service.document_list_context(
            auth.organization_id,
            vehicle_id=vehicle_id,
            document_type=document_type,
            expired_only=expired_only,
            expiring_soon=expiring_soon,
            offset=offset,
            limit=limit,
        )
    )
    return templates.TemplateResponse(request, "fleet/documents.html", context)


@router.get("/documents/new", response_class=HTMLResponse)
def document_new(
    request: Request,
    vehicle_id: Optional[UUID] = None,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """New document form."""
    context = base_context(request, auth, "Add Document", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(web_service.document_form_context(auth.organization_id, vehicle_id=vehicle_id))
    return templates.TemplateResponse(request, "fleet/document_form.html", context)


@router.get("/documents/{document_id}", response_class=HTMLResponse)
def document_detail(
    request: Request,
    document_id: UUID,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """Document detail view."""
    context = base_context(request, auth, "Document Details", "fleet", db=db)
    web_service = FleetWebService(db)
    try:
        context.update(web_service.document_detail_context(auth.organization_id, document_id))
        return templates.TemplateResponse(request, "fleet/document_detail.html", context)
    except NotFoundError:
        return RedirectResponse(url="/fleet/documents?error=not_found", status_code=303)
