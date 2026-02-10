import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from time import monotonic
from unittest.mock import Mock

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse, RedirectResponse, Response

# Ensure all models are registered with SQLAlchemy metadata at startup.
import app.models as app_models  # noqa: F401
from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.auth_flow import router as auth_flow_router
from app.api.careers import router as careers_api_router
from app.api.crm import router as crm_router
from app.api.crm import webhook_router as crm_webhook_router
from app.api.deps import require_role, require_tenant_auth
from app.api.expense import router as expense_router
from app.api.expense_limits import router as expense_limits_router
from app.api.finance import (
    ap_router,
    ar_router,
    banking_router,
    cons_router,
    gl_router,
    import_export_router,
    ipsas_router,
    lease_router,
    opening_balance_router,
    payments_router,
    payments_webhook_router,
    rpt_router,
    search_router,
    tax_router,
)
from app.api.fixed_assets import router as fa_api_router
from app.api.fleet import router as fleet_router
from app.api.inventory import router as inv_api_router
from app.api.me import router as me_router
from app.api.people import router as people_hr_router
from app.api.persons import router as people_router
from app.api.pm import router as pm_router
from app.api.procurement import router as procurement_router
from app.api.rbac import router as rbac_router
from app.api.scheduler import router as scheduler_router
from app.api.settings import router as settings_router
from app.api.support import router as support_router
from app.api.sync.dotmac_crm import router as crm_sync_router
from app.api.workflow_tasks import router as workflow_tasks_router
from app.db import SessionLocal
from app.errors import register_error_handlers
from app.logging import configure_logging
from app.middleware.csp import add_unsafe_eval_to_csp
from app.middleware.rate_limit import rate_limit_middleware
from app.models.domain_settings import DomainSetting, SettingDomain
from app.observability import ObservabilityMiddleware
from app.services import audit as audit_service
from app.services.settings_seed import seed_all_settings
from app.startup import log_startup_info, validate_startup
from app.telemetry import setup_otel
from app.web.admin import router as admin_web_router
from app.web.admin_crm_sync import router as admin_crm_sync_router
from app.web.admin_sync import router as admin_sync_router
from app.web.auth import router as auth_web_router
from app.web.careers import router as careers_web_router
from app.web.csrf import csrf_middleware
from app.web.finance import automation_router as automation_web_router
from app.web.finance import expense_router as expense_web_router
from app.web.finance import router as finance_web_router
from app.web.finance import settings_router as finance_settings_web_router
from app.web.fixed_assets import router as fixed_assets_web_router
from app.web.fleet import router as fleet_web_router
from app.web.inventory import router as inventory_web_router
from app.web.notifications import router as notifications_web_router
from app.web.onboarding_portal import router as onboarding_portal_router
from app.web.payroll_alias import router as payroll_alias_web_router
from app.web.people import router as people_web_router
from app.web.procurement import router as procurement_web_router
from app.web.profile import router as profile_web_router
from app.web.projects import router as projects_web_router
from app.web.settings import router as module_settings_web_router
from app.web.support import router as support_web_router
from app.web.workflow_tasks import router as workflow_tasks_web_router
from app.web_home import router as web_home_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Log startup info for debugging
    log_startup_info()

    db = SessionLocal()
    try:
        # Validate required configuration before accepting requests
        # This will exit(1) if critical config is missing
        validate_startup(db, exit_on_failure=True)

        # Seed default settings for all domains
        seed_all_settings(db)
    finally:
        db.close()
    yield


app = FastAPI(title="DotMac ERP API", lifespan=lifespan)


# Redirect legacy AR customers path to web UI (avoid JSON API response)
@app.get("/ar/customers", include_in_schema=False)
@app.get("/ar/customers/", include_in_schema=False)
def ar_customers_redirect(request: Request):
    query = request.url.query
    url = "/finance/ar/customers"
    if query:
        url = f"{url}?{query}"
    return RedirectResponse(url=url, status_code=302)


_AUDIT_SETTINGS_CACHE: dict | None = None
_AUDIT_SETTINGS_CACHE_AT: float | None = None
_AUDIT_SETTINGS_CACHE_TTL_SECONDS = 30.0
_AUDIT_SETTINGS_LOCK = Lock()
configure_logging()
setup_otel(app)
app.add_middleware(ObservabilityMiddleware)
register_error_handlers(app)
# Rate limiting must come before CSRF to reject early
app.middleware("http")(rate_limit_middleware)
app.middleware("http")(csrf_middleware)


