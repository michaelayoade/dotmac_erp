import logging
import os
import re
import time
import uuid
from contextvars import ContextVar

from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.net import get_client_ip, is_from_trusted_proxy

from app.metrics import REQUEST_COUNT, REQUEST_ERRORS, REQUEST_LATENCY

logger = logging.getLogger(__name__)

# Paths excluded from INFO-level request logging (health probes, static assets,
# metrics scrapes).  These generate high volume with near-zero diagnostic value.
_QUIET_PATH_PREFIXES = ("/health", "/static", "/metrics", "/favicon")

# Context variables for request tracking - accessible anywhere in the request lifecycle
#: What an unauthenticated request WRITES, rather than what an unset variable
#: happens to read as. A submitted username or header never becomes an actor —
#: anonymous stays anonymous, and it says so.
ANONYMOUS_ACTOR = "anonymous"

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
actor_id_var: ContextVar[str] = ContextVar("actor_id", default="")
ip_address_var: ContextVar[str] = ContextVar("ip_address", default="")
user_agent_var: ContextVar[str] = ContextVar("user_agent", default="")


def get_request_id() -> str:
    """Get the current request ID from context.

    Use this in services/models to include correlation ID in logs.
    """
    return request_id_var.get()


def get_actor_id() -> str:
    """Get the current actor ID from context."""
    return actor_id_var.get()


def _extract_bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if not auth:
        return None
    parts = auth.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _extract_access_token_cookie(request: Request) -> str | None:
    token = request.cookies.get("access_token")
    if token:
        return token.strip() or None
    return None


def _jwt_secret() -> str | None:
    secret = os.getenv("JWT_SECRET")
    if secret:
        return secret
    return None


def _jwt_algorithm() -> str:
    return os.getenv("JWT_ALGORITHM", "HS256")


def _extract_actor_id_from_jwt(token: str | None) -> str | None:
    if not token:
        return None
    secret = _jwt_secret()
    if not secret:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=[_jwt_algorithm()])
    except JWTError:
        return None
    subject = payload.get("sub")
    if subject:
        return str(subject)
    return None


# Matches UUID-like and numeric-only path segments that would create
# high-cardinality Prometheus labels if used as-is.
_ID_SEGMENT_RE = re.compile(
    r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|/\d{4,}",
    re.IGNORECASE,
)


