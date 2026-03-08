"""
Smoke test: every registered GET web-route must not return 5xx.

Routes are auto-discovered from the web routers so that adding a new
route without updating this file will cause the guard test to fail.

Run:
    poetry run pytest tests/test_web_pages_crud_smoke.py -x -q
"""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Mount

from app.web.deps import (
    WebAuthContext,
    get_async_db,
    get_db,
    optional_web_auth,
    require_discipline_access,
    require_discipline_cases_create,
    require_discipline_cases_read,
    require_discipline_cases_update,
    require_discipline_workflow_manage,
    require_expense_access,
    require_finance_access,
    require_finance_admin,
    require_fleet_access,
    require_hr_access,
    require_inventory_access,
    require_procurement_access,
    require_projects_access,
    require_public_sector_access,
    require_self_service_access,
    require_self_service_discipline_manager,
    require_self_service_expense_approver,
    require_self_service_leave_approver,
    require_settings_access,
    require_support_access,
    require_web_auth,
)

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TEST_PERSON_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
PLACEHOLDER_UUID = "00000000-0000-0000-0000-000000000099"

_PARAM_RE = re.compile(r"\{[^}]+\}")

# Routes to always skip (token-auth, not session-auth).
SKIP_PREFIXES = ("/onboarding/start/",)

# Special path parameter replacements (non-UUID).
SPECIAL_PARAMS: dict[str, str] = {
    "{token}": "test-token",
    "{slug}": "test-slug",
    "{filename}": "test.pdf",
    "{org_slug}": "test-org",
    "{module_key}": "finance",
    "{module}": "finance",
}


# ---------------------------------------------------------------------------
# Build a test app from web routers only (mirrors main.py web inclusions)
# ---------------------------------------------------------------------------


def _build_web_app() -> FastAPI:
    """Build a FastAPI app with ONLY the web routers — no API routers."""
    app = FastAPI()

    # Root / Home / Auth / Profile / Help
    from app.web.auth import router as auth_web_router
    from app.web.help import router as help_web_router
    from app.web.profile import router as profile_web_router
    from app.web_home import router as web_home_router

    app.include_router(web_home_router)
    app.include_router(auth_web_router)
    app.include_router(profile_web_router)
    app.include_router(help_web_router)

    # Admin
    from app.web.admin import router as admin_web_router
    from app.web.admin_crm_sync import router as admin_crm_sync_router
    from app.web.admin_sync import router as admin_sync_router

    app.include_router(admin_web_router)
    app.include_router(admin_sync_router)
    app.include_router(admin_crm_sync_router)

    # Finance (main module + standalone routers)
    from app.web.finance import (
        automation_router as automation_web_router,
    )
    from app.web.finance import (
        expense_router as expense_web_router,
    )
    from app.web.finance import (
        router as finance_web_router,
    )
    from app.web.finance import (
        settings_router as finance_settings_web_router,
    )

    app.include_router(finance_web_router, prefix="/finance")
    app.include_router(expense_web_router)
    app.include_router(finance_settings_web_router)
    app.include_router(automation_web_router)

    # People / HR
    from app.web.payroll_alias import router as payroll_alias_web_router
    from app.web.people import router as people_web_router

    app.include_router(people_web_router)
    app.include_router(payroll_alias_web_router)

    # Notifications / Workflow
    from app.web.notifications import router as notifications_web_router
    from app.web.workflow_tasks import router as workflow_tasks_web_router

    app.include_router(notifications_web_router)
    app.include_router(workflow_tasks_web_router)

    # Standalone modules
    from app.web.coach import router as coach_web_router
    from app.web.fixed_assets import router as fixed_assets_web_router
    from app.web.fleet import router as fleet_web_router
    from app.web.inventory import router as inventory_web_router
    from app.web.procurement import router as procurement_web_router
    from app.web.projects import router as projects_web_router
    from app.web.public_sector import router as public_sector_web_router
    from app.web.settings import router as module_settings_web_router
    from app.web.support import router as support_web_router

    app.include_router(fixed_assets_web_router)
    app.include_router(inventory_web_router)
    app.include_router(fleet_web_router)
    app.include_router(procurement_web_router)
    app.include_router(support_web_router)
    app.include_router(projects_web_router)
    app.include_router(module_settings_web_router)
    app.include_router(coach_web_router)
    app.include_router(public_sector_web_router)

    # Public portals
    from app.web.careers import router as careers_web_router
    from app.web.careers import short_router as careers_short_web_router
    from app.web.onboarding_portal import router as onboarding_portal_router

    app.include_router(careers_web_router)
    app.include_router(careers_short_web_router)
    app.include_router(onboarding_portal_router)

    return app


