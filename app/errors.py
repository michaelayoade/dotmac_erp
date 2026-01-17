import logging
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse

logger = logging.getLogger(__name__)


def _error_payload(code: str, message: str, details):
    return {
        "code": code,
        "message": message,
        "details": jsonable_encoder(details),
    }


def _is_html_request(request: Request) -> bool:
    """Check if the request expects an HTML response."""
    accept = request.headers.get("accept", "")
    content_type = request.headers.get("content-type", "")

    # Check if it's an API request (JSON content type or API path)
    if "application/json" in content_type:
        return False
    if request.url.path.startswith("/api/"):
        return False
    if request.url.path.startswith("/auth/") and not request.url.path.startswith("/auth/me"):
        # Auth API endpoints (login, logout, etc.) should return JSON
        # But /auth/me is a web page
        return False

    # Check Accept header for HTML preference
    if "text/html" in accept:
        return True

    # Default to HTML for web routes (non-API paths)
    return not request.url.path.startswith("/api/")


def register_error_handlers(app) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        # For 401 errors on web routes, redirect to login
        if exc.status_code == 401 and _is_html_request(request):
            # Build the next URL from the current request path
            next_url = str(request.url.path)
            if request.url.query:
                next_url += f"?{request.url.query}"
            login_url = f"/login?next={quote(next_url, safe='')}"
            return RedirectResponse(url=login_url, status_code=302)

        # For 403 errors on web routes, show a forbidden page or redirect
        if exc.status_code == 403 and _is_html_request(request):
            return RedirectResponse(url="/login?error=forbidden", status_code=302)

        # For other errors or API requests, return JSON
        detail = exc.detail
        code = f"http_{exc.status_code}"
        message = "Request failed"
        details = None
        if isinstance(detail, dict):
            code = detail.get("code", code)
            message = detail.get("message", message)
            details = detail.get("details")
        elif isinstance(detail, str):
            message = detail
        else:
            details = detail
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(code, message, details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_error_payload(
                "validation_error", "Validation error", exc.errors()
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # Log the full exception with stack trace for debugging
        logger.exception(
            "Unhandled exception on %s %s: %s",
            request.method,
            request.url.path,
            str(exc),
        )
        return JSONResponse(
            status_code=500,
            content=_error_payload(
                "internal_error", "Internal server error", None
            ),
        )
