"""
FA (Fixed Assets) Web Routes.

HTML template routes for Assets and Depreciation.
"""

from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.services.finance.import_export.web import import_web_service
from app.services.fixed_assets.web import fa_web_service
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db_for_org,
    require_fixed_assets_access,
)

router = APIRouter(prefix="/fixed-assets", tags=["fa-web"])


def _build_target_fields(
    columns: dict[str, list[str]],
) -> list[dict[str, str | bool]]:
    """Build target_fields list from column requirements for the wizard."""
    fields: list[dict[str, str | bool]] = []
    for col in columns.get("required", []):
        fields.append({"source_field": col, "target_field": col, "required": True})
    for col in columns.get("optional", []):
        fields.append({"source_field": col, "target_field": col, "required": False})
    return fields


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def fa_landing(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Asset management landing dashboard."""
    context = base_context(request, auth, "Asset Management", "fixed_assets")
    context.update(fa_web_service.dashboard_context(db, str(auth.organization_id)))
    return templates.TemplateResponse(request, "fixed_assets/index.html", context)


@router.get("/reports", response_class=HTMLResponse)
def fa_reports(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
    section: str = Query(default="overview"),
    discrepancy_status: str = Query(default="OPEN"),
):
    """Asset management reporting dashboard."""
    context = base_context(request, auth, "Asset Management Reports", "reports", db=db)
    context.update(
        fa_web_service.reporting_context(
            db,
            str(auth.organization_id),
            section=section,
            discrepancy_status=discrepancy_status,
        )
    )
    return templates.TemplateResponse(request, "fixed_assets/reports.html", context)


@router.get("/reports/gl-reconciliation/export")
def export_fa_gl_reconciliation_report(
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
    as_of: date | None = Query(default=None),
    format: str = Query(default="csv", pattern="^(csv|pdf)$"),
):
    """Export asset register to GL control reconciliation as CSV or PDF."""
    if format == "pdf":
        return fa_web_service.export_gl_reconciliation_pdf_response(
            db,
            str(auth.organization_id),
            as_of=as_of,
        )
    return fa_web_service.export_gl_reconciliation_csv_response(
        db,
        str(auth.organization_id),
        as_of=as_of,
    )


@router.get("/reports/gl-reconciliation", response_class=HTMLResponse)
def fa_gl_reconciliation_report(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
    as_of: date | None = Query(default=None),
):
    """Asset register to GL control reconciliation report."""
    context = base_context(request, auth, "Asset GL Reconciliation", "reports", db=db)
    context.update(
        fa_web_service.gl_reconciliation_context(
            db,
            str(auth.organization_id),
            as_of=as_of,
        )
    )
    return templates.TemplateResponse(
        request, "fixed_assets/gl_reconciliation.html", context
    )


@router.get("/reports/count-sheets/export")
def export_fa_count_sheets_report(
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
    audit_plan_id: str | None = Query(default=None),
    location: str | None = Query(default=None),
    category: str | None = Query(default=None),
    format: str = Query(default="csv", pattern="^(csv|pdf)$"),
):
    """Export asset count sheets as CSV or PDF."""
    if format == "pdf":
        return fa_web_service.export_asset_count_sheet_pdf_response(
            db,
            str(auth.organization_id),
            audit_plan_id=audit_plan_id,
            location=location,
            category=category,
        )
    return fa_web_service.export_asset_count_sheet_csv_response(
        db,
        str(auth.organization_id),
        audit_plan_id=audit_plan_id,
        location=location,
        category=category,
    )


@router.get("/reports/count-sheets", response_class=HTMLResponse)
def fa_count_sheets_report(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
    audit_plan_id: str | None = Query(default=None),
    location: str | None = Query(default=None),
    category: str | None = Query(default=None),
):
    """Asset count sheets comparing system quantities with physical checks."""
    context = base_context(request, auth, "Asset Count Sheets", "reports", db=db)
    context.update(
        fa_web_service.asset_count_sheet_context(
            db,
            str(auth.organization_id),
            audit_plan_id=audit_plan_id,
            location=location,
            category=category,
        )
    )
    return templates.TemplateResponse(
        request, "fixed_assets/count_sheets.html", context
    )


@router.get("/dashboard", response_class=HTMLResponse)
def fa_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Asset management dashboard route."""
    return fa_landing(request, auth, db)


@router.get("/import", response_class=HTMLResponse)
def fa_import_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
):
    """Asset management import dashboard."""
    context = base_context(request, auth, "Import Asset Management", "fixed_assets")
    context["entity_types"] = [
        {
            "id": "assets",
            "name": "Assets",
            "description": "Import asset records and depreciation schedule data",
            "order": 1,
        }
    ]
    return templates.TemplateResponse(
        request, "fixed_assets/import_export/dashboard.html", context
    )