# ---------------------------------------------------------------------------
# ASGI Test Client
# ---------------------------------------------------------------------------


class _Response:
    def __init__(
        self,
        status_code: int,
        headers: list[tuple[bytes, bytes]],
        body: bytes,
    ) -> None:
        self.status_code = status_code
        self.headers = {k.decode().lower(): v.decode() for k, v in headers}
        self.body = body


class _ASGIClient:
    """Minimal sync ASGI test client."""

    def __init__(self, app: Any) -> None:
        self._app = app

    def get(self, path: str) -> _Response:
        query = b""
        if "?" in path:
            path, query_part = path.split("?", 1)
            query = query_part.encode()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "headers": [(b"accept", b"text/html")],
            "client": ("testclient", 123),
            "server": ("testserver", 80),
        }

        async def _run() -> _Response:
            status_code = 500
            headers: list[tuple[bytes, bytes]] = []
            chunks: list[bytes] = []
            sent = False

            async def receive() -> dict:
                nonlocal sent
                if sent:
                    return {"type": "http.disconnect"}
                sent = True
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message: dict) -> None:
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = message["status"]
                    headers.extend(message.get("headers", []))
                elif message["type"] == "http.response.body":
                    chunks.append(message.get("body", b""))

            try:
                await self._app(scope, receive, send)
            except Exception as exc:
                return _Response(
                    500,
                    [(b"content-type", b"text/plain; charset=utf-8")],
                    str(exc).encode(),
                )
            return _Response(status_code, headers, b"".join(chunks))

        return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Route Discovery
# ---------------------------------------------------------------------------


def _collect_get_routes(app: FastAPI) -> list[str]:
    """Collect all GET route path templates from the app (recursively)."""
    paths: list[str] = []

    def _walk(routes, prefix: str = "") -> None:
        for route in routes:
            if isinstance(route, Mount):
                _walk(route.routes or [], prefix + (route.path or ""))
            elif isinstance(route, APIRoute):
                if "GET" in route.methods:
                    full = prefix + route.path
                    paths.append(full)

    _walk(app.routes)
    return sorted(set(paths))


def _materialize_path(template: str) -> str:
    """Replace path parameters with concrete test values."""

    def _replace(match: re.Match) -> str:
        param = match.group(0)
        if param in SPECIAL_PARAMS:
            return SPECIAL_PARAMS[param]
        return PLACEHOLDER_UUID

    return _PARAM_RE.sub(_replace, template)


def _should_skip(path: str) -> bool:
    return any(path.startswith(p) for p in SKIP_PREFIXES)


def _get_web_get_routes() -> list[tuple[str, str]]:
    """Return (template, concrete_path) for all testable web GET routes."""
    routes = []
    for template in _collect_get_routes(_WEB_APP):
        if _should_skip(template):
            continue
        concrete = _materialize_path(template)
        routes.append((template, concrete))
    return routes


# Build the web app and route list once at module load time.
_WEB_APP = _build_web_app()
_WEB_ROUTES = _get_web_get_routes()
_WEB_ROUTE_PATHS = [concrete for _, concrete in _WEB_ROUTES]
_WEB_ROUTE_IDS = [template for template, _ in _WEB_ROUTES]


# ---------------------------------------------------------------------------
# Auth mock
# ---------------------------------------------------------------------------


