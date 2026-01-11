"""
Web routes for authentication pages.

Provides login, admin login, and logout pages for the web interface.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.templates import templates
from app.web.deps import brand_context, get_db, optional_web_auth, WebAuthContext


router = APIRouter(tags=["web-auth"])


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    next: str = Query(default="/dashboard"),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """
    Display the login page.

    If user is already authenticated, redirect to the next URL.
    """
    # If already authenticated, redirect
    if auth.is_authenticated:
        return RedirectResponse(url=next, status_code=302)

    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "title": "Login",
            "brand": brand_context(),
            "next": next,
        },
    )


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
    # If already authenticated as admin, redirect
    if auth.is_authenticated and "admin" in auth.roles:
        return RedirectResponse(url=next, status_code=302)

    return templates.TemplateResponse(
        request,
        "admin_login.html",
        {
            "title": "Admin Login",
            "brand": brand_context(),
            "next": next,
            "is_authenticated": auth.is_authenticated,
            "has_admin_role": "admin" in auth.roles if auth.is_authenticated else False,
        },
    )


@router.get("/logout", response_class=HTMLResponse)
def logout_page(
    request: Request,
    next: str = Query(default="/login"),
):
    """
    Log out the user by clearing cookies and redirecting to login.
    """
    response = RedirectResponse(url=next, status_code=302)

    # Clear access token cookie
    response.delete_cookie(key="access_token", path="/")

    # Clear refresh token cookie
    response.delete_cookie(key="refresh_token", path="/")

    return response


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(
    request: Request,
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """
    Display the forgot password page.
    """
    if auth.is_authenticated:
        return RedirectResponse(url="/dashboard", status_code=302)

    return templates.TemplateResponse(
        request,
        "forgot_password.html",
        {
            "title": "Forgot Password",
            "brand": brand_context(),
        },
    )


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(
    request: Request,
    token: str = Query(...),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """
    Display the reset password page.
    """
    if auth.is_authenticated:
        return RedirectResponse(url="/dashboard", status_code=302)

    return templates.TemplateResponse(
        request,
        "reset_password.html",
        {
            "title": "Reset Password",
            "brand": brand_context(),
            "token": token,
        },
    )
