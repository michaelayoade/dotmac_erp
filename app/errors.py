import logging
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse

from app.services.common import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    ServiceError,
    ValidationError,
)
from app.templates import templates

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
    if request.url.path.startswith("/auth/") and not request.url.path.startswith(
        "/auth/me"
    ):
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
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        try:
            raw_body = await request.body()
            body_len = len(raw_body or b"")
        except Exception:
            body_len = -1
        logger.warning(
            "Request validation error: path=%s method=%s content_type=%s content_length=%s body_len=%s has_csrf_form=%s errors=%s",
            request.url.path,
            request.method,
            request.headers.get("content-type"),
            request.headers.get("content-length"),
            body_len,
            bool(getattr(request.state, "csrf_form", None)),
            exc.errors(),
        )
        if _is_html_request(request):
            errors = exc.errors()
            message = "Please check the form and try again."
            if errors:
                field = errors[0].get("loc", [])
                field_name = field[-1] if field else None
                if field_name == "subject":
                    message = "Subject is required."
                elif field_name == "project_name":
                    message = "Project name is required."
                elif field_name == "status":
                    message = "Status is required."
            path = request.url.path
            if request.method == "POST":
                if path.startswith("/people/hr/discipline/"):
                    parts = path.strip("/").split("/")
                    case_id = parts[3] if len(parts) > 3 else ""
                    if errors:
                        field = errors[0].get("loc", [])
                        field_name = field[-1] if field else None
                        if field_name == "query_text":
                            message = "Query text is required."
                        elif field_name == "response_due_date":
                            message = "Response due date is required."
                        elif field_name == "hearing_date":
                            message = "Hearing date is required."
                        elif field_name == "decision_summary":
                            message = "Decision summary is required."
                    if case_id:
                        return RedirectResponse(
                            url=f"/people/hr/discipline/{case_id}?error={quote(message)}",
                            status_code=303,
                        )
                if path == "/support/tickets":
                    return RedirectResponse(
                        url=f"/support/tickets/new?error={quote(message)}",
                        status_code=303,
                    )
                if path in {"/projects", "/projects/new"}:
                    return RedirectResponse(
                        url=f"/projects/new?error={quote(message)}",
                        status_code=303,
                    )
            return templates.TemplateResponse(
                request,
                "errors/400.html",
                {"message": message},
                status_code=400,
            )
        return JSONResponse(
            status_code=422,
            content=_error_payload(
                "validation_error", "Validation error", exc.errors()
            ),
        )

    # =========================================================================
    # Service Layer Error Handlers
    # =========================================================================
    # These handle custom exceptions from the service layer and convert them
    # to appropriate HTTP responses. Order matters - more specific first.

    @app.exception_handler(NotFoundError)
    async def not_found_error_handler(request: Request, exc: NotFoundError):
        """Handle resource not found errors (404)."""
        if _is_html_request(request):
            return templates.TemplateResponse(
                request,
                "errors/404.html",
                {"message": exc.message},
                status_code=404,
            )
        return JSONResponse(
            status_code=404,
            content=_error_payload("not_found", exc.message, None),
        )

    @app.exception_handler(ValidationError)
    async def service_validation_error_handler(request: Request, exc: ValidationError):
        """Handle service-level validation errors (400)."""
        if _is_html_request(request):
            return templates.TemplateResponse(
                request,
                "errors/400.html",
                {"message": exc.message},
                status_code=400,
            )
        return JSONResponse(
            status_code=400,
            content=_error_payload("validation_error", exc.message, None),
        )

    @app.exception_handler(ConflictError)
    async def conflict_error_handler(request: Request, exc: ConflictError):
        """Handle conflict errors - duplicate resources, invalid state transitions (409)."""
        if _is_html_request(request):
            return templates.TemplateResponse(
                request,
                "errors/409.html",
                {"message": exc.message},
                status_code=409,
            )
        return JSONResponse(
            status_code=409,
            content=_error_payload("conflict", exc.message, None),
        )

    @app.exception_handler(ForbiddenError)
    async def forbidden_error_handler(request: Request, exc: ForbiddenError):
        """Handle forbidden/permission errors (403)."""
        if _is_html_request(request):
            return RedirectResponse(url="/login?error=forbidden", status_code=302)
        return JSONResponse(
            status_code=403,
            content=_error_payload("forbidden", exc.message, None),
        )

    @app.exception_handler(RateLimitError)
    async def rate_limit_error_handler(request: Request, exc: RateLimitError):
        """Handle rate limit errors (429).

        Includes Retry-After header per HTTP spec.
        """
        if _is_html_request(request):
            return templates.TemplateResponse(
                request,
                "errors/429.html",
                {"message": exc.message, "retry_after": exc.retry_after},
                status_code=429,
            )
        return JSONResponse(
            status_code=429,
            content=_error_payload(
                "rate_limit_exceeded",
                exc.message,
                {"retry_after": exc.retry_after},
            ),
            headers={"Retry-After": str(exc.retry_after)},
        )

    @app.exception_handler(AuthenticationError)
    async def authentication_error_handler(request: Request, exc: AuthenticationError):
        """Handle authentication errors (401)."""
        if _is_html_request(request):
            next_url = str(request.url.path)
            if request.url.query:
                next_url += f"?{request.url.query}"
            login_url = f"/login?next={quote(next_url, safe='')}"
            return RedirectResponse(url=login_url, status_code=302)
        return JSONResponse(
            status_code=401,
            content=_error_payload("authentication_error", exc.message, None),
        )

    @app.exception_handler(AuthorizationError)
    async def authorization_error_handler(request: Request, exc: AuthorizationError):
        """Handle authorization errors (403)."""
        if _is_html_request(request):
            return RedirectResponse(url="/login?error=forbidden", status_code=302)
        return JSONResponse(
            status_code=403,
            content=_error_payload("authorization_error", exc.message, None),
        )

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError):
        """Handle generic service errors (500).

        This catches any ServiceError subclass not handled above.
        """
        logger.warning(
            "Service error on %s %s: %s",
            request.method,
            request.url.path,
            exc.message,
        )
        if _is_html_request(request):
            return templates.TemplateResponse(
                request,
                "errors/500.html",
                {"message": exc.message},
                status_code=500,
            )
        return JSONResponse(
            status_code=500,
            content=_error_payload("service_error", exc.message, None),
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
        if _is_html_request(request):
            return templates.TemplateResponse(
                request,
                "errors/500.html",
                {"message": "An unexpected error occurred. Please try again later."},
                status_code=500,
            )
        return JSONResponse(
            status_code=500,
            content=_error_payload("internal_error", "Internal server error", None),
        )
