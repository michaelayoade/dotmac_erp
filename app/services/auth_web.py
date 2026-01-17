"""
Auth web view service.

Provides response builders for auth-related web routes.
"""

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db import SessionLocal
from app.templates import templates
from app.services.auth_flow import AuthFlow
from app.web.deps import brand_context, WebAuthContext


class AuthWebService:
    """View service for auth web routes."""

    def login_response(
        self,
        request: Request,
        next_url: str,
        auth: WebAuthContext,
    ) -> HTMLResponse | RedirectResponse:
        if auth.is_authenticated:
            return RedirectResponse(url=next_url, status_code=302)

        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "title": "Login",
                "brand": brand_context(),
                "next": next_url,
            },
        )

    def admin_login_response(
        self,
        request: Request,
        next_url: str,
        auth: WebAuthContext,
    ) -> HTMLResponse | RedirectResponse:
        if auth.is_authenticated and "admin" in auth.roles:
            return RedirectResponse(url=next_url, status_code=302)

        return templates.TemplateResponse(
            request,
            "admin_login.html",
            {
                "title": "Admin Login",
                "brand": brand_context(),
                "next": next_url,
                "is_authenticated": auth.is_authenticated,
                "has_admin_role": "admin" in auth.roles if auth.is_authenticated else False,
            },
        )

    def logout_response(self, next_url: str) -> RedirectResponse:
        response = RedirectResponse(url=next_url, status_code=302)
        response.delete_cookie(key="access_token", path="/")
        db = SessionLocal()
        try:
            settings = AuthFlow.refresh_cookie_settings(db)
        finally:
            db.close()
        response.delete_cookie(
            key=settings["key"],
            domain=settings["domain"],
            path=settings["path"],
        )
        return response

    def forgot_password_response(
        self,
        request: Request,
        auth: WebAuthContext,
    ) -> HTMLResponse | RedirectResponse:
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

    def reset_password_response(
        self,
        request: Request,
        token: str,
        auth: WebAuthContext,
    ) -> HTMLResponse | RedirectResponse:
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


auth_web_service = AuthWebService()
