from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.services.help_center import build_help_center_payload
from app.services.settings_spec import resolve_value
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    brand_context,
    get_db,
    landing_content,
    optional_web_auth,
    require_finance_access,
    require_web_auth,
)

router = APIRouter()


@router.get("/", tags=["web"], response_class=HTMLResponse)
def home(
    request: Request,
    auth: WebAuthContext = Depends(optional_web_auth),
):
    brand = brand_context()
    if auth.is_authenticated:
        return templates.TemplateResponse(
            request,
            "module_select.html",
            {
                "title": f"{brand['name']} | Select Module",
                "brand": brand,
                "user": auth.user,
                "accessible_modules": auth.accessible_modules,
            },
        )

    content = landing_content()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "title": brand["name"],
            "brand": brand,
            "content": content,
            "user": auth.user,
        },
    )


@router.get("/dashboard", tags=["web"])
def dashboard_redirect(
    auth: WebAuthContext = Depends(require_web_auth),
):
    """
    Root dashboard redirect.

    Redirects authenticated users to the module selector page.
    """
    return RedirectResponse(url="/", status_code=302)


@router.get("/help", tags=["web"], response_class=HTMLResponse)
def help_center(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """App-wide help center with module manuals and end-to-end journeys."""
    context = base_context(request, auth, "Help & Training", "help", db=db)
    content_overrides = resolve_value(
        db, SettingDomain.settings, "help_center_content_json"
    )
    context.update(
        build_help_center_payload(
            accessible_modules=auth.accessible_modules,
            roles=auth.roles,
            scopes=auth.scopes,
            is_admin=auth.is_admin,
            overrides=content_overrides
            if isinstance(content_overrides, dict)
            else None,
        )
    )
    return templates.TemplateResponse(request, "help_center.html", context)


@router.get("/operations/dashboard", tags=["web"], response_class=HTMLResponse)
def operations_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Operations module dashboard with cross-module overview."""
    from app.services.operations.dashboard_web import operations_dashboard_web_service

    context = base_context(request, auth, "Operations Dashboard", "dashboard", db=db)
    context.update(
        operations_dashboard_web_service.dashboard_context(db, auth.organization_id)
    )
    return templates.TemplateResponse(request, "operations/dashboard.html", context)


@router.get("/gl/accounts/new", tags=["web"])
def gl_accounts_new_redirect(
    auth: WebAuthContext = Depends(require_finance_access),
):
    """Redirect legacy GL account create URL to the finance web route."""
    return RedirectResponse(url="/finance/gl/accounts/new", status_code=302)
