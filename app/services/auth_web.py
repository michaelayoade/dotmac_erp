"""
Auth web view service.

Provides response builders for auth-related web routes.

SSO Support:
When SSO is enabled and this app is an SSO client (not provider),
login pages redirect to the SSO provider for authentication.
"""

import logging
from urllib.parse import urlencode, urlparse

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.db import SessionLocal, get_auth_db_session
from app.templates import templates
from app.services.auth_flow import AuthFlow, hash_session_token
from app.web.deps import brand_context, WebAuthContext

logger = logging.getLogger(__name__)


def _is_safe_redirect_url(url: str, request: Request) -> bool:
    """Check if a redirect URL is safe (prevents open redirect attacks).

    A URL is considered safe if:
    - It's a relative path (starts with /)
    - It's an absolute URL to the same host
    - For SSO, it's to an allowed SSO domain

    Args:
        url: The redirect URL to validate
        request: The current request for host comparison

    Returns:
        True if the URL is safe to redirect to
    """
    if not url:
        return False

    # Relative URLs are safe
    if url.startswith("/") and not url.startswith("//"):
        return True

    # Parse the URL
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    # No scheme means it's relative
    if not parsed.scheme:
        return url.startswith("/") and not url.startswith("//")

    # Only allow http/https
    if parsed.scheme not in ("http", "https"):
        return False

    # Get request host for comparison
    request_host = request.url.netloc.split(":")[0].lower()
    target_host = (parsed.netloc.split(":")[0]).lower()

    # Same host is always safe
    if target_host == request_host:
        return True

    # For SSO, allow redirect to SSO cookie domain
    if settings.sso_enabled and settings.sso_cookie_domain:
        sso_domain = settings.sso_cookie_domain.lstrip(".")
        if target_host == sso_domain or target_host.endswith(f".{sso_domain}"):
            return True

    return False


def _sanitize_redirect_url(url: str, request: Request, default: str = "/") -> str:
    """Sanitize a redirect URL to prevent open redirect attacks.

    Returns the URL if safe, otherwise returns the default.
    """
    if _is_safe_redirect_url(url, request):
        return url
    logger.warning("Blocked unsafe redirect URL: %s", url)
    return default


