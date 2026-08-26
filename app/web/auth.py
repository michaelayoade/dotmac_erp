"""
Web routes for authentication pages.

Provides login, admin login, and logout pages for the web interface.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.api.deps import get_db_auth_bypass
from app.db.session_context import prime_tenant_context
from app.services.auth_flow import AuthFlow
from app.services.auth_web import _sanitize_redirect_url, auth_web_service
from app.services.external_login import (
    ExternalLoginRefused,
    complete_external_login,
    start_external_login,
)
from app.web.deps import WebAuthContext, optional_web_auth

router = APIRouter(tags=["web-auth"])

_OIDC_STATE_COOKIE = "erp_oidc_state"
_OIDC_ORG_COOKIE = "erp_oidc_organization"


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    next: str = Query(default="/"),
    org: str = Query(default=""),
    auth: WebAuthContext = Depends(optional_web_auth),
    db: Session = Depends(get_db),
):
    """
    Display the login page.

    If user is already authenticated, redirect to the next URL.
    Supports ?org=<slug> for org-specific branding.
    """
    return auth_web_service.login_response(request, next, auth, db=db, org_slug=org)


@router.get("/auth/oidc/start", include_in_schema=False)
def oidc_login_start(
    request: Request,
    organization_id: UUID,
    next: str = Query(default="/"),
    db: Session = Depends(get_db_auth_bypass),
) -> RedirectResponse:
    prime_tenant_context(db, organization_id)
    try:
        started = start_external_login(
            db,
            organization_id=organization_id,
            return_to=_sanitize_redirect_url(next, request),
        )
    except ExternalLoginRefused:
        return RedirectResponse("/login?error=external_login_refused", status_code=303)
    response = RedirectResponse(started.authorization_url, status_code=303)
    secure = bool(AuthFlow.refresh_cookie_settings(db)["secure"])
    response.set_cookie(
        _OIDC_STATE_COOKIE,
        started.stored_state,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/auth/oidc/callback",
        max_age=started.ceremony_ttl_seconds,
    )
    response.set_cookie(
        _OIDC_ORG_COOKIE,
        str(organization_id),
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/auth/oidc/callback",
        max_age=started.ceremony_ttl_seconds,
    )
    return response


@router.get("/auth/oidc/callback", include_in_schema=False)
def oidc_login_callback(
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    db: Session = Depends(get_db_auth_bypass),
) -> RedirectResponse:
    try:
        organization_id = UUID(request.cookies.get(_OIDC_ORG_COOKIE, ""))
        prime_tenant_context(db, organization_id)
        completed = complete_external_login(
            db,
            organization_id=organization_id,
            code=code,
            state_parameter=state,
            stored_state=request.cookies.get(_OIDC_STATE_COOKIE, ""),
            request=request,
        )
        response = RedirectResponse(
            _sanitize_redirect_url(completed.return_to, request), status_code=303
        )
        AuthFlow.set_auth_cookies(db, response, completed.token_payload)
    except (ExternalLoginRefused, ValueError):
        response = RedirectResponse(
            "/login?error=external_login_refused", status_code=303
        )
    response.delete_cookie(_OIDC_STATE_COOKIE, path="/auth/oidc/callback")
    response.delete_cookie(_OIDC_ORG_COOKIE, path="/auth/oidc/callback")
    return response


@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(
    request: Request,
    next: str = Query(default="/admin"),
    auth: WebAuthContext = Depends(optional_web_auth),
    db: Session = Depends(get_db),
):
    """
    Display the admin login page.

    If user is already authenticated with admin role, redirect to admin dashboard.
    If authenticated without admin role, show error.
    """
    return auth_web_service.admin_login_response(request, next, auth, db=db)


@router.get("/logout", response_class=HTMLResponse)
def logout_page(
    request: Request,
    next: str = Query(default="/login"),
):
    """
    Log out the user by revoking session and clearing cookies.

    Revokes only the ERP-owned session and clears ERP-owned cookies.
    """
    return auth_web_service.logout_response(request, next)


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(
    request: Request,
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """
    Display the forgot password page.
    """
    return auth_web_service.forgot_password_response(request, auth)


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(
    request: Request,
    token: str = Query(...),
    next: str | None = Query(default=None),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """
    Display the reset password page.
    """
    return auth_web_service.reset_password_response(request, token, auth, next_url=next)


@router.get("/reset-password-required", response_class=HTMLResponse)
def reset_password_required_page(
    request: Request,
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """
    Display the required password reset page (after first login).
    """
    return auth_web_service.reset_password_required_response(request, auth)
