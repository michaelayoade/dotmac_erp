"""Skills Catalog routes."""

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.hr import SkillCategory
from app.services.people.hr import SkillService
from app.services.common import coerce_uuid
from app.templates import templates
from app.web.deps import base_context, get_db, require_hr_access, WebAuthContext

from ._common import _parse_bool


router = APIRouter()


# =============================================================================
# Skills Catalog
# =============================================================================


@router.get("/skills", response_class=HTMLResponse)
def list_skills(
    request: Request,
    category: Optional[str] = None,
    search: Optional[str] = None,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Skills catalog page."""
    org_id = coerce_uuid(auth.organization_id)
    skill_svc = SkillService(db, org_id)

    cat = SkillCategory(category) if category else None
    skills = skill_svc.list_skills(category=cat, search=search, active_only=False)

    context = base_context(request, auth, "Skills Catalog", "skills", db=db)
    context.update({
        "skills": skills,
        "categories": list(SkillCategory),
        "selected_category": category,
        "search": search,
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/skills_catalog.html", context)


@router.get("/skills/new", response_class=HTMLResponse)
def new_skill_catalog_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New skill form."""
    context = base_context(request, auth, "Add Skill", "skills", db=db)
    context.update({
        "categories": list(SkillCategory),
        "form_data": {},
        "skill": None,
    })
    return templates.TemplateResponse(request, "people/hr/skill_catalog_form.html", context)


@router.post("/skills/new", response_class=HTMLResponse)
def create_skill(
    request: Request,
    skill_name: str = Form(...),
    category: str = Form(...),
    description: Optional[str] = Form(None),
    is_language: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new skill in the catalog."""
    org_id = coerce_uuid(auth.organization_id)
    skill_svc = SkillService(db, org_id)

    try:
        skill_svc.create_skill(
            skill_name=skill_name,
            category=SkillCategory(category),
            description=description or None,
            is_language=_parse_bool(is_language),
        )
        db.commit()
        return RedirectResponse(url="/people/hr/skills?success=Skill+created", status_code=303)
    except Exception as e:
        db.rollback()
        context = base_context(request, auth, "Add Skill", "skills", db=db)
        context.update({
            "categories": list(SkillCategory),
            "form_data": {
                "skill_name": skill_name,
                "category": category,
                "description": description,
                "is_language": is_language,
            },
            "skill": None,
            "error": str(e),
        })
        return templates.TemplateResponse(request, "people/hr/skill_catalog_form.html", context)


@router.get("/skills/{skill_id}/edit", response_class=HTMLResponse)
def edit_skill_catalog_form(
    request: Request,
    skill_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit skill form."""
    org_id = coerce_uuid(auth.organization_id)
    skill_svc = SkillService(db, org_id)
    skill = skill_svc.get_skill(coerce_uuid(skill_id))

    context = base_context(request, auth, "Edit Skill", "skills", db=db)
    context.update({
        "categories": list(SkillCategory),
        "skill": skill,
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/skill_catalog_form.html", context)


@router.post("/skills/{skill_id}/edit", response_class=HTMLResponse)
def update_skill(
    request: Request,
    skill_id: str,
    skill_name: str = Form(...),
    category: str = Form(...),
    description: Optional[str] = Form(None),
    is_language: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update an existing skill in the catalog."""
    org_id = coerce_uuid(auth.organization_id)
    skill_svc = SkillService(db, org_id)
    skill_uuid = coerce_uuid(skill_id)

    try:
        skill_svc.update_skill(
            skill_id=skill_uuid,
            skill_name=skill_name,
            category=SkillCategory(category),
            description=description or None,
            is_active=_parse_bool(is_active),
        )
        db.commit()
        return RedirectResponse(url="/people/hr/skills?success=Skill+updated", status_code=303)
    except Exception as e:
        db.rollback()
        skill = skill_svc.get_skill(skill_uuid)
        context = base_context(request, auth, "Edit Skill", "skills", db=db)
        context.update({
            "categories": list(SkillCategory),
            "skill": skill,
            "form_data": {
                "skill_name": skill_name,
                "category": category,
                "description": description,
                "is_language": is_language,
                "is_active": is_active,
            },
            "error": str(e),
        })
        return templates.TemplateResponse(request, "people/hr/skill_catalog_form.html", context)


@router.post("/skills/{skill_id}/delete", response_class=HTMLResponse)
def delete_skill(
    request: Request,
    skill_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a skill from the catalog."""
    org_id = coerce_uuid(auth.organization_id)
    skill_svc = SkillService(db, org_id)

    try:
        skill_svc.delete_skill(coerce_uuid(skill_id))
        db.commit()
        return RedirectResponse(url="/people/hr/skills?success=Skill+deleted", status_code=303)
    except Exception as e:
        db.rollback()
        return RedirectResponse(url=f"/people/hr/skills?error={str(e)}", status_code=303)