@app.middleware("http")
async def csp_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = add_unsafe_eval_to_csp(
        response.headers.get("Content-Security-Policy")
    )
    return response


# Sensitive parameter names to redact from audit logs
_SENSITIVE_PARAMS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "api-key",
        "auth",
        "authorization",
        "credential",
        "credentials",
        "access_token",
        "refresh_token",
        "private_key",
        "privatekey",
    }
)


def _sanitize_query_params(params: dict) -> dict:
    """Redact sensitive query parameters from audit logs."""
    return {
        k: "***REDACTED***" if k.lower() in _SENSITIVE_PARAMS else v
        for k, v in params.items()
    }


def _extract_audit_data(request: Request, response: Response) -> dict:
    """Extract audit data from request/response for async logging."""
    from app.models.audit import AuditActorType

    actor_type = request.headers.get("x-actor-type", AuditActorType.system.value)
    actor_id = request.headers.get("x-actor-id")
    request_id = request.headers.get("x-request-id")
    entity_id = request.headers.get("x-entity-id")
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    try:
        query_params = _sanitize_query_params(dict(request.query_params))
    except (KeyError, Exception):
        query_params = {}

    return {
        "actor_type": actor_type,
        "actor_id": actor_id,
        "action": request.method,
        "entity_type": request.url.path,
        "entity_id": entity_id,
        "status_code": response.status_code,
        "is_success": response.status_code < 400,
        "ip_address": ip_address,
        "user_agent": user_agent,
        "request_id": request_id,
        "metadata_": {
            "path": request.url.path,
            "query": query_params,
        },
    }


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    from app.tasks.audit import log_audit_event

    response: Response
    path = request.url.path
    db = SessionLocal()
    try:
        audit_settings = _load_audit_settings(db)
    except Exception as exc:
        # Fail open for audit to preserve availability when DB is down.
        # Use conservative defaults (disabled).
        logger = logging.getLogger(__name__)
        logger.warning("Audit settings unavailable, skipping audit: %s", exc)
        return await call_next(request)
    finally:
        db.close()
    if not audit_settings["enabled"]:
        return await call_next(request)
    header_key = audit_settings.get("read_trigger_header") or ""
    header_value = request.headers.get(header_key, "") if header_key else ""
    track_read = request.method == "GET" and (
        (header_value or "").lower() == "true"
        or request.query_params.get(audit_settings["read_trigger_query"]) == "true"
    )
    should_log = request.method in audit_settings["methods"] or track_read
    if _is_audit_path_skipped(path, audit_settings["skip_paths"]):
        should_log = False
    try:
        response = await call_next(request)
    except Exception:
        if should_log:
            # Log error response asynchronously (or synchronously in tests)
            audit_response = Response(status_code=500)
            if os.getenv("PYTEST_CURRENT_TEST"):
                with SessionLocal() as log_db:
                    audit_service.audit_events.log_request(
                        log_db, request, audit_response
                    )
            else:
                audit_data = _extract_audit_data(request, audit_response)
                log_audit_event.delay(**audit_data)
        raise
    if should_log:
        # Log response asynchronously via Celery (or synchronously in tests)
        if os.getenv("PYTEST_CURRENT_TEST"):
            with SessionLocal() as log_db:
                audit_service.audit_events.log_request(log_db, request, response)
        else:
            audit_data = _extract_audit_data(request, response)
            log_audit_event.delay(**audit_data)
    return response