def _request_path(request: Request) -> str:
    """Return the route template path, or a normalized fallback.

    Starlette's ``request.scope["route"].path`` gives us the template
    (e.g. ``/finance/ar/invoices/{invoice_id}``).  When no route matched
    (404, middleware-intercepted), we fall back to the raw URL path but
    replace UUID/numeric segments with ``/{id}`` to prevent Prometheus
    cardinality explosion.
    """
    route = request.scope.get("route")
    if route and hasattr(route, "path"):
        return str(route.path)
    # Normalize UUIDs and long numeric IDs to a fixed placeholder
    return _ID_SEGMENT_RE.sub("/{id}", request.url.path)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Create the per-request observability context, once, and always clear it.

    ## The defect this shape exists to prevent

    Until 2026-09-04 this middleware called `actor_id_var.set()` only
    ``if actor_id:`` and never reset any variable it set. An ANONYMOUS request
    therefore did not clear the previous request's actor — it declined to write,
    and the previous value was what a reader saw. Every audit event, log line
    and field-tracker attribution taken during that request could be attributed
    to whoever happened to be authenticated before it.

    Two rules follow, and neither is a style preference:

    **Every variable is set on every request, including explicit anonymous
    values.** An anonymous request SETS anonymous. Declining to set is what lets
    a stale actor persist, and "unset" and "anonymous" are different facts that
    a default of `""` cannot tell apart at the read site — so anonymity is
    written down rather than inferred from silence.

    **Every `set()` token is retained and reset in `finally`.** `ContextVar.reset`
    requires the token from its OWN `set()`; a bare reset-to-default would
    discard whatever an outer scope had established. `finally` is what makes it
    survive the exception path, which is exactly where inheritance is most
    likely because the failing request is the one that leaves state behind.

    ## The client address comes from the trusted-origin resolver

    `app.net.get_client_ip` honours `X-Forwarded-For` only when the PEER is
    inside a configured trusted-proxy network, and trusts nothing when none is
    configured. This middleware previously read `request.client.host` directly,
    so ERP had a correct resolver and its own request-context creator bypassed
    it — the address behind a proxy was the proxy's.

    ## An inbound request ID is believed only from a trusted peer

    `x-request-id` was accepted from anyone, so any caller could choose the
    correlation identity its own request would be logged under and collide it
    with somebody else's deliberately. It is now honoured only from a trusted
    proxy; otherwise a fresh one is generated.
    """

    async def dispatch(self, request: Request, call_next):
        # Believed only from a trusted peer. An untrusted caller does not choose
        # the identity its request is correlated under.
        inbound = (
            request.headers.get("x-request-id")
            if is_from_trusted_proxy(request)
            else None
        )
        request_id = inbound or str(uuid.uuid4())
        request.state.request_id = request_id

        token = _extract_bearer_token(request) or _extract_access_token_cookie(request)
        # EXPLICIT anonymity. `ANONYMOUS_ACTOR` rather than `""`, so a reader can
        # tell "nobody was authenticated" from "nobody has set this yet".
        actor_id = (
            getattr(request.state, "actor_id", None)
            or _extract_actor_id_from_jwt(token)
            or ANONYMOUS_ACTOR
        )

        # Every variable, every request, tokens retained for `finally`.
        tokens = (
            request_id_var.set(request_id),
            actor_id_var.set(actor_id),
            ip_address_var.set(get_client_ip(request)),
            user_agent_var.set(request.headers.get("user-agent", "")),
        )
        variables = (request_id_var, actor_id_var, ip_address_var, user_agent_var)
        try:
            return await self._dispatch(request, call_next, request_id, actor_id)
        finally:
            for variable, reset_token in zip(variables, tokens, strict=True):
                variable.reset(reset_token)

    async def _dispatch(self, request: Request, call_next, request_id, actor_id):

        # Set change source for field-level tracking.
        # Lazy import: field_tracker imports from app.observability (actor_id_var,
        # request_id_var), so a module-level import here would create a circular
        # dependency.
        from app.services.audit.field_tracker import set_change_source

        path = request.url.path
        if path.startswith("/api/"):
            set_change_source("api")
        elif path.startswith(
            (
                "/finance/",
                "/people/",
                "/expense/",
                "/inventory/",
                "/procurement/",
                "/public-sector/",
                "/automation/",
            )
        ):
            set_change_source("web_form")
        else:
            set_change_source("")

        start = time.monotonic()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            duration_ms = (time.monotonic() - start) * 1000.0
            path = _request_path(request)
            REQUEST_COUNT.labels(request.method, path, str(status_code)).inc()
            REQUEST_LATENCY.labels(request.method, path, str(status_code)).observe(
                duration_ms / 1000.0
            )
            REQUEST_ERRORS.labels(request.method, path, str(status_code)).inc()
            logger.exception(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "actor_id": actor_id,
                    "path": path,
                    "method": request.method,
                    "status": status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise
        duration_ms = (time.monotonic() - start) * 1000.0
        path = _request_path(request)
        REQUEST_COUNT.labels(request.method, path, str(status_code)).inc()
        REQUEST_LATENCY.labels(request.method, path, str(status_code)).observe(
            duration_ms / 1000.0
        )
        if status_code >= 500:
            REQUEST_ERRORS.labels(request.method, path, str(status_code)).inc()
        # Skip INFO logging for high-frequency, low-value paths (health probes,
        # static assets, metrics).  Errors on these paths are still logged.
        if status_code >= 400 or not path.startswith(_QUIET_PATH_PREFIXES):
            logger.info(
                "request_completed",
                extra={
                    "request_id": request_id,
                    "actor_id": actor_id,
                    "path": path,
                    "method": request.method,
                    "status": status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )
        response.headers["x-request-id"] = request_id
        return response