class AuthWebService:
    """View service for auth web routes."""

    def _get_sso_login_url(self, request: Request, next_url: str) -> str | None:
        """Get SSO provider login URL if this is an SSO client.

        Returns None if SSO is not enabled or this is the SSO provider.
        """
        if not settings.sso_enabled:
            return None
        if settings.sso_provider_mode:
            # This is the SSO provider, handle login locally
            return None
        if not settings.sso_provider_url:
            # No SSO provider URL configured
            return None

        # Build redirect URL back to this app
        # Use the current request's URL as the base for the redirect
        scheme = request.url.scheme
        host = request.url.netloc
        redirect_url = f"{scheme}://{host}{next_url}"

        # Build SSO provider login URL with redirect parameter
        params = urlencode({"next": redirect_url})
        return f"{settings.sso_provider_url}/login?{params}"

    def login_response(
        self,
        request: Request,
        next_url: str,
        auth: WebAuthContext,
    ) -> HTMLResponse | RedirectResponse:
        # Sanitize redirect URL to prevent open redirect attacks
        safe_next_url = _sanitize_redirect_url(next_url, request, default="/")

        if auth.is_authenticated:
            return RedirectResponse(url=safe_next_url, status_code=302)

        # SSO: redirect to SSO provider for login (use sanitized URL)
        sso_login_url = self._get_sso_login_url(request, safe_next_url)
        if sso_login_url:
            return RedirectResponse(url=sso_login_url, status_code=302)

        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "title": "Login",
                "brand": brand_context(),
                "next": safe_next_url,
            },
        )

    def admin_login_response(
        self,
        request: Request,
        next_url: str,
        auth: WebAuthContext,
    ) -> HTMLResponse | RedirectResponse:
        # Sanitize redirect URL to prevent open redirect attacks
        safe_next_url = _sanitize_redirect_url(next_url, request, default="/admin")

        if auth.is_authenticated and "admin" in auth.roles:
            return RedirectResponse(url=safe_next_url, status_code=302)

        # SSO: redirect to SSO provider for admin login
        sso_login_url = self._get_sso_login_url(request, safe_next_url)
        if sso_login_url:
            return RedirectResponse(url=sso_login_url, status_code=302)

        return templates.TemplateResponse(
            request,
            "admin_login.html",
            {
                "title": "Admin Login",
                "brand": brand_context(),
                "next": safe_next_url,
                "is_authenticated": auth.is_authenticated,
                "has_admin_role": "admin" in auth.roles if auth.is_authenticated else False,
            },
        )

    def logout_response(self, request: Request, next_url: str) -> RedirectResponse:
        """Revoke session and clear auth cookies.

        For SSO: revokes session in shared database and clears cookies
        with SSO domain so logout propagates across all apps.
        """
        # Sanitize redirect URL to prevent open redirect attacks
        safe_next_url = _sanitize_redirect_url(next_url, request, default="/login")
        response = RedirectResponse(url=safe_next_url, status_code=302)

        db = SessionLocal()
        try:
            cookie_settings = AuthFlow.refresh_cookie_settings(db)
            access_settings = AuthFlow.access_cookie_settings(db)

            # Get refresh token from cookie to revoke the session
            refresh_token = request.cookies.get(cookie_settings["key"])
            if refresh_token:
                self._revoke_session(refresh_token, db)

        finally:
            db.close()

        # Clear access token cookie with proper domain (for SSO)
        response.delete_cookie(
            key=access_settings["key"],
            domain=access_settings["domain"],
            path=access_settings["path"],
        )

        # Clear refresh token cookie
        response.delete_cookie(
            key=cookie_settings["key"],
            domain=cookie_settings["domain"],
            path=cookie_settings["path"],
        )

        return response

    def _revoke_session(self, refresh_token: str, db) -> None:
        """Revoke the session associated with the refresh token.

        For SSO clients, revokes in the shared auth database.
        """
        from datetime import datetime, timezone
        from app.models.auth import Session as AuthSession, SessionStatus

        token_hash = hash_session_token(refresh_token)

        # Determine which database to use for session revocation
        if settings.sso_enabled and not settings.sso_provider_mode:
            # SSO client - revoke in shared auth database
            auth_db = get_auth_db_session()
            try:
                session = (
                    auth_db.query(AuthSession)
                    .filter(AuthSession.token_hash == token_hash)
                    .filter(AuthSession.revoked_at.is_(None))
                    .first()
                )
                if session:
                    session.status = SessionStatus.revoked
                    session.revoked_at = datetime.now(timezone.utc)
                    auth_db.commit()
                    logger.info("SSO session revoked: %s", session.id)
            except Exception as e:
                logger.warning("Failed to revoke SSO session: %s", e)
                auth_db.rollback()
            finally:
                auth_db.close()
        else:
            # SSO provider or non-SSO - revoke in local database
            try:
                session = (
                    db.query(AuthSession)
                    .filter(AuthSession.token_hash == token_hash)
                    .filter(AuthSession.revoked_at.is_(None))
                    .first()
                )
                if session:
                    session.status = SessionStatus.revoked
                    session.revoked_at = datetime.now(timezone.utc)
                    db.commit()
                    logger.info("Session revoked: %s", session.id)
            except Exception as e:
                logger.warning("Failed to revoke session: %s", e)
                db.rollback()

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

    def reset_password_required_response(
        self,
        request: Request,
        auth: WebAuthContext,
    ) -> HTMLResponse | RedirectResponse:
        if auth.is_authenticated:
            return RedirectResponse(url="/dashboard", status_code=302)

        return templates.TemplateResponse(
            request,
            "reset_password_required.html",
            {
                "title": "Reset Password",
                "brand": brand_context(),
            },
        )


auth_web_service = AuthWebService()
