from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.responses import Response

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"
CSRF_FORM_FIELD = "csrf_token"

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _default_port(scheme: str | None) -> int | None:
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def _request_host_parts(request: Request) -> tuple[str | None, int | None]:
    host = request.url.hostname
    scheme = request.url.scheme
    if not host:
        return None, None
    return host, request.url.port or _default_port(scheme)


def _origin_parts(value: str) -> tuple[str | None, int | None]:
    parsed = urlsplit(value)
    if not parsed.hostname:
        return None, None
    return parsed.hostname, parsed.port or _default_port(parsed.scheme)


def _origin_matches_request(request: Request, origin_value: str) -> bool:
    origin_host, origin_port = _origin_parts(origin_value)
    if not origin_host:
        return False
    request_host, request_port = _request_host_parts(request)
    if not request_host:
        return False
    return origin_host.lower() == request_host.lower() and origin_port == request_port


def _is_secure_request(request: Request) -> bool:
    return request.url.scheme == "https"


def _should_enforce_csrf(request: Request) -> bool:
    """Determine if CSRF protection should be enforced for this request.

    CSRF protection is required when:
    1. The request uses a non-safe HTTP method (POST, PUT, DELETE, PATCH)
    2. The request uses cookie-based authentication

    CSRF protection is NOT required when:
    1. The request uses safe HTTP methods (GET, HEAD, OPTIONS, TRACE)
    2. The request uses pure Bearer token authentication (inherently CSRF-safe)

    The key insight is that CSRF attacks exploit the browser's automatic cookie
    sending behavior. Bearer tokens must be explicitly added via JavaScript,
    which cross-origin scripts cannot do due to same-origin policy.
    """
    if request.method in _SAFE_METHODS:
        return False

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
    if content_type.startswith("application/x-www-form-urlencoded") or content_type.startswith(
        "multipart/form-data"
    ):
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()
            request.state.csrf_form = form
        token = form.get(CSRF_FORM_FIELD)
        if token:
            return str(token)
    return None


async def csrf_middleware(request: Request, call_next) -> Response:
    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME) or ""
    request.state.csrf_token = csrf_cookie

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

    if not csrf_cookie:
        raise HTTPException(status_code=400, detail="Missing CSRF token")

    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin or origin == "null" or not _origin_matches_request(request, origin):
        raise HTTPException(status_code=400, detail="Invalid CSRF origin")

    request_token = await _extract_csrf_token(request)
    if not request_token or request_token != csrf_cookie:
        raise HTTPException(status_code=400, detail="Invalid CSRF token")

    return await call_next(request)
