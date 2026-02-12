"""
Fleet Web Routes.

Server-rendered HTML routes for fleet management.
"""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from sqlalchemy.orm import Session

from app.services.common import NotFoundError
from app.services.finance.import_export.base import ImportConfig
from app.services.finance.import_export.import_service import ImportService
from app.services.fleet.import_export import VehicleImporter
from app.services.fleet.web.fleet_web import FleetWebService
from app.services.fleet.web.import_web import fleet_import_web_service
from app.services.upload_utils import get_env_max_bytes, write_upload_to_temp
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
    status: str | None = None,
    vehicle_type: str | None = None,
    department_id: UUID | None = None,
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


@router.get("/vehicles/import", response_class=HTMLResponse)
def vehicle_import_form(
    request: Request,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """Vehicle import form."""
    if not auth.has_all_permissions(["fleet:access", "fleet:dashboard"]):
        return RedirectResponse(
            url="/fleet/vehicles?error=not_authorized", status_code=303
        )
    context = base_context(request, auth, "Import Vehicles", "fleet", db=db)
    max_bytes = get_env_max_bytes("MAX_IMPORT_FILE_SIZE", 50 * 1024 * 1024)
    context.update(
        {
            "result": None,
            "error": None,
            "max_file_mb": max_bytes // 1024 // 1024,
        }
    )
    return templates.TemplateResponse(request, "fleet/vehicle_import.html", context)


@router.post("/vehicles/import", response_class=HTMLResponse)
async def vehicle_import_submit(
    request: Request,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """Handle vehicle CSV import."""
    if not auth.has_all_permissions(["fleet:access", "fleet:dashboard"]):
        return RedirectResponse(
            url="/fleet/vehicles?error=not_authorized", status_code=303
        )

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    upload = form.get("import_file")
    skip_duplicates = str(form.get("skip_duplicates", "")).lower() in {
        "true",
        "1",
        "on",
        "yes",
    }
    dry_run = str(form.get("dry_run", "")).lower() in {"true", "1", "on", "yes"}
    try:
        batch_size = int(form.get("batch_size", 100))
    except (TypeError, ValueError):
        batch_size = 100

    context = base_context(request, auth, "Import Vehicles", "fleet", db=db)
    max_bytes = get_env_max_bytes("MAX_IMPORT_FILE_SIZE", 50 * 1024 * 1024)
    context["max_file_mb"] = max_bytes // 1024 // 1024

    if not upload or not isinstance(upload, UploadFile) or not upload.filename:
        context["error"] = "Please choose a CSV file to upload."
        context["result"] = None
        return templates.TemplateResponse(request, "fleet/vehicle_import.html", context)

    if not upload.filename.endswith(".csv"):
        context["error"] = "Only CSV files are supported."
        context["result"] = None
        return templates.TemplateResponse(request, "fleet/vehicle_import.html", context)

    if not auth.user_id or not auth.organization_id:
        context["error"] = "Missing user or organization context."
        context["result"] = None
        return templates.TemplateResponse(request, "fleet/vehicle_import.html", context)

    tmp_path = await write_upload_to_temp(
        upload,
        suffix=".csv",
        max_bytes=max_bytes,
        error_detail=f"File too large. Maximum size: {max_bytes // 1024 // 1024}MB",
    )
    try:
        config = ImportConfig(
            organization_id=auth.organization_id,
            user_id=auth.user_id,
            skip_duplicates=skip_duplicates,
            dry_run=dry_run,
            batch_size=batch_size,
        )
        importer = VehicleImporter(db, config)
        result = ImportService.run_import(importer, tmp_path)
        context["result"] = {
            "total_rows": result.total_rows,
            "imported_count": result.imported_count,
            "skipped_count": result.skipped_count,
            "duplicate_count": result.duplicate_count,
            "error_count": result.error_count,
            "success_rate": f"{result.success_rate:.1f}%",
            "errors": [str(e) for e in result.errors[:20]],
            "warnings": [str(w) for w in result.warnings[:20]],
        }
        context["error"] = None
        return templates.TemplateResponse(request, "fleet/vehicle_import.html", context)
    except Exception as exc:
        context["error"] = str(exc)
        context["result"] = None
        return templates.TemplateResponse(request, "fleet/vehicle_import.html", context)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.get("/vehicles/template-csv")
def vehicle_import_template(
    auth: WebAuthContext = Depends(require_fleet_access),
):
    """Download a CSV template for vehicle import."""
    if not auth.has_all_permissions(["fleet:access", "fleet:dashboard"]):
        return RedirectResponse(
            url="/fleet/vehicles?error=not_authorized", status_code=303
        )

    header = [
        "Vehicle Code",
        "Registration Number",
        "Make",
        "Model",
        "Year",
        "Vehicle Type",
        "Fuel Type",
        "Ownership Type",
        "Vehicle Status",
        "VIN",
        "Engine Number",
        "License Expiry Date",
        "Location Code",
        "Location Name",
        "Current Odometer",
        "Color",
        "Assignment Type",
        "Assigned Employee Code",
        "Assigned Department Code",
    ]
    sample = [
        "VEH-001",
        "ABC-1234",
        "Toyota",
        "Corolla",
        "2020",
        "SEDAN",
        "PETROL",
        "OWNED",
        "ACTIVE",
        "JTDBR32E720123456",
        "1ZZFE123456",
        "2027-12-31",
        "BR001",
        "Main Branch",
        "12000",
        "Silver",
        "POOL",
        "",
        "",
    ]
    content = (",".join(header) + "\n" + ",".join(sample) + "\n").encode("utf-8")
    filename = "fleet_vehicles_template.csv"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
        context.update(
            web_service.vehicle_detail_context(auth.organization_id, vehicle_id)
        )
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
        context.update(
            web_service.vehicle_form_context(auth.organization_id, vehicle_id)
        )
        return templates.TemplateResponse(request, "fleet/vehicle_form.html", context)
    except NotFoundError:
        return RedirectResponse(url="/fleet/vehicles?error=not_found", status_code=303)


# =============================================================================
# Maintenance
# =============================================================================


@router.get("/maintenance", response_class=HTMLResponse)
def maintenance_list(
    request: Request,
    vehicle_id: UUID | None = None,
    status: str | None = None,
    maintenance_type: str | None = None,
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
    vehicle_id: UUID | None = None,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """New maintenance record form."""
    context = base_context(request, auth, "Schedule Maintenance", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(
        web_service.maintenance_form_context(
            auth.organization_id, vehicle_id=vehicle_id
        )
    )
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
        context.update(
            web_service.maintenance_detail_context(auth.organization_id, record_id)
        )
        return templates.TemplateResponse(
            request, "fleet/maintenance_detail.html", context
        )
    except NotFoundError:
        return RedirectResponse(
            url="/fleet/maintenance?error=not_found", status_code=303
        )


# =============================================================================
# Fuel Logs
# =============================================================================


@router.get("/fuel", response_class=HTMLResponse)
def fuel_list(
    request: Request,
    vehicle_id: UUID | None = None,
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
    vehicle_id: UUID | None = None,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """New fuel log entry form."""
    context = base_context(request, auth, "Record Fuel Purchase", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(
        web_service.fuel_form_context(auth.organization_id, vehicle_id=vehicle_id)
    )
    return templates.TemplateResponse(request, "fleet/fuel_form.html", context)


@router.get("/expense-claims/search")
def fleet_expense_claim_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=8, ge=1, le=20),
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """Search expense claims for fleet fuel/incident form linking."""
    payload = FleetWebService.expense_claim_typeahead(
        db=db,
        organization_id=str(auth.organization_id),
        query=q,
        limit=limit,
    )
    return JSONResponse(payload)


# =============================================================================
# Incidents
# =============================================================================


@router.get("/incidents", response_class=HTMLResponse)
def incident_list(
    request: Request,
    vehicle_id: UUID | None = None,
    status: str | None = None,
    severity: str | None = None,
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
    vehicle_id: UUID | None = None,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """New incident report form."""
    context = base_context(request, auth, "Report Incident", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(
        web_service.incident_form_context(auth.organization_id, vehicle_id=vehicle_id)
    )
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
        context.update(
            web_service.incident_detail_context(auth.organization_id, incident_id)
        )
        return templates.TemplateResponse(
            request, "fleet/incident_detail.html", context
        )
    except NotFoundError:
        return RedirectResponse(url="/fleet/incidents?error=not_found", status_code=303)


# =============================================================================
# Reservations
# =============================================================================


@router.get("/reservations", response_class=HTMLResponse)
def reservation_list(
    request: Request,
    vehicle_id: UUID | None = None,
    status: str | None = None,
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
        context.update(
            web_service.reservation_detail_context(auth.organization_id, reservation_id)
        )
        return templates.TemplateResponse(
            request, "fleet/reservation_detail.html", context
        )
    except NotFoundError:
        return RedirectResponse(
            url="/fleet/reservations?error=not_found", status_code=303
        )


# =============================================================================
# Documents
# =============================================================================


@router.get("/documents", response_class=HTMLResponse)
def document_list(
    request: Request,
    vehicle_id: UUID | None = None,
    document_type: str | None = None,
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
    vehicle_id: UUID | None = None,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """New document form."""
    context = base_context(request, auth, "Add Document", "fleet", db=db)
    web_service = FleetWebService(db)
    context.update(
        web_service.document_form_context(auth.organization_id, vehicle_id=vehicle_id)
    )
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
        context.update(
            web_service.document_detail_context(auth.organization_id, document_id)
        )
        return templates.TemplateResponse(
            request, "fleet/document_detail.html", context
        )
    except NotFoundError:
        return RedirectResponse(url="/fleet/documents?error=not_found", status_code=303)


# =============================================================================
# Import Dashboard
# =============================================================================


@router.get("/import", response_class=HTMLResponse)
def fleet_import_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """Fleet import dashboard page."""
    context = base_context(request, auth, "Fleet Import", "fleet", db=db)
    context["entity_types"] = fleet_import_web_service.get_dashboard_entities()
    return templates.TemplateResponse(
        request, "fleet/import_export/dashboard.html", context
    )


@router.get("/import/{entity_type}", response_class=HTMLResponse)
def fleet_import_form(
    request: Request,
    entity_type: str,
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """Fleet import form for a specific entity type."""
    entity_names = fleet_import_web_service.ENTITY_TYPES
    context = base_context(
        request,
        auth,
        f"Import {entity_names.get(entity_type, entity_type)}",
        "fleet",
        db=db,
    )
    context["entity_type"] = entity_type
    context["entity_name"] = entity_names.get(entity_type, entity_type)
    context["columns"] = fleet_import_web_service.get_entity_columns(entity_type)
    return templates.TemplateResponse(
        request, "fleet/import_export/import_form.html", context
    )


@router.post("/import/{entity_type}/preview", response_class=JSONResponse)
async def fleet_import_preview(
    request: Request,
    entity_type: str,
    file: UploadFile = File(...),
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """Preview fleet import with validation and column mapping."""
    try:
        result = await fleet_import_web_service.preview_import(
            db=db,
            organization_id=auth.organization_id,
            user_id=auth.person_id,
            entity_type=entity_type,
            file=file,
        )
        return JSONResponse(content=result)
    except ValueError as exc:
        return JSONResponse(content={"detail": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse(
            content={"detail": f"Preview failed: {str(exc)}"}, status_code=500
        )


@router.post("/import/{entity_type}", response_class=JSONResponse)
async def fleet_execute_import(
    request: Request,
    entity_type: str,
    file: UploadFile = File(...),
    skip_duplicates: str | None = Form(default=None),
    dry_run: str | None = Form(default=None),
    auth: WebAuthContext = Depends(require_fleet_access),
    db: Session = Depends(get_db),
):
    """Execute fleet import operation (web route)."""
    try:
        skip_dups = skip_duplicates is not None and skip_duplicates.lower() in (
            "true",
            "1",
            "on",
            "",
        )
        is_dry_run = dry_run is not None and dry_run.lower() in ("true", "1", "on", "")

        result = await fleet_import_web_service.execute_import(
            db=db,
            organization_id=auth.organization_id,
            user_id=auth.person_id,
            entity_type=entity_type,
            file=file,
            skip_duplicates=skip_dups,
            dry_run=is_dry_run,
        )
        return JSONResponse(content=result)
    except ValueError as exc:
        return JSONResponse(content={"detail": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse(
            content={"detail": f"Import failed: {str(exc)}"}, status_code=500
        )