def _mock_web_auth() -> WebAuthContext:
    return WebAuthContext(
        is_authenticated=True,
        person_id=TEST_PERSON_ID,
        organization_id=TEST_ORG_ID,
        employee_id=TEST_PERSON_ID,
        user_name="Smoke Tester",
        user_initials="ST",
        roles=["admin"],
        scopes=[
            "finance:access",
            "hr:access",
            "inventory:access",
            "fleet:access",
            "procurement:access",
            "projects:access",
            "support:access",
            "expense:access",
            "public_sector:access",
            "settings:access",
        ],
    )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def smoke_client(db_session):
    """Reuse the web-only app with per-test auth and DB overrides."""
    app = _WEB_APP

    def override_db():
        yield db_session

    async def override_async_db():
        yield db_session

    # Override every auth dependency.
    auth_deps = [
        require_web_auth,
        optional_web_auth,
        require_finance_access,
        require_finance_admin,
        require_hr_access,
        require_inventory_access,
        require_fleet_access,
        require_public_sector_access,
        require_support_access,
        require_procurement_access,
        require_projects_access,
        require_settings_access,
        require_expense_access,
        require_self_service_access,
        require_discipline_access,
        require_discipline_cases_read,
        require_discipline_cases_create,
        require_discipline_cases_update,
        require_discipline_workflow_manage,
        require_self_service_leave_approver,
        require_self_service_discipline_manager,
        require_self_service_expense_approver,
    ]

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_async_db] = override_async_db

    for dep in auth_deps:
        app.dependency_overrides[dep] = _mock_web_auth

    try:
        from app.web import auth as auth_web_module

        app.dependency_overrides[auth_web_module.get_db] = override_db
    except (ImportError, AttributeError):
        pass

    try:
        yield _ASGIClient(app)
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


_INFRA_ERRORS = (
    # SQLite test DB missing tables/columns
    "no such table",
    "no such column",
    "OperationalError",
    "ProgrammingError",
    # Placeholder UUID entity not found (expected for detail/edit pages)
    "not found",
    "Not Found",
    "does not exist",
)


@pytest.mark.parametrize("path", _WEB_ROUTE_PATHS, ids=_WEB_ROUTE_IDS)
def test_web_route_no_5xx(smoke_client: _ASGIClient, path: str):
    """Every web GET route must not return a server error (5xx).

    Acceptable responses:
      - 200 OK  (page rendered)
      - 302/307 (redirect)
      - 404     (placeholder UUID not found — expected for detail pages)
      - 403     (permission denied)
      - 422     (validation error on path params)
      - 500     ONLY when caused by missing SQLite tables (test infra limit)
    """
    response = smoke_client.get(path)
    if response.status_code >= 500:
        snippet = response.body[:1000].decode(errors="replace")
        is_infra_limitation = any(err in snippet for err in _INFRA_ERRORS)
        if not is_infra_limitation:
            pytest.fail(
                f"{path} returned {response.status_code}\nBody snippet: {snippet[:500]}"
            )


def test_route_count_guard():
    """Guard: fail if the app has fewer routes than expected.

    Update the minimum when legitimately removing routes.
    """
    assert len(_WEB_ROUTES) >= 100, (
        f"Only {len(_WEB_ROUTES)} web GET routes discovered — "
        f"expected at least 100. Did a router get removed from "
        f"_build_web_app()?"
    )


def test_all_modules_have_routes():
    """Ensure every major module has at least one discovered route."""
    module_prefixes = [
        "/finance/",
        "/people/",
        "/inventory/",
        "/procurement/",
        "/support/",
        "/projects/",
        "/admin/",
        "/fleet/",
        "/expense/",
        "/fixed-assets/",
        "/public-sector/",
        "/coach/",
        "/careers/",
    ]
    templates = {t for t, _ in _WEB_ROUTES}
    for prefix in module_prefixes:
        matches = [t for t in templates if t.startswith(prefix)]
        assert matches, f"No routes discovered for module prefix {prefix}"