def _load_audit_settings(db: Session):
    global _AUDIT_SETTINGS_CACHE, _AUDIT_SETTINGS_CACHE_AT
    now = monotonic()
    with _AUDIT_SETTINGS_LOCK:
        if (
            _AUDIT_SETTINGS_CACHE
            and _AUDIT_SETTINGS_CACHE_AT
            and now - _AUDIT_SETTINGS_CACHE_AT < _AUDIT_SETTINGS_CACHE_TTL_SECONDS
        ):
            return _AUDIT_SETTINGS_CACHE
    defaults = {
        "enabled": True,
        "methods": {"POST", "PUT", "PATCH", "DELETE"},
        "skip_paths": ["/static", "/web", "/health"],
        "read_trigger_header": "x-audit-read",
        "read_trigger_query": "audit",
    }
    rows = (
        db.query(DomainSetting)
        .filter(DomainSetting.domain == SettingDomain.audit)
        .filter(DomainSetting.is_active.is_(True))
        .all()
    )
    if isinstance(rows, Mock):
        rows = []
    values = {row.key: row for row in rows}
    if "enabled" in values:
        defaults["enabled"] = _to_bool(values["enabled"])
    if "methods" in values:
        defaults["methods"] = _to_list(values["methods"], upper=True)
    if "skip_paths" in values:
        defaults["skip_paths"] = _to_list(values["skip_paths"], upper=False)
    if "read_trigger_header" in values:
        defaults["read_trigger_header"] = _to_str(values["read_trigger_header"])
    if "read_trigger_query" in values:
        defaults["read_trigger_query"] = _to_str(values["read_trigger_query"])
    with _AUDIT_SETTINGS_LOCK:
        _AUDIT_SETTINGS_CACHE = defaults
        _AUDIT_SETTINGS_CACHE_AT = now
    return defaults


def _to_bool(setting: DomainSetting) -> bool:
    value = setting.value_json if setting.value_json is not None else setting.value_text
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _to_str(setting: DomainSetting) -> str:
    value = setting.value_text if setting.value_text is not None else setting.value_json
    if value is None:
        return ""
    return str(value)


def _to_list(setting: DomainSetting, upper: bool) -> set[str] | list[str]:
    value = setting.value_json if setting.value_json is not None else setting.value_text
    items: list[str]
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
    else:
        items = []
    if upper:
        return {item.upper() for item in items}
    return items


def _is_audit_path_skipped(path: str, skip_paths: list[str]) -> bool:
    return any(path.startswith(prefix) for prefix in skip_paths)


def _include_api_router(router, dependencies=None):
    app.include_router(router, dependencies=dependencies)
    app.include_router(router, prefix="/api/v1", dependencies=dependencies)


