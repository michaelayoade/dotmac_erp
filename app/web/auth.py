"""
Web routes for authentication pages.

Provides login, admin login, and logout pages for the web interface.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from app.services.auth_web import auth_web_service
from app.web.deps import optional_web_auth, WebAuthContext


router = APIRouter(tags=["web-auth"])


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    next: str = Query(default="/"),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """
    Display the login page.

    If user is already authenticated, redirect to the next URL.
    """
    return auth_web_service.login_response(request, next, auth)


@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(
    request: Request,
    next: str = Query(default="/admin"),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """
    Display the admin login page.

    If user is already authenticated with admin role, redirect to admin dashboard.
    If authenticated without admin role, show error.
    """
    return auth_web_service.admin_login_response(request, next, auth)


@router.get("/logout", response_class=HTMLResponse)
def logout_page(
    request: Request,
    next: str = Query(default="/login"),
):
    """
    Log out the user by clearing cookies and redirecting to login.
    """
    return auth_web_service.logout_response(next)


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
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """
    Display the reset password page.
    """
    return auth_web_service.reset_password_response(request, token, auth)
