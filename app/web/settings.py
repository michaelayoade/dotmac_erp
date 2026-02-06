"""
Module Settings Web Routes.

Configuration pages for inventory, support, projects, fleet, and procurement.
"""

from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.module_settings_web import (
    MODULE_SETTINGS_BY_KEY,
    module_settings_web_service,
)
from app.templates import templates
from app.web.deps import base_context, get_db, require_settings_access, WebAuthContext


router = APIRouter(prefix="/settings", tags=["settings-web"])


@dataclass(frozen=True)
class ModuleSettingsHandler:
    get_context: Callable
    update_settings: Callable


MODULE_SETTINGS_HANDLERS = {
    "support": ModuleSettingsHandler(
        get_context=module_settings_web_service.get_support_context,
        update_settings=module_settings_web_service.update_support_settings,
    ),
    "inventory": ModuleSettingsHandler(
        get_context=module_settings_web_service.get_inventory_context,
        update_settings=module_settings_web_service.update_inventory_settings,
    ),
    "projects": ModuleSettingsHandler(
        get_context=module_settings_web_service.get_projects_context,
        update_settings=module_settings_web_service.update_projects_settings,
    ),
    "fleet": ModuleSettingsHandler(
        get_context=module_settings_web_service.get_fleet_context,
        update_settings=module_settings_web_service.update_fleet_settings,
    ),
    "procurement": ModuleSettingsHandler(
        get_context=module_settings_web_service.get_procurement_context,
        update_settings=module_settings_web_service.update_procurement_settings,
    ),
}


def _normalize_form(form) -> dict[str, str]:
    """Normalize form data to dict of strings."""
    if form is None:
        return {}
    return {key: value if isinstance(value, str) else "" for key, value in form.items()}


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def settings_index(
    request: Request,
    auth: WebAuthContext = Depends(require_settings_access),
    db: Session = Depends(get_db),
):
    """Settings hub page."""
    context = base_context(request, auth, "Settings", "settings", db=db)
    context.update(module_settings_web_service.get_hub_context(auth.organization_id))

    return templates.TemplateResponse(request, "settings/index.html", context)


def _get_module_config(module_key: str):
    config = MODULE_SETTINGS_BY_KEY.get(module_key)
    handler = MODULE_SETTINGS_HANDLERS.get(module_key)
    if not config or not handler:
        raise HTTPException(status_code=404, detail="Settings page not found")
    return config, handler


@router.get("/{module_key}", response_class=HTMLResponse)
def module_settings(
    module_key: str,
    request: Request,
    auth: WebAuthContext = Depends(require_settings_access),
    db: Session = Depends(get_db),
):
    """Module settings page."""
    config, handler = _get_module_config(module_key)
    context = base_context(request, auth, config.page_title, "settings", db=db)
    context.update(handler.get_context(db, auth.organization_id))
    return templates.TemplateResponse(request, config.template, context)


@router.post("/{module_key}", response_class=HTMLResponse)
async def update_module_settings(
    module_key: str,
    request: Request,
    auth: WebAuthContext = Depends(require_settings_access),
    db: Session = Depends(get_db),
):
    """Handle module settings update."""
    config, handler = _get_module_config(module_key)
    raw_form = getattr(request.state, "csrf_form", None)
    if raw_form is None:
        raw_form = await request.form()
    form = _normalize_form(raw_form)

    success, error = handler.update_settings(db, auth.organization_id, form)

    if error:
        context = base_context(request, auth, config.page_title, "settings", db=db)
        context.update(handler.get_context(db, auth.organization_id))
        context["error"] = error
        return templates.TemplateResponse(request, config.template, context)

    return RedirectResponse(url=f"/settings/{module_key}?success=1", status_code=303)
