from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from time import monotonic
from threading import Lock
from starlette.responses import Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.auth_flow import router as auth_flow_router
from app.api.persons import router as people_router
from app.api.rbac import router as rbac_router
from app.api.scheduler import router as scheduler_router
from app.api.settings import router as settings_router
from app.api.me import router as me_router
from app.api.workflow_tasks import router as workflow_tasks_router
from app.web_home import router as web_home_router
from app.web.finance import router as finance_web_router
from app.web.finance import expense_router as expense_web_router
from app.web.finance import settings_router as settings_web_router
from app.web.finance import automation_router as automation_web_router
from app.web.auth import router as auth_web_router
from app.web.admin import router as admin_web_router
from app.web.admin_sync import router as admin_sync_router
from app.web.profile import router as profile_web_router
from app.web.people import router as people_web_router
from app.web.operations import router as operations_web_router
from app.web.notifications import router as notifications_web_router
from app.web.workflow_tasks import router as workflow_tasks_web_router
from app.api.finance import (
    gl_router,
    ap_router,
    ar_router,
    fa_router,
    inv_router,
    lease_router,
    tax_router,
    cons_router,
    rpt_router,
    banking_router,
    import_export_router,
    opening_balance_router,
    search_router,
    payments_router,
    payments_webhook_router,
)
from app.api.people import router as people_hr_router
from app.api.expense import router as expense_router
from app.api.expense_limits import router as expense_limits_router
from app.api.support import router as support_router
from app.api.pm import router as pm_router
from app.db import SessionLocal
from app.services import audit as audit_service
from app.api.deps import require_role, require_user_auth, require_tenant_auth
from app.models.domain_settings import DomainSetting, SettingDomain
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse
from app.services.settings_seed import seed_all_settings
from app.logging import configure_logging
from app.observability import ObservabilityMiddleware
from app.telemetry import setup_otel
from app.errors import register_error_handlers
from app.web.csrf import csrf_middleware
from app.middleware.rate_limit import rate_limit_middleware
from app.startup import log_startup_info, validate_startup


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
async def audit_middleware(request: Request, call_next):
    response: Response
    path = request.url.path
    db = SessionLocal()
    try:
        audit_settings = _load_audit_settings(db)
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
            db = SessionLocal()
            try:
                audit_service.audit_events.log_request(
                    db, request, Response(status_code=500)
                )
            finally:
                db.close()
        raise
    if should_log:
        db = SessionLocal()
        try:
            audit_service.audit_events.log_request(db, request, response)
        finally:
            db.close()
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
app.include_router(profile_web_router)
app.include_router(finance_web_router, prefix="/finance")
app.include_router(expense_web_router, prefix="/expense")
app.include_router(settings_web_router)  # Has its own /settings prefix
app.include_router(automation_web_router)  # Has its own /automation prefix
app.include_router(people_web_router)
app.include_router(operations_web_router)
app.include_router(notifications_web_router)
app.include_router(workflow_tasks_web_router)

# Finance Accounting Routers (authenticated with tenant context)
_include_api_router(gl_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(ap_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(ar_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(fa_router, dependencies=[Depends(require_tenant_auth)])
_include_api_router(inv_router, dependencies=[Depends(require_tenant_auth)])
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

# Support/Helpdesk (Operations module)
_include_api_router(support_router, dependencies=[Depends(require_tenant_auth)])

# Project Management (Operations module)
_include_api_router(pm_router, dependencies=[Depends(require_tenant_auth)])

app.mount("/static", StaticFiles(directory="static"), name="static")


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
        return {"healthy": True, "message": "Redis package not installed (optional)"}
    except Exception as e:
        return {"healthy": False, "message": str(e)[:100]}


@app.get("/metrics")
def metrics():
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
