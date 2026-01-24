from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.templates import templates
from app.web.deps import (
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


@router.get("/gl/accounts/new", tags=["web"])
def gl_accounts_new_redirect(
    auth: WebAuthContext = Depends(require_finance_access),
):
    """Redirect legacy GL account create URL to the finance web route."""
    return RedirectResponse(url="/finance/gl/accounts/new", status_code=302)
