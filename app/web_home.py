from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.templates import templates
from app.web.deps import (
    base_context,
    get_db,
    optional_web_auth,
    require_web_auth,
    require_finance_access,
    WebAuthContext,
    brand_context,
    landing_content,
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
    return templates.TemplateResponse(
        request, "operations/dashboard.html", context
    )


@router.get("/gl/accounts/new", tags=["web"])
def gl_accounts_new_redirect(
    auth: WebAuthContext = Depends(require_finance_access),
):
    """Redirect legacy GL account create URL to the finance web route."""
    return RedirectResponse(url="/finance/gl/accounts/new", status_code=302)
