"""
Module Settings Web Routes.

Configuration pages for inventory, support, projects, fleet, and procurement.
"""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.services.finance.settings_web import settings_web_service
from app.services.module_settings_web import (
    MODULE_SETTINGS_BY_KEY,
    module_settings_web_service,
)
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_async_db_for_org,
    get_db_for_org,
    require_finance_access,
    require_settings_access,
)

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
    "help": ModuleSettingsHandler(
        get_context=module_settings_web_service.get_help_context,
        update_settings=module_settings_web_service.update_help_settings,
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
    "expense": ModuleSettingsHandler(
        get_context=module_settings_web_service.get_expense_context,
        update_settings=module_settings_web_service.update_expense_settings,
    ),
    "fixed-assets": ModuleSettingsHandler(
        get_context=module_settings_web_service.get_fixed_assets_context,
        update_settings=module_settings_web_service.update_fixed_assets_settings,
    ),
}


def _consume_settings_success(request: Request, module_key: str) -> bool:
    """Consume one-time success flag for a specific settings module."""
    try:
        success_module = request.session.pop("settings_success_module", None)
    except AssertionError:
        return False
    return bool(success_module == module_key)


def _mark_settings_success(request: Request, module_key: str) -> None:
    """Mark a settings save success for next GET request."""
    try:
        request.session["settings_success_module"] = module_key
    except AssertionError:
        return


def _normalize_form(form) -> dict[str, str]:
    """Normalize form data to dict of strings."""
    if form is None:
        return {}
    return {key: value if isinstance(value, str) else "" for key, value in form.items()}


@router.get("", response_class=HTMLResponse)
def settings_index(
    request: Request,
    auth: WebAuthContext = Depends(require_settings_access),
    db: Session = Depends(get_db_for_org),
):
    """Settings hub page."""
    context = base_context(request, auth, "Settings", "settings", db=db)
    context.update(module_settings_web_service.get_hub_context(auth.organization_id))

    return templates.TemplateResponse(request, "settings/index.html", context)


@router.get("/numbering", response_class=HTMLResponse)
async def finance_numbering_sequences(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_async_db_for_org),
    sync_db: Session = Depends(get_db_for_org),
):
    """Finance numbering sequences page exposed from the shared settings URL."""
    result = await settings_web_service.get_numbering_list_context(
        db, auth.organization_id
    )

    context = base_context(request, auth, "Numbering Sequences", "settings", db=sync_db)
    context.update(result)

    return templates.TemplateResponse(
        request, "finance/settings/numbering.html", context
    )


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
    db: Session = Depends(get_db_for_org),
):
    """Module settings page."""
    config, handler = _get_module_config(module_key)
    context = base_context(request, auth, config.page_title, "settings", db=db)
    context.update(handler.get_context(db, auth.organization_id))
    context["settings_saved"] = _consume_settings_success(request, module_key)
    return templates.TemplateResponse(request, config.template, context)


@router.post("/{module_key}", response_class=HTMLResponse)
async def update_module_settings(
    module_key: str,
    request: Request,
    auth: WebAuthContext = Depends(require_settings_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle module settings update."""
    config, handler = _get_module_config(module_key)
    raw_form = getattr(request.state, "csrf_form", None)
    if raw_form is None:
        raw_form = await request.form()
    form = _normalize_form(raw_form)

    # Expense settings need multi-select for allowed account IDs
    if module_key == "expense":
        allowed_ids = raw_form.getlist("expense_allowed_account_ids")
        success, error = handler.update_settings(
            db, auth.organization_id, form, allowed_account_ids=allowed_ids
        )
    else:
        success, error = handler.update_settings(db, auth.organization_id, form)

    if error:
        context = base_context(request, auth, config.page_title, "settings", db=db)
        context.update(handler.get_context(db, auth.organization_id))
        context["error"] = error
        return templates.TemplateResponse(request, config.template, context)

    _mark_settings_success(request, module_key)
    return RedirectResponse(url=f"/settings/{module_key}", status_code=303)
