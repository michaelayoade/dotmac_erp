import logging
from html import escape
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

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


def _error_payload(code: str, message: str, details: object) -> dict[str, object]:
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


def _friendly_bad_request_message(detail: object) -> str:
    """Return a user-friendly message for 400 errors."""
    fallback = "Some required information is missing or invalid. Please check the form and try again."

    if isinstance(detail, str):
        message = detail.strip()
        if not message:
            return fallback
        lowered = message.lower()
        # Hide technical payload-like errors from end users
        if any(
            token in lowered
            for token in (
                "validation error",
                "type_error",
                "value_error",
                "traceback",
                "{",
                "[",
            )
        ):
            return fallback
        return message

    if isinstance(detail, dict):
        # Prefer explicit user message if present
        for key in ("message", "detail", "error"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                candidate = value.strip()
                lowered = candidate.lower()
                if any(
                    token in lowered
                    for token in ("type_error", "value_error", "traceback")
                ):
                    return fallback
                return candidate
        return fallback

    # Lists are commonly validation payloads; keep message plain for non-technical users
    if isinstance(detail, list):
        return fallback

    return fallback


def register_error_handlers(app) -> None:
    def _error_template_response(
        request: Request,
        template_name: str,
        context: dict[str, object],
        *,
        status_code: int,
        fallback_title: str,
    ) -> Response:
        try:
            template_context = dict(context)
            template_context.setdefault("request", request)
            rendered_html = templates.env.get_template(template_name).render(
                template_context
            )
            return HTMLResponse(
                status_code=status_code,
                content=rendered_html,
            )
        except Exception:
            logger.exception("Failed rendering error template %s", template_name)
            message = context.get("message")
            safe_message = (
                escape(str(message))
                if message is not None
                else "An unexpected error occurred."
            )
            safe_title = escape(fallback_title)
            return HTMLResponse(
                status_code=status_code,
                content=(
                    "<!doctype html><html><head>"
                    f"<title>{safe_title}</title>"
                    "</head><body>"
                    f"<h1>{safe_title}</h1>"
                    f"<p>{safe_message}</p>"
                    "</body></html>"
                ),
            )

    async def _handle_http_exception(
        request: Request, status_code: int, detail: object
    ) -> Response:
        # For 401 errors on web routes, redirect to login
        if status_code == 401 and _is_html_request(request):
            # Build the next URL from the current request path
            next_url = str(request.url.path)
            if request.url.query:
                next_url += f"?{request.url.query}"
            login_url = f"/login?next={quote(next_url, safe='')}"
            return RedirectResponse(url=login_url, status_code=302)

        # For 403 errors on web routes, render a user-friendly forbidden page
        if status_code == 403 and _is_html_request(request):
            return _error_template_response(
                request,
                "errors/403.html",
                {},
                status_code=403,
                fallback_title="Access Denied",
            )

        # For 404 errors on web routes, render the HTML 404 page
        if status_code == 404 and _is_html_request(request):
            return _error_template_response(
                request,
                "errors/404.html",
                {},
                status_code=404,
                fallback_title="Page Not Found",
            )

        # For 400 errors on web routes, render a user-friendly bad request page
        if status_code == 400 and _is_html_request(request):
            return _error_template_response(
                request,
                "errors/400.html",
                {},
                status_code=400,
                fallback_title="We Need a Few Details",
            )

        # For other errors or API requests, return JSON
        code = f"http_{status_code}"
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
            status_code=status_code,
            content=_error_payload(code, message, details),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return await _handle_http_exception(request, exc.status_code, exc.detail)

    @app.exception_handler(StarletteHTTPException)
    async def starlette_http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ):
        return await _handle_http_exception(request, exc.status_code, exc.detail)

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
            return _error_template_response(
                request,
                "errors/400.html",
                {},
                status_code=400,
                fallback_title="We Need a Few Details",
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
        logger.warning(
            "Not found on %s %s: %s",
            request.method,
            request.url.path,
            exc.message,
        )
        if _is_html_request(request):
            return templates.TemplateResponse(
                request,
                "errors/404.html",
                {},
                status_code=404,
            )
        return JSONResponse(
            status_code=404,
            content=_error_payload("not_found", exc.message, None),
        )

    @app.exception_handler(ValidationError)
    async def service_validation_error_handler(request: Request, exc: ValidationError):
        """Handle service-level validation errors (400)."""
        logger.warning(
            "Validation error on %s %s: %s",
            request.method,
            request.url.path,
            exc.message,
        )
        if _is_html_request(request):
            return templates.TemplateResponse(
                request,
                "errors/400.html",
                {},
                status_code=400,
            )
        return JSONResponse(
            status_code=400,
            content=_error_payload("validation_error", exc.message, None),
        )

    @app.exception_handler(ConflictError)
    async def conflict_error_handler(request: Request, exc: ConflictError):
        """Handle conflict errors - duplicate resources, invalid state transitions (409)."""
        logger.warning(
            "Conflict on %s %s: %s",
            request.method,
            request.url.path,
            exc.message,
        )
        if _is_html_request(request):
            return templates.TemplateResponse(
                request,
                "errors/409.html",
                {},
                status_code=409,
            )
        return JSONResponse(
            status_code=409,
            content=_error_payload("conflict", exc.message, None),
        )

    @app.exception_handler(ForbiddenError)
    async def forbidden_error_handler(request: Request, exc: ForbiddenError):
        """Handle forbidden/permission errors (403)."""
        logger.warning(
            "Forbidden on %s %s: %s",
            request.method,
            request.url.path,
            exc.message,
        )
        if _is_html_request(request):
            return templates.TemplateResponse(
                request,
                "errors/403.html",
                {},
                status_code=403,
            )
        return JSONResponse(
            status_code=403,
            content=_error_payload("forbidden", exc.message, None),
        )

    @app.exception_handler(RateLimitError)
    async def rate_limit_error_handler(request: Request, exc: RateLimitError):
        """Handle rate limit errors (429).

        Includes Retry-After header per HTTP spec.
        """
        logger.warning(
            "Rate limit on %s %s: %s retry_after=%s",
            request.method,
            request.url.path,
            exc.message,
            exc.retry_after,
        )
        if _is_html_request(request):
            return templates.TemplateResponse(
                request,
                "errors/429.html",
                {},
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
        logger.warning(
            "Authentication error on %s %s: %s",
            request.method,
            request.url.path,
            exc.message,
        )
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
        logger.warning(
            "Authorization error on %s %s: %s",
            request.method,
            request.url.path,
            exc.message,
        )
        if _is_html_request(request):
            return templates.TemplateResponse(
                request,
                "errors/403.html",
                {},
                status_code=403,
            )
        return JSONResponse(
            status_code=403,
            content=_error_payload("authorization_error", exc.message, None),
        )

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError):
        """Handle generic service errors (500).

        This catches any ServiceError subclass not handled above.
        """
        logger.error(
            "Service error on %s %s: %s",
            request.method,
            request.url.path,
            exc.message,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        if _is_html_request(request):
            return templates.TemplateResponse(
                request,
                "errors/500.html",
                {},
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
                {},
                status_code=500,
            )
        return JSONResponse(
            status_code=500,
            content=_error_payload("internal_error", "Internal server error", None),
        )
