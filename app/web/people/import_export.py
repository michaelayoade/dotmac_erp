"""
People Import Web Routes.

HTML template routes for HR data import functionality.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.services.people.hr.web.import_web import hr_import_web_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_hr_access

router = APIRouter(prefix="/import", tags=["people-import-web"])


@router.get("", response_class=HTMLResponse)
def hr_import_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """HR import dashboard page."""
    context = base_context(request, auth, "People Import", "people", db=db)
    context["entity_types"] = hr_import_web_service.get_dashboard_entities()
    return templates.TemplateResponse(
        request, "people/import_export/dashboard.html", context
    )


@router.get("/{entity_type}", response_class=HTMLResponse)
def hr_import_form(
    request: Request,
    entity_type: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """HR import form for a specific entity type."""
    entity_names = hr_import_web_service.ENTITY_TYPES
    context = base_context(
        request,
        auth,
        f"Import {entity_names.get(entity_type, entity_type)}",
        "people",
        db=db,
    )
    context["entity_type"] = entity_type
    context["entity_name"] = entity_names.get(entity_type, entity_type)
    context["columns"] = hr_import_web_service.get_entity_columns(entity_type)
    return templates.TemplateResponse(
        request, "people/import_export/import_form.html", context
    )


@router.post("/{entity_type}/preview", response_class=JSONResponse)
async def hr_import_preview(
    request: Request,
    entity_type: str,
    file: UploadFile = File(...),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Preview HR import with validation and column mapping."""
    try:
        result = await hr_import_web_service.preview_import(
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


@router.post("/{entity_type}", response_class=JSONResponse)
async def hr_execute_import(
    request: Request,
    entity_type: str,
    file: UploadFile = File(...),
    skip_duplicates: str | None = Form(default=None),
    dry_run: str | None = Form(default=None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Execute HR import operation (web route)."""
    try:
        skip_dups = skip_duplicates is not None and skip_duplicates.lower() in (
            "true",
            "1",
            "on",
            "",
        )
        is_dry_run = dry_run is not None and dry_run.lower() in ("true", "1", "on", "")

        result = await hr_import_web_service.execute_import(
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