_include_api_router(auth_router, dependencies=[Depends(require_role("admin"))])
_include_api_router(auth_flow_router)
_include_api_router(rbac_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(me_router)
_include_api_router(workflow_tasks_router, dependencies=[Depends(require_tenant_auth)])
app.include_router(
    people_router,
    prefix="/api/v1",
    dependencies=[Depends(require_tenant_auth)],
)
_include_api_router(audit_router)
app.include_router(
    settings_router,
    prefix="/api/v1",
    dependencies=[Depends(require_tenant_auth)],
)
_include_api_router(scheduler_router, dependencies=[Depends(require_tenant_auth)])
app.include_router(web_home_router)
app.include_router(auth_web_router)
app.include_router(admin_web_router)
app.include_router(admin_sync_router)  # Admin sync management UI
app.include_router(admin_crm_sync_router)  # DotMac CRM sync management UI
app.include_router(profile_web_router)
app.include_router(finance_web_router, prefix="/finance")
app.include_router(expense_web_router)
app.include_router(
    finance_settings_web_router
)  # Has its own /settings prefix (finance)
app.include_router(automation_web_router)  # Has its own /automation prefix
app.include_router(payroll_alias_web_router)
app.include_router(people_web_router)
app.include_router(notifications_web_router)
app.include_router(workflow_tasks_web_router)

# Finance Accounting Routers (authenticated with tenant context)
_include_api_router(gl_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(ap_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(ar_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(lease_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(tax_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(cons_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(rpt_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(banking_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(import_export_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(opening_balance_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(search_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(payments_router, dependencies=[Depends(require_tenant_auth)])
# Payments webhook router - NO authentication (uses signature verification)
_include_api_router(payments_webhook_router)

# People/HR Routers
_include_api_router(people_hr_router, dependencies=[Depends(require_tenant_auth)])

# Expense Management (independent module)
_include_api_router(expense_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(expense_limits_router, dependencies=[Depends(require_tenant_auth)])

# Support/Helpdesk
app.include_router(
    support_router,
    prefix="/api/v1",
    dependencies=[Depends(require_tenant_auth)],
)

# Project Management
app.include_router(
    pm_router,
    prefix="/api/v1",
    dependencies=[Depends(require_tenant_auth)],
)

# Fleet Management
app.include_router(
    fleet_router,
    prefix="/api/v1",
    dependencies=[Depends(require_tenant_auth)],
)

# CRM Integration (sync from crm.dotmac.io)
_include_api_router(crm_router, dependencies=[Depends(require_tenant_auth)])
# CRM webhook router - NO authentication (uses HMAC signature verification)
_include_api_router(crm_webhook_router)
# DotMac CRM Sync - service auth (API key) for bulk sync, tenant auth for UI endpoints
_include_api_router(crm_sync_router)

# Public Careers Portal (no authentication required)
# Serve API under /api/v1 to avoid clashing with web UI routes.
app.include_router(careers_api_router, prefix="/api/v1")  # API endpoints
app.include_router(careers_web_router)  # Web UI routes

# Public Onboarding Self-Service Portal (token-based auth, no login required)
app.include_router(onboarding_portal_router)  # Web UI routes

# Standalone module web routes
app.include_router(fixed_assets_web_router)  # /fixed-assets/* web routes
app.include_router(inventory_web_router)  # /inventory/* web routes
app.include_router(fleet_web_router)  # /fleet/* web routes
app.include_router(procurement_web_router)  # /procurement/* web routes
app.include_router(support_web_router)  # /support/* web routes
app.include_router(projects_web_router)  # /projects/* web routes
app.include_router(module_settings_web_router)  # /settings/* web routes

# Standalone module API routes
_include_api_router(fa_api_router, dependencies=[Depends(require_tenant_auth)])
app.include_router(
    inv_api_router,
    prefix="/api/v1",
    dependencies=[Depends(require_tenant_auth)],
)
app.include_router(
    procurement_router,
    prefix="/api/v1",
    dependencies=[Depends(require_tenant_auth)],
)
_include_api_router(ipsas_router, dependencies=[Depends(require_tenant_auth)])

static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return RedirectResponse(url="/static/favicon.svg")


@app.get("/health")
def health_check():
    """Basic health check endpoint (backwards compatibility)."""
    return {"status": "ok"}


@app.get("/health/live")
def liveness_probe():
    """Liveness probe - checks if the process is running.

    Kubernetes uses this to determine if the container should be restarted.
    This should always return 200 if the Python process is running.
    """
    return {"status": "alive"}


@app.get("/health/ready")
def readiness_probe():
    """Readiness probe - checks if the app is ready to serve traffic.

    Kubernetes uses this to determine if the pod should receive traffic.
    Checks critical dependencies (database, etc.).

    Returns:
        200: Ready to serve traffic
        503: Not ready (dependency failure)
    """
    checks = {
        "database": _check_database(),
        "redis": _check_redis(),
    }

    all_healthy = all(check["healthy"] for check in checks.values())

    if all_healthy:
        return {"status": "ready", "checks": checks}
    else:
        return Response(
            content=JSONResponse(
                content={"status": "not_ready", "checks": checks}
            ).body,
            status_code=503,
            media_type="application/json",
        )


def _check_database() -> dict:
    """Check database connectivity."""
    try:
        db = SessionLocal()
        try:
            # Simple query to verify connection
            db.execute(text("SELECT 1"))
            return {"healthy": True, "message": "Connected"}
        finally:
            db.close()
    except Exception as e:
        return {"healthy": False, "message": str(e)[:100]}


def _check_redis() -> dict:
    """Check Redis connectivity (optional dependency)."""
    import os

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return {"healthy": True, "message": "Not configured (optional)"}

    try:
        import redis

        client = redis.from_url(redis_url, socket_connect_timeout=2)
        client.ping()
        return {"healthy": True, "message": "Connected"}
    except ImportError:
        return {"healthy": False, "message": "Redis package not installed"}
    except Exception as e:
        return {"healthy": False, "message": str(e)[:100]}


def _metrics_authorized(request: Request) -> bool:
    if os.getenv("METRICS_AUTH_DISABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True
    token = os.getenv("METRICS_TOKEN", "").strip()
    if token:
        header_token = request.headers.get("x-metrics-token", "").strip()
        return header_token == token
    # If no token configured, only allow local access.
    return bool(request.client and request.client.host in {"127.0.0.1", "::1"})


@app.get("/metrics")
def metrics(request: Request):
    if not _metrics_authorized(request):
        return Response(status_code=403)
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
