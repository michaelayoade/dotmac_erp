"""
Auth web view service.

Provides response builders for auth-related web routes.

ERP remains the sole issuer of its sessions and cookies. External protocol
verification is deliberately delegated to the published shared adapter; this
view service retains only ERP navigation and rendering decisions.
"""

import logging
from datetime import timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from typing import TYPE_CHECKING
from urllib.parse import urlencode, urlparse

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.services.auth_flow import AuthFlow, hash_session_token
from app.templates import templates
from app.web.deps import WebAuthContext, brand_context, org_brand_context

logger = logging.getLogger(__name__)


def _apply_no_cache_headers(response: HTMLResponse) -> HTMLResponse:
    """Prevent auth pages from being cached across cookie-varying sessions."""
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Vary"] = "Cookie"
    return response


def _is_safe_redirect_url(url: str, request: Request) -> bool:
    """Check if a redirect URL is safe (prevents open redirect attacks).

    A URL is considered safe if:
    - It's a relative path (starts with /)
    - It's an absolute URL to the same host

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

    return target_host == request_host


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

    def _get_brand_for_login(
        self,
        db: "Session | None",
        org_slug: str = "",
    ) -> dict:
        """Get brand context for login pages.

        For single-tenant deployments (1 org), auto-detect the org branding.
        For multi-tenant, support ?org=<slug> query param.
        Falls back to system defaults.
        """
        if not db:
            return brand_context()

        try:
            from sqlalchemy import func, select

            from app.models.finance.core_org.organization import Organization

            if org_slug:
                # Look up by slug
                stmt = select(Organization.organization_id).where(
                    Organization.slug == org_slug,
                )
                org_id = db.scalar(stmt)
                if org_id:
                    return org_brand_context(db, org_id)
            else:
                # Auto-detect: if exactly 1 org exists, use its branding
                count = db.scalar(select(func.count(Organization.organization_id)))
                if count == 1:
                    org_id = db.scalar(select(Organization.organization_id))
                    if org_id:
                        return org_brand_context(db, org_id)
        except Exception:
            logger.debug("Could not fetch org branding for login", exc_info=True)

        return brand_context()

    def login_response(
        self,
        request: Request,
        next_url: str,
        auth: WebAuthContext,
        *,
        db: "Session | None" = None,
        org_slug: str = "",
    ) -> HTMLResponse | RedirectResponse:
        # Sanitize redirect URL to prevent open redirect attacks
        safe_next_url = _sanitize_redirect_url(next_url, request, default="/")

        if auth.is_authenticated:
            return RedirectResponse(url=safe_next_url, status_code=302)

        brand = self._get_brand_for_login(db, org_slug)

        response = templates.TemplateResponse(
            request,
            "login.html",
            {
                "title": "Login",
                "brand": brand,
                "next": safe_next_url,
                "app_version": settings.app_version,
            },
        )
        return _apply_no_cache_headers(response)

    def admin_login_response(
        self,
        request: Request,
        next_url: str,
        auth: WebAuthContext,
        *,
        db: "Session | None" = None,
    ) -> HTMLResponse | RedirectResponse:
        # Sanitize redirect URL to prevent open redirect attacks
        safe_next_url = _sanitize_redirect_url(next_url, request, default="/admin")

        if auth.is_authenticated and "admin" in auth.roles:
            return RedirectResponse(url=safe_next_url, status_code=302)

        brand = self._get_brand_for_login(db)

        response = templates.TemplateResponse(
            request,
            "admin_login.html",
            {
                "title": "Admin Login",
                "brand": brand,
                "next": safe_next_url,
                "app_version": settings.app_version,
                "is_authenticated": auth.is_authenticated,
                "has_admin_role": "admin" in auth.roles
                if auth.is_authenticated
                else False,
            },
        )
        return _apply_no_cache_headers(response)

    def logout_response(self, request: Request, next_url: str) -> RedirectResponse:
        """Revoke session and clear auth cookies.

        Only the ERP-local session is revoked. Identity-provider single logout
        is intentionally outside ERP's session authority.
        """
        # Sanitize redirect URL to prevent open redirect attacks
        safe_next_url = _sanitize_redirect_url(next_url, request, default="/login")
        response = RedirectResponse(url=safe_next_url, status_code=302)

        cookie_settings: dict = {
            "key": "refresh_token",
            "domain": None,
            "path": "/",
        }
        access_settings: dict = {
            "key": "access_token",
            "domain": None,
            "path": "/",
        }

        db = SessionLocal()
        try:
            try:
                cookie_settings = AuthFlow.refresh_cookie_settings(db)
            except Exception:
                logger.warning(
                    "Failed to load refresh cookie settings; using defaults",
                    exc_info=True,
                )
            try:
                access_settings = AuthFlow.access_cookie_settings(db)
            except Exception:
                logger.warning(
                    "Failed to load access cookie settings; using defaults",
                    exc_info=True,
                )

            # Get refresh token from cookie to revoke the session
            refresh_token = request.cookies.get(cookie_settings["key"])
            if refresh_token:
                self._revoke_session(refresh_token, db)

        finally:
            db.close()

        # Clear ERP-local auth cookies.
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
        """Revoke the ERP-local session associated with the refresh token."""
        from datetime import datetime

        from sqlalchemy import select

        from app.models.auth import Session as AuthSession
        from app.models.auth import SessionStatus

        token_hash = hash_session_token(refresh_token)

        try:
            session = db.scalar(
                select(AuthSession).where(
                    AuthSession.token_hash == token_hash,
                    AuthSession.revoked_at.is_(None),
                )
            )
            if session:
                session.status = SessionStatus.revoked
                session.revoked_at = datetime.now(UTC)
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
                "app_version": settings.app_version,
            },
        )

    def reset_password_response(
        self,
        request: Request,
        token: str,
        auth: WebAuthContext,
        next_url: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        if auth.is_authenticated:
            return RedirectResponse(url="/dashboard", status_code=302)

        safe_next_url = _sanitize_redirect_url(
            next_url or "/dashboard",
            request,
            default="/dashboard",
        )
        login_href = f"/login?{urlencode({'next': safe_next_url})}"

        return templates.TemplateResponse(
            request,
            "reset_password.html",
            {
                "title": "Reset Password",
                "brand": brand_context(),
                "token": token,
                "login_href": login_href,
                "app_version": settings.app_version,
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
                "app_version": settings.app_version,
            },
        )


auth_web_service = AuthWebService()
