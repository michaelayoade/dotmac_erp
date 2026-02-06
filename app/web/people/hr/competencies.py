"""Competencies routes."""

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.hr import CompetencyCategory
from app.services.people.hr import CompetencyService
from app.services.common import coerce_uuid
from app.templates import templates
from app.web.deps import base_context, get_db, require_hr_access, WebAuthContext

from ._common import _parse_bool


router = APIRouter()


# =============================================================================
# Competencies
# =============================================================================


@router.get("/competencies", response_class=HTMLResponse)
def list_competencies(
    request: Request,
    category: Optional[str] = None,
    search: Optional[str] = None,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Competency catalog page."""
    org_id = coerce_uuid(auth.organization_id)
    comp_svc = CompetencyService(db, org_id)

    cat = CompetencyCategory(category) if category else None
    result = comp_svc.list_competencies(category=cat, is_active=None, search=search)

    context = base_context(request, auth, "Competencies", "competencies", db=db)
    context.update(
        {
            "competencies": result.items,
            "categories": list(CompetencyCategory),
            "selected_category": category,
            "search": search,
            "success": success,
            "error": error,
        }
    )
    return templates.TemplateResponse(request, "people/hr/competencies.html", context)


@router.get("/competencies/new", response_class=HTMLResponse)
def new_competency_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New competency form."""
    context = base_context(request, auth, "Add Competency", "competencies", db=db)
    context.update(
        {
            "categories": list(CompetencyCategory),
            "form_data": {},
        }
    )
    return templates.TemplateResponse(
        request, "people/hr/competency_form.html", context
    )


@router.post("/competencies/new", response_class=HTMLResponse)
def create_competency(
    request: Request,
    competency_code: str = Form(...),
    competency_name: str = Form(...),
    category: str = Form(...),
    description: Optional[str] = Form(None),
    level_1_description: Optional[str] = Form(None),
    level_2_description: Optional[str] = Form(None),
    level_3_description: Optional[str] = Form(None),
    level_4_description: Optional[str] = Form(None),
    level_5_description: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new competency."""
    org_id = coerce_uuid(auth.organization_id)
    comp_svc = CompetencyService(db, org_id, auth.principal)

    try:
        comp_svc.create_competency(
            competency_code=competency_code,
            competency_name=competency_name,
            category=CompetencyCategory(category),
            description=description or None,
            level_1_description=level_1_description or None,
            level_2_description=level_2_description or None,
            level_3_description=level_3_description or None,
            level_4_description=level_4_description or None,
            level_5_description=level_5_description or None,
        )
        db.commit()
        return RedirectResponse(
            url="/people/hr/competencies?success=Competency+created", status_code=303
        )
    except Exception as e:
        db.rollback()
        context = base_context(request, auth, "Add Competency", "competencies", db=db)
        context.update(
            {
                "categories": list(CompetencyCategory),
                "form_data": {
                    "competency_code": competency_code,
                    "competency_name": competency_name,
                    "category": category,
                    "description": description,
                    "level_1_description": level_1_description,
                    "level_2_description": level_2_description,
                    "level_3_description": level_3_description,
                    "level_4_description": level_4_description,
                    "level_5_description": level_5_description,
                },
                "error": str(e),
            }
        )
        return templates.TemplateResponse(
            request, "people/hr/competency_form.html", context
        )


@router.get("/competencies/{competency_id}", response_class=HTMLResponse)
def view_competency(
    request: Request,
    competency_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View competency detail."""
    org_id = coerce_uuid(auth.organization_id)
    comp_svc = CompetencyService(db, org_id)

    competency = comp_svc.get_competency(coerce_uuid(competency_id))
    if not competency:
        return RedirectResponse(
            url="/people/hr/competencies?error=Competency+not+found", status_code=303
        )

    context = base_context(
        request, auth, competency.competency_name, "competencies", db=db
    )
    context.update(
        {
            "competency": competency,
        }
    )
    return templates.TemplateResponse(
        request, "people/hr/competency_detail.html", context
    )


@router.get("/competencies/{competency_id}/edit", response_class=HTMLResponse)
def edit_competency_form(
    request: Request,
    competency_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit competency form."""
    org_id = coerce_uuid(auth.organization_id)
    comp_svc = CompetencyService(db, org_id)

    competency = comp_svc.get_competency(coerce_uuid(competency_id))
    if not competency:
        return RedirectResponse(
            url="/people/hr/competencies?error=Competency+not+found", status_code=303
        )

    context = base_context(
        request, auth, f"Edit {competency.competency_name}", "competencies", db=db
    )
    context.update(
        {
            "competency": competency,
            "categories": list(CompetencyCategory),
            "form_data": {},
        }
    )
    return templates.TemplateResponse(
        request, "people/hr/competency_form.html", context
    )


@router.post("/competencies/{competency_id}/edit", response_class=HTMLResponse)
def update_competency(
    request: Request,
    competency_id: str,
    competency_code: str = Form(...),
    competency_name: str = Form(...),
    category: str = Form(...),
    description: Optional[str] = Form(None),
    level_1_description: Optional[str] = Form(None),
    level_2_description: Optional[str] = Form(None),
    level_3_description: Optional[str] = Form(None),
    level_4_description: Optional[str] = Form(None),
    level_5_description: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a competency."""
    org_id = coerce_uuid(auth.organization_id)
    comp_svc = CompetencyService(db, org_id, auth.principal)

    try:
        comp_svc.update_competency(
            coerce_uuid(competency_id),
            {
                "competency_code": competency_code,
                "competency_name": competency_name,
                "category": CompetencyCategory(category),
                "description": description or None,
                "level_1_description": level_1_description or None,
                "level_2_description": level_2_description or None,
                "level_3_description": level_3_description or None,
                "level_4_description": level_4_description or None,
                "level_5_description": level_5_description or None,
                "is_active": _parse_bool(is_active, True),
            },
        )
        db.commit()
        return RedirectResponse(
            url="/people/hr/competencies?success=Competency+updated", status_code=303
        )
    except Exception as e:
        db.rollback()
        competency = comp_svc.get_competency(coerce_uuid(competency_id))
        context = base_context(request, auth, "Edit Competency", "competencies", db=db)
        context.update(
            {
                "competency": competency,
                "categories": list(CompetencyCategory),
                "form_data": {
                    "competency_code": competency_code,
                    "competency_name": competency_name,
                    "category": category,
                    "description": description,
                },
                "error": str(e),
            }
        )
        return templates.TemplateResponse(
            request, "people/hr/competency_form.html", context
        )
