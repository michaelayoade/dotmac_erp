from __future__ import annotations

import os
import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
import logging
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"
CSRF_FORM_FIELD = "csrf_token"

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

logger = logging.getLogger(__name__)


def _default_port(scheme: str | None) -> int | None:
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def _parse_host_port(value: str, scheme: str | None) -> tuple[str | None, int | None]:
    if not value:
        return None, None
    parsed = urlsplit(f"{scheme or 'http'}://{value}")
    if not parsed.hostname:
        return None, None
    return parsed.hostname, parsed.port or _default_port(parsed.scheme)


def _forwarded_host_parts(request: Request) -> tuple[str | None, int | None]:
    forwarded_host = request.headers.get("x-forwarded-host")
    if not forwarded_host:
        return None, None
    forwarded_host = forwarded_host.split(",")[0].strip()
    forwarded_proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return _parse_host_port(forwarded_host, forwarded_proto)


def _request_host_candidates(request: Request) -> list[tuple[str | None, int | None]]:
    host = request.url.hostname
    scheme = request.url.scheme
    request_parts = (
        (host, request.url.port or _default_port(scheme)) if host else (None, None)
    )
    forwarded_parts = _forwarded_host_parts(request)
    app_url = os.getenv("APP_URL", "").strip()
    app_parts = _origin_parts(app_url) if app_url else (None, None)
    return [request_parts, forwarded_parts, app_parts]


def _origin_parts(value: str) -> tuple[str | None, int | None]:
    parsed = urlsplit(value)
    if not parsed.hostname:
        return None, None
    return parsed.hostname, parsed.port or _default_port(parsed.scheme)


def _origin_matches_request(request: Request, origin_value: str) -> bool:
    origin_host, origin_port = _origin_parts(origin_value)
    if not origin_host:
        return False
    for request_host, request_port in _request_host_candidates(request):
        if not request_host:
            continue
        if origin_host.lower() != request_host.lower():
            continue
        if origin_port == request_port:
            return True
        if origin_port is None or request_port is None:
            return True
        if origin_port in (80, 443) and request_port in (80, 443):
            return True
    return False


def _is_secure_request(request: Request) -> bool:
    return request.url.scheme == "https"


def _should_enforce_csrf(request: Request) -> bool:
    """Determine if CSRF protection should be enforced for this request.

    CSRF protection is required when:
    1. The request uses a non-safe HTTP method (POST, PUT, DELETE, PATCH)
    2. The request uses cookie-based authentication
    3. The request is to a public portal that uses URL-based token auth

    CSRF protection is NOT required when:
    1. The request uses safe HTTP methods (GET, HEAD, OPTIONS, TRACE)
    2. The request uses pure Bearer token authentication (inherently CSRF-safe)

    The key insight is that CSRF attacks exploit the browser's automatic cookie
    sending behavior. Bearer tokens must be explicitly added via JavaScript,
    which cross-origin scripts cannot do due to same-origin policy.

    For public portals (onboarding, careers), we enforce CSRF even without
    auth cookies because the URL token acts as authentication.
    """
    if request.method in _SAFE_METHODS:
        return False

    # JSON-only auth endpoints are inherently CSRF-safe:
    # - CORS preflight blocks cross-origin JSON POSTs
    # - Refresh tokens are rotated on each use (replay protection)
    # - The CSRF cookie is httponly, so JS can't read it for the
    #   double-submit pattern on fetch() calls
    csrf_exempt_paths = ["/auth/refresh", "/api/v1/auth/refresh"]
    path = request.url.path
    if path in csrf_exempt_paths:
        return False

    # Public portals that use URL-based token authentication need CSRF protection
    # even without auth cookies, because the token in the URL acts as auth
    portal_paths = [
        "/onboarding/",
        "/careers/",
    ]
    is_portal_request = any(path.startswith(p) for p in portal_paths)
    if is_portal_request:
        return True

    # Check if this is a pure Bearer token request (no cookies involved)
    # Bearer token requests are inherently CSRF-safe
    auth_header = request.headers.get("authorization", "")
    has_bearer_token = auth_header.lower().startswith("bearer ")

    # Check for any authentication-related cookies
    # These cookies indicate cookie-based auth which needs CSRF protection
    has_auth_cookies = bool(
        request.cookies.get("access_token") or request.cookies.get("refresh_token")
    )

    # If using pure Bearer auth with no auth cookies, skip CSRF
    # (API clients using Bearer tokens don't need CSRF protection)
    if has_bearer_token and not has_auth_cookies:
        return False

    # Enforce CSRF for any request with authentication cookies
    return has_auth_cookies


async def _extract_csrf_token(request: Request) -> str | None:
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if header_token:
        return header_token.strip()
    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith(
        "application/x-www-form-urlencoded"
    ) or content_type.startswith("multipart/form-data"):
        # Read and cache the raw body so downstream form parsing still works.
        try:
            if getattr(request, "_body", None) is None:
                await request.body()
        except Exception:
            pass
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()
            request.state.csrf_form = form
        token = form.get(CSRF_FORM_FIELD)
        if token:
            return str(token)
    return None


async def csrf_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME) or ""
    request.state.csrf_token = csrf_cookie
    set_csrf_cookie = False

    if request.method in _SAFE_METHODS:
        if not csrf_cookie:
            csrf_cookie = secrets.token_urlsafe(32)
            request.state.csrf_token = csrf_cookie
        response = await call_next(request)
        if not request.cookies.get(CSRF_COOKIE_NAME):
            response.set_cookie(
                CSRF_COOKIE_NAME,
                csrf_cookie,
                httponly=True,
                secure=_is_secure_request(request),
                samesite="Lax",
                path="/",
            )
        return response

    if not _should_enforce_csrf(request):
        return await call_next(request)

    request_token = None
    if not csrf_cookie:
        request_token = await _extract_csrf_token(request)
        if not request_token:
            raise HTTPException(status_code=400, detail="Missing CSRF token")
        csrf_cookie = request_token
        request.state.csrf_token = csrf_cookie
        set_csrf_cookie = True

    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin and (origin == "null" or not _origin_matches_request(request, origin)):
        logger.warning(
            "CSRF origin mismatch: path=%s origin=%s host=%s",
            request.url.path,
            origin,
            request.headers.get("host"),
        )
        raise HTTPException(status_code=400, detail="Invalid CSRF origin")

    if request_token is None:
        request_token = await _extract_csrf_token(request)
    if not request_token:
        logger.warning(
            "CSRF token missing: path=%s has_cookie=%s",
            request.url.path,
            bool(csrf_cookie),
        )
        raise HTTPException(status_code=400, detail="Missing CSRF token")
    if request_token != csrf_cookie:
        logger.warning(
            "CSRF token mismatch: path=%s",
            request.url.path,
        )
        raise HTTPException(status_code=400, detail="Invalid CSRF token")

    response = await call_next(request)
    if set_csrf_cookie:
        response.set_cookie(
            CSRF_COOKIE_NAME,
            csrf_cookie,
            httponly=True,
            secure=_is_secure_request(request),
            samesite="Lax",
            path="/",
        )
    return response