@router.get("/import/{entity_type}", response_class=HTMLResponse)
def fa_import_form(
    request: Request,
    entity_type: str,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
):
    """Asset management import form for supported entity types."""
    if entity_type != "assets":
        raise HTTPException(
            status_code=404, detail=f"Unsupported import entity: {entity_type}"
        )

    from app.services.finance.import_export.base import build_alias_map

    columns = {
        "assets": {
            "required": ["Asset Name"],
            "optional": [
                "Asset Number",
                "Acquisition Date",
                "Acquisition Cost",
                "Category",
                "Useful Life",
            ],
        }
    }

    context = base_context(request, auth, "Import Asset Management", "fixed_assets")
    context["entity_type"] = entity_type
    context["entity_name"] = "Asset Management"
    context["columns"] = columns[entity_type]
    context["preview_url"] = f"/fixed-assets/import/{entity_type}/preview"
    context["import_url"] = f"/fixed-assets/import/{entity_type}"
    context["cancel_url"] = "/fixed-assets/import"
    context["alias_map"] = build_alias_map()
    context["target_fields"] = _build_target_fields(columns[entity_type])
    context["accent_color"] = "emerald"

    return templates.TemplateResponse(
        request, "fixed_assets/import_export/import_form.html", context
    )


# =============================================================================
# Asset Count Plans
# =============================================================================


@router.get("/count-plans", response_class=HTMLResponse)
def list_count_plans(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
    status: str | None = Query(default=None),
):
    """List fixed asset physical count plans."""
    context = base_context(request, auth, "Asset Count Plans", "count_plans", db=db)
    context.update(
        fa_web_service.count_plans_context(
            db,
            str(auth.organization_id),
            status=status,
        )
    )
    return templates.TemplateResponse(request, "fixed_assets/count_plans.html", context)


