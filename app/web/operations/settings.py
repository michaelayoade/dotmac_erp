"""
Operations Settings Web Routes.

Configuration pages for Operations modules including Support, Inventory, and Projects.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.operations.settings_web import operations_settings_web_service
from app.templates import templates
from app.web.deps import base_context, get_db, require_operations_access, WebAuthContext


router = APIRouter(prefix="/settings", tags=["operations-settings"])


def _normalize_form(form) -> dict[str, str]:
    """Normalize form data to dict of strings."""
    if form is None:
        return {}
    return {key: value if isinstance(value, str) else "" for key, value in form.items()}


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def settings_index(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Operations settings hub page."""
    context = base_context(request, auth, "Settings", "settings", db=db)
    context.update(operations_settings_web_service.get_hub_context(auth.organization_id))

    return templates.TemplateResponse(request, "operations/settings/index.html", context)


# ========== Support Settings ==========


@router.get("/support", response_class=HTMLResponse)
def support_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Support/SLA settings page."""
    context = base_context(request, auth, "Support Settings", "settings", db=db)
    context.update(operations_settings_web_service.get_support_context(db, auth.organization_id))

    return templates.TemplateResponse(request, "operations/settings/support.html", context)


@router.post("/support", response_class=HTMLResponse)
async def update_support_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Handle support settings update."""
    raw_form = getattr(request.state, "csrf_form", None)
    if raw_form is None:
        raw_form = await request.form()
    form = _normalize_form(raw_form)

    success, error = operations_settings_web_service.update_support_settings(
        db, auth.organization_id, form
    )

    if error:
        context = base_context(request, auth, "Support Settings", "settings", db=db)
        context.update(operations_settings_web_service.get_support_context(db, auth.organization_id))
        context["error"] = error
        return templates.TemplateResponse(request, "operations/settings/support.html", context)

    return RedirectResponse(url="/operations/settings/support?success=1", status_code=303)


# ========== Inventory Settings ==========


@router.get("/inventory", response_class=HTMLResponse)
def inventory_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Inventory settings page."""
    context = base_context(request, auth, "Inventory Settings", "settings", db=db)
    context.update(operations_settings_web_service.get_inventory_context(db, auth.organization_id))

    return templates.TemplateResponse(request, "operations/settings/inventory.html", context)


@router.post("/inventory", response_class=HTMLResponse)
async def update_inventory_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Handle inventory settings update."""
    raw_form = getattr(request.state, "csrf_form", None)
    if raw_form is None:
        raw_form = await request.form()
    form = _normalize_form(raw_form)

    success, error = operations_settings_web_service.update_inventory_settings(
        db, auth.organization_id, form
    )

    if error:
        context = base_context(request, auth, "Inventory Settings", "settings", db=db)
        context.update(operations_settings_web_service.get_inventory_context(db, auth.organization_id))
        context["error"] = error
        return templates.TemplateResponse(request, "operations/settings/inventory.html", context)

    return RedirectResponse(url="/operations/settings/inventory?success=1", status_code=303)


# ========== Projects Settings ==========


@router.get("/projects", response_class=HTMLResponse)
def projects_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Projects settings page."""
    context = base_context(request, auth, "Projects Settings", "settings", db=db)
    context.update(operations_settings_web_service.get_projects_context(db, auth.organization_id))

    return templates.TemplateResponse(request, "operations/settings/projects.html", context)


@router.post("/projects", response_class=HTMLResponse)
async def update_projects_settings(
    request: Request,
    auth: WebAuthContext = Depends(require_operations_access),
    db: Session = Depends(get_db),
):
    """Handle projects settings update."""
    raw_form = getattr(request.state, "csrf_form", None)
    if raw_form is None:
        raw_form = await request.form()
    form = _normalize_form(raw_form)

    success, error = operations_settings_web_service.update_projects_settings(
        db, auth.organization_id, form
    )

    if error:
        context = base_context(request, auth, "Projects Settings", "settings", db=db)
        context.update(operations_settings_web_service.get_projects_context(db, auth.organization_id))
        context["error"] = error
        return templates.TemplateResponse(request, "operations/settings/projects.html", context)

    return RedirectResponse(url="/operations/settings/projects?success=1", status_code=303)