@router.get("/count-plans/new", response_class=HTMLResponse)
def new_count_plan_form(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """New fixed asset physical count plan form."""
    context = base_context(request, auth, "New Asset Count Plan", "count_plans", db=db)
    context.update(
        fa_web_service.count_plan_form_context(db, str(auth.organization_id))
    )
    return templates.TemplateResponse(
        request, "fixed_assets/count_plan_form.html", context
    )


@router.post("/count-plans/new")
def create_count_plan(
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
    title: str = Form(...),
    planned_date: str = Form(...),
    scope_location_id: str | None = Form(default=None),
):
    """Create a fixed asset physical count plan."""
    return fa_web_service.create_count_plan_response(
        db,
        str(auth.organization_id),
        auth.person_id,
        title,
        planned_date,
        scope_location_id=scope_location_id,
    )


@router.get("/count-plans/{audit_plan_id}", response_class=HTMLResponse)
def count_plan_detail(
    request: Request,
    audit_plan_id: str,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
    line_status: str | None = Query(default=None),
):
    """Fixed asset physical count plan detail and check screen."""
    context = base_context(request, auth, "Asset Count Plan", "count_plans", db=db)
    context.update(
        fa_web_service.count_plan_detail_context(
            db,
            str(auth.organization_id),
            audit_plan_id,
            line_status=line_status,
        )
    )
    return templates.TemplateResponse(
        request, "fixed_assets/count_plan_detail.html", context
    )


@router.post("/count-plans/{audit_plan_id}/start")
def start_count_plan(
    audit_plan_id: str,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Start a fixed asset physical count plan."""
    return fa_web_service.start_count_plan_response(
        db,
        str(auth.organization_id),
        audit_plan_id,
    )


@router.post("/count-plans/{audit_plan_id}/complete")
def complete_count_plan(
    audit_plan_id: str,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Complete a fixed asset physical count plan."""
    return fa_web_service.complete_count_plan_response(
        db,
        str(auth.organization_id),
        audit_plan_id,
    )


@router.post("/count-plans/{audit_plan_id}/mark-pending-found")
def mark_count_plan_pending_found(
    audit_plan_id: str,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Mark all pending lines in a count plan as found."""
    return fa_web_service.mark_count_plan_pending_found_response(
        db,
        str(auth.organization_id),
        auth.person_id,
        audit_plan_id,
    )


@router.post("/count-plans/{audit_plan_id}/lines/{audit_line_id}/check")
def check_count_plan_line(
    audit_plan_id: str,
    audit_line_id: str,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
    action: str = Form(...),
    observed_location_id: str | None = Form(default=None),
    observed_status: str | None = Form(default=None),
    discrepancy_notes: str | None = Form(default=None),
):
    """Record a fixed asset physical count line check."""
    return fa_web_service.check_count_plan_line_response(
        db,
        str(auth.organization_id),
        auth.person_id,
        audit_plan_id,
        audit_line_id,
        action,
        observed_location_id=observed_location_id,
        observed_status=observed_status,
        discrepancy_notes=discrepancy_notes,
    )


@router.post("/import/{entity_type}/preview", response_class=JSONResponse)
async def fa_import_preview(
    request: Request,
    entity_type: str,
    file: UploadFile = File(...),
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Preview fixed assets import with validation and column mapping."""
    try:
        result = await import_web_service.preview_import(
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
async def fa_execute_import(
    request: Request,
    entity_type: str,
    file: UploadFile = File(...),
    skip_duplicates: str | None = Form(default=None),
    dry_run: str | None = Form(default=None),
    column_mapping: str | None = Form(default=None),
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Execute fixed assets import operation (web route)."""
    import json

    try:
        skip_dups = skip_duplicates is not None and skip_duplicates.lower() in (
            "true",
            "1",
            "on",
            "",
        )
        is_dry_run = dry_run is not None and dry_run.lower() in ("true", "1", "on", "")
        mapping = json.loads(column_mapping) if column_mapping else None

        result = await import_web_service.execute_import(
            db=db,
            organization_id=auth.organization_id,
            user_id=auth.person_id,
            entity_type=entity_type,
            file=file,
            skip_duplicates=skip_dups,
            dry_run=is_dry_run,
            column_mapping=mapping,
        )
        return JSONResponse(content=result)
    except ValueError as exc:
        return JSONResponse(content={"detail": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse(
            content={"detail": f"Import failed: {str(exc)}"}, status_code=500
        )


# =============================================================================
# Assets
# =============================================================================


@router.get("/assets", response_class=HTMLResponse)
def list_assets(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    search: str | None = None,
    category: str | None = None,
    status: str | None = None,
    location: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=10, le=200),
    db: Session = Depends(get_db_for_org),
):
    """Assets list page."""
    context = base_context(request, auth, "Asset Management", "fixed_assets")
    context.update(
        fa_web_service.list_assets_context(
            db,
            str(auth.organization_id),
            search=search,
            category=category,
            status=status,
            location=location,
            page=page,
            limit=limit,
        )
    )
    return templates.TemplateResponse(request, "fixed_assets/assets.html", context)


@router.get("/assets/new", response_class=HTMLResponse)
def new_asset_form(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """New asset form page."""
    return fa_web_service.asset_new_form_response(request, auth, db)


@router.post("/assets/new")
def create_asset(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    asset_number: str | None = Form(default=None),
    asset_name: str = Form(...),
    serial_number: str | None = Form(default=None),
    location_id: str | None = Form(default=None),
    department_id: str | None = Form(default=None),
    custodian_employee_id: str | None = Form(default=None),
    category_id: str = Form(...),
    acquisition_date: str | None = Form(default=None),
    acquisition_cost: str | None = Form(default=None),
    currency_code: str | None = Form(default=None),
    status: str | None = Form(default=None),
    description: str | None = Form(default=None),
    depreciation_schedule_id: str | None = Form(default=None),
    db: Session = Depends(get_db_for_org),
):
    """Create a new fixed asset."""
    return fa_web_service.create_asset_response(
        request,
        auth,
        asset_number,
        asset_name,
        serial_number,
        location_id,
        department_id,
        custodian_employee_id,
        category_id,
        acquisition_date,
        acquisition_cost,
        currency_code,
        status,
        description,
        depreciation_schedule_id,
        db,
    )


@router.get("/assets/export")
async def export_all_assets(
    request: Request,
    search: str = "",
    status: str = "",
    category: str = "",
    location: str = "",
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Export all assets matching filters to CSV."""
    from app.services.fixed_assets.bulk import get_asset_bulk_service

    service = get_asset_bulk_service(db, auth.organization_id, auth.user_id)
    extra = {
        key: value
        for key, value in {
            "category_id": category,
            "location_id": location,
        }.items()
        if value
    } or None
    return await service.export_all(search=search, status=status, extra_filters=extra)


@router.get("/assets/{asset_id}", response_class=HTMLResponse)
def view_asset(
    request: Request,
    asset_id: str,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Asset detail page."""
    return fa_web_service.asset_detail_response(request, auth, db, asset_id)


@router.get("/assets/{asset_id}/edit", response_class=HTMLResponse)
def edit_asset_form(
    request: Request,
    asset_id: str,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit asset form page."""
    return fa_web_service.asset_edit_form_response(request, auth, db, asset_id)


@router.post("/assets/{asset_id}/edit")
async def update_asset(
    request: Request,
    asset_id: str,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Update an existing fixed asset."""
    return await fa_web_service.update_asset_response(request, auth, db, asset_id)


@router.post("/assets/{asset_id}/dispose")
async def dispose_asset(
    request: Request,
    asset_id: str,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Dispose a fixed asset."""
    return await fa_web_service.dispose_asset_response(request, auth, db, asset_id)


@router.post("/assets/{asset_id}/revalue")
async def revalue_asset(
    request: Request,
    asset_id: str,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Revalue a fixed asset."""
    return await fa_web_service.revalue_asset_response(request, auth, db, asset_id)


@router.post("/assets/{asset_id}/impair")
async def impair_asset(
    request: Request,
    asset_id: str,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Record impairment for a fixed asset."""
    return await fa_web_service.impair_asset_response(request, auth, db, asset_id)


# =============================================================================
# Bulk Actions - Assets
# =============================================================================


@router.post("/assets/bulk-delete")
async def bulk_delete_assets(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk delete assets (only DRAFT status)."""
    from app.schemas.bulk_actions import BulkActionRequest
    from app.services.fixed_assets.bulk import get_asset_bulk_service

    body = await request.json()
    req = BulkActionRequest(**body)
    service = get_asset_bulk_service(db, auth.organization_id, auth.user_id)
    return await service.bulk_delete(req.ids)


@router.post("/assets/bulk-export")
async def bulk_export_assets(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Export selected assets to CSV."""
    from app.schemas.bulk_actions import BulkExportRequest
    from app.services.fixed_assets.bulk import get_asset_bulk_service

    body = await request.json()
    req = BulkExportRequest(**body)
    service = get_asset_bulk_service(db, auth.organization_id, auth.user_id)
    return await service.bulk_export(req.ids, req.format)


# =============================================================================
# Asset Categories
# =============================================================================


@router.get("/categories", response_class=HTMLResponse)
def list_categories(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    is_active: str | None = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db_for_org),
):
    """Asset categories list page."""
    active_filter = None
    if is_active == "true":
        active_filter = True
    elif is_active == "false":
        active_filter = False

    return fa_web_service.list_categories_response(
        request,
        auth,
        active_filter,
        page,
        db,
    )


@router.get("/categories/new", response_class=HTMLResponse)
def new_category_form(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """New asset category form page."""
    return fa_web_service.new_category_form_response(request, auth, db)


@router.post("/categories/new", response_class=HTMLResponse)
async def create_category(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new asset category."""
    return await fa_web_service.create_category_response(request, auth, db)


@router.get("/categories/{category_id}/edit", response_class=HTMLResponse)
def edit_category_form(
    request: Request,
    category_id: str,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit asset category form page."""
    return fa_web_service.edit_category_form_response(request, auth, category_id, db)


@router.post("/categories/{category_id}/edit", response_class=HTMLResponse)
async def update_category(
    request: Request,
    category_id: str,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Update an existing asset category."""
    return await fa_web_service.update_category_response(request, auth, category_id, db)


@router.post("/categories/{category_id}/toggle", response_class=HTMLResponse)
def toggle_category(
    category_id: str,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Toggle asset category active/inactive status."""
    return fa_web_service.toggle_category_response(auth, category_id, db)


# =============================================================================
# Depreciation
# =============================================================================


@router.get("/depreciation", response_class=HTMLResponse)
def depreciation_schedule(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    asset_id: str | None = None,
    period: str | None = None,
    db: Session = Depends(get_db_for_org),
):
    """Depreciation schedule page."""
    context = base_context(request, auth, "Depreciation Schedule", "fixed_assets")
    context.update(
        fa_web_service.depreciation_context(
            db,
            str(auth.organization_id),
            asset_id=asset_id,
            period=period,
        )
    )
    return templates.TemplateResponse(
        request, "fixed_assets/depreciation.html", context
    )


@router.post("/depreciation/run")
async def run_depreciation(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Run depreciation for a period."""
    return await fa_web_service.run_depreciation_response(request, auth, db)


@router.get("/depreciation/run", response_class=HTMLResponse)
@router.get("/depreciation/runs/new", response_class=HTMLResponse)
def new_depreciation_run(
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    period: str | None = None,
    db: Session = Depends(get_db_for_org),
):
    """Depreciation run creation page."""
    context = base_context(request, auth, "Create Depreciation Run", "fixed_assets")
    context.update(
        fa_web_service.depreciation_run_form_context(
            db,
            str(auth.organization_id),
            period=period,
        )
    )
    return templates.TemplateResponse(
        request, "fixed_assets/depreciation_run_form.html", context
    )


@router.get("/depreciation/runs/{run_id}", response_class=HTMLResponse)
def depreciation_run_detail(
    run_id: str,
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Depreciation run detail page."""
    context = base_context(request, auth, "Depreciation Run", "fixed_assets")
    context.update(
        fa_web_service.depreciation_run_detail_context(
            db,
            str(auth.organization_id),
            run_id,
        )
    )
    return templates.TemplateResponse(
        request, "fixed_assets/depreciation_run_detail.html", context
    )


@router.post("/depreciation/runs/{run_id}/post")
async def post_depreciation_run(
    run_id: str,
    request: Request,
    auth: WebAuthContext = Depends(require_fixed_assets_access),
    db: Session = Depends(get_db_for_org),
):
    """Post a calculated depreciation run."""
    return await fa_web_service.post_depreciation_run_response(
        request,
        auth,
        db,
        run_id,
    )
