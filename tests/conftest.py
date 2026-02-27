import os
import sys
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from types import ModuleType

import anyio.to_thread as anyio_to_thread
import fastapi.concurrency as fastapi_concurrency
import fastapi.dependencies.utils as fastapi_deps
import fastapi.routing as fastapi_routing
import pytest
import starlette.background as starlette_background
import starlette.concurrency as starlette_concurrency
import jwt
from sqlalchemy import String, Text, TypeDecorator, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool


# Create a SQLite-compatible UUID type that stores as VARCHAR
class SQLiteUUID(TypeDecorator):
    """UUID type that works with SQLite by storing as VARCHAR."""

    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if isinstance(value, uuid.UUID):
                return str(value)
            return str(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            if not isinstance(value, uuid.UUID):
                return uuid.UUID(value)
        return value


# Monkey-patch the postgresql UUID import to use our SQLite-compatible version
# This is done BEFORE any app model imports so they use the patched version
import sqlalchemy.dialects.postgresql as pg_dialect  # noqa: E402

_original_uuid = pg_dialect.UUID
_original_jsonb = getattr(pg_dialect, "JSONB", None)


class PatchedUUID(SQLiteUUID):
    """Patched UUID that uses SQLite-compatible storage."""

    cache_ok = True

    def __init__(self, as_uuid=True):
        super().__init__()
        self.as_uuid = as_uuid


# Replace the PostgreSQL UUID with our patched version
pg_dialect.UUID = PatchedUUID


class PatchedJSONB(Text):
    """Patched JSONB that uses TEXT storage for SQLite."""

    cache_ok = True


if _original_jsonb is not None:
    pg_dialect.JSONB = PatchedJSONB


# Create a test engine BEFORE any app imports
_test_engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    execution_options={
        "schema_translate_map": {
            "audit": None,
            "expense": None,
            "platform": None,
            "gl": None,
            "ap": None,
            "core_org": None,
            "hr": None,
            "pm": None,
            "support": None,
            "automation": None,
        }
    },
)


# Create a mock for the app.db module that uses our test engine
class TestBase(DeclarativeBase):
    pass


_TestSessionLocal = sessionmaker(bind=_test_engine, autoflush=False, autocommit=False)


# Mock AsyncSessionLocal for tests (SQLite doesn't support async)
class MockAsyncSessionLocalProxy:
    """Mock proxy class for tests that don't actually use async sessions."""

    def __call__(self):
        # Return a sync session wrapped to look like async session
        return _TestSessionLocal()


_MockAsyncSessionLocal = MockAsyncSessionLocalProxy()


# Create a mock db module
mock_db_module = ModuleType("app.db")
mock_db_module.Base = TestBase
mock_db_module.SessionLocal = _TestSessionLocal
mock_db_module.AsyncSessionLocal = _MockAsyncSessionLocal
mock_db_module.get_engine = lambda: _test_engine
mock_db_module.get_async_session_local = lambda: _TestSessionLocal
mock_db_module.get_auth_db_session = lambda: _TestSessionLocal()
mock_db_module.get_auth_db = lambda: _TestSessionLocal()


def _get_db_session():
    db = _TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


mock_db_module.get_db_session = _get_db_session
mock_db_module.get_db = _get_db_session

# Also mock app.config to prevent .env loading
mock_config_module = ModuleType("app.config")

# Mock app.rls module with no-op functions for SQLite (RLS uses PostgreSQL-specific features)
mock_rls_module = ModuleType("app.rls")


# No-op sync RLS functions for SQLite
def _noop_set_org_sync(db, organization_id):
    """No-op for SQLite: PostgreSQL RLS not available."""
    pass


def _noop_clear_org_sync(db):
    """No-op for SQLite: PostgreSQL RLS not available."""
    pass


def _noop_enable_bypass_sync(db):
    """No-op for SQLite: PostgreSQL RLS not available."""
    pass


def _noop_disable_bypass_sync(db):
    """No-op for SQLite: PostgreSQL RLS not available."""
    pass


@contextmanager
def _noop_tenant_context_sync(db, organization_id):
    """No-op tenant context for SQLite."""
    yield


# No-op async RLS functions for SQLite
async def _noop_set_org_async(db, organization_id):
    """No-op for SQLite: PostgreSQL RLS not available."""
    pass


async def _noop_clear_org_async(db):
    """No-op for SQLite: PostgreSQL RLS not available."""
    pass


async def _noop_enable_bypass_async(db):
    """No-op for SQLite: PostgreSQL RLS not available."""
    pass


async def _noop_disable_bypass_async(db):
    """No-op for SQLite: PostgreSQL RLS not available."""
    pass


@asynccontextmanager
async def _noop_tenant_context(db, organization_id):
    """No-op tenant context for SQLite."""
    yield


# Assign mock functions to the module
mock_rls_module.set_current_organization_sync = _noop_set_org_sync
mock_rls_module.clear_organization_context_sync = _noop_clear_org_sync
mock_rls_module.enable_rls_bypass_sync = _noop_enable_bypass_sync
mock_rls_module.disable_rls_bypass_sync = _noop_disable_bypass_sync
mock_rls_module.tenant_context_sync = _noop_tenant_context_sync
mock_rls_module.set_current_organization = _noop_set_org_async
mock_rls_module.clear_organization_context = _noop_clear_org_async
mock_rls_module.enable_rls_bypass = _noop_enable_bypass_async
mock_rls_module.disable_rls_bypass = _noop_disable_bypass_async
mock_rls_module.tenant_context = _noop_tenant_context


class MockSettings:
    database_url = "sqlite+pysqlite:///:memory:"
    db_pool_size = 5
    db_max_overflow = 10
    db_pool_timeout = 30
    db_pool_recycle = 1800
    avatar_upload_dir = "static/avatars"
    avatar_max_size_bytes = 2 * 1024 * 1024
    avatar_allowed_types = "image/jpeg,image/png,image/gif,image/webp"
    avatar_url_prefix = "/static/avatars"
    brand_name = "Starter Template"
    brand_tagline = "FastAPI starter"
    brand_logo_url = None
    brand_mark = None
    # IFRS default currencies
    default_functional_currency_code = "USD"
    default_presentation_currency_code = "USD"
    landing_hero_badge = "IFRS-ready accounting"
    landing_hero_title = "Close faster with audit-ready accounting"
    landing_hero_subtitle = (
        "Multi-entity support, clean audit trail, and accurate AR/AP aging."
    )
    landing_cta_primary = "Start trial"
    landing_cta_secondary = "View sample reports"
    landing_content_json = None
    # Resume upload settings
    resume_upload_dir = "uploads/resumes"
    resume_max_size_bytes = 5 * 1024 * 1024
    resume_allowed_extensions = ".pdf,.doc,.docx"
    # CAPTCHA settings
    captcha_site_key = None
    captcha_secret_key = None
    # App URL
    app_url = "http://localhost:8000"
    # SSO settings
    sso_enabled = False
    sso_jwt_secret = None
    sso_cookie_domain = None
    # Coach / Intelligence Engine
    coach_enabled = False
    coach_llm_backends = "llama,deepseek"
    coach_llm_default_backend = "deepseek"
    coach_llm_fast_backend = "llama"
    coach_llm_standard_backend = "deepseek"
    coach_llm_deep_backend = "deepseek"
    coach_llm_llama_base_url = ""
    coach_llm_llama_api_key = ""
    coach_llm_llama_model_fast = ""
    coach_llm_llama_model_standard = ""
    coach_llm_llama_model_deep = ""
    coach_llm_deepseek_base_url = ""
    coach_llm_deepseek_api_key = ""
    coach_llm_deepseek_model_fast = ""
    coach_llm_deepseek_model_standard = ""
    coach_llm_deepseek_model_deep = ""
    coach_llm_timeout_s = 30
    coach_llm_max_retries = 2
    coach_llm_max_output_tokens = 1200
    coach_monthly_token_budget = 500_000
    coach_cache_ttl_hours = 24
    coach_max_insights_per_run = 20
    # S3 / MinIO settings
    s3_endpoint_url = "http://minio:9000"
    s3_access_key = "minioadmin"
    s3_secret_key = "minioadmin"
    s3_bucket_name = "dotmac-erp-test"
    s3_region = "us-east-1"
    # Branding settings
    branding_upload_dir = "static/branding"
    branding_max_size_bytes = 5 * 1024 * 1024
    branding_allowed_types = "image/jpeg,image/png,image/gif,image/webp,image/svg+xml,image/x-icon,image/vnd.microsoft.icon"
    branding_url_prefix = "/static/branding"
    # Generated docs
    generated_docs_dir = "uploads/generated_docs"
    # DB statement timeout
    db_statement_timeout_ms = 30000
    # Default org
    default_organization_id = None


mock_config_module.settings = MockSettings()
mock_config_module.Settings = MockSettings

# Insert mocks before any app imports
sys.modules["app.config"] = mock_config_module
sys.modules["app.db"] = mock_db_module
sys.modules["app.rls"] = mock_rls_module

# Set environment variables
os.environ["JWT_SECRET"] = "test-secret"
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["TOTP_ENCRYPTION_KEY"] = "QLUJktsTSfZEbST4R-37XmQ0tCkiVCBXZN2Zt053w8g="
os.environ["TOTP_ISSUER"] = "StarterTemplate"
os.environ.setdefault("PYTEST_CURRENT_TEST", "1")

# Now import the models - they'll use our mocked db module

from app.models.analytics.org_metric_snapshot import OrgMetricSnapshot  # noqa: E402
from app.models.audit import AuditActorType, AuditEvent  # noqa: E402
from app.models.auth import (  # noqa: E402
    ApiKey,
    MFAMethod,
    SessionStatus,
    UserCredential,
)
from app.models.auth import (  # noqa: E402
    Session as AuthSession,
)
from app.models.coach.insight import CoachInsight  # noqa: E402
from app.models.coach.report import CoachReport  # noqa: E402
from app.models.domain_settings import (  # noqa: E402
    DomainSetting,
    DomainSettingHistory,
    SettingDomain,
)
from app.models.expense import (  # noqa: E402
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimAction,
    ExpenseClaimItem,
)
from app.models.finance.platform.idempotency_record import (  # noqa: E402
    IdempotencyRecord,
)

# Import discipline models to resolve Employee relationship
from app.models.people.discipline import DisciplinaryCase  # noqa: E402,F401
from app.models.person import Person  # noqa: E402
from app.models.rbac import Permission, PersonRole, Role, RolePermission  # noqa: E402
from app.models.scheduler import ScheduledTask, ScheduleType  # noqa: E402

# List of tables that are SQLite-compatible (public schema models only)
# IFRS models use PostgreSQL-specific types (JSONB, ARRAY) that SQLite doesn't support
SQLITE_COMPATIBLE_TABLES = [
    Person.__table__,
    UserCredential.__table__,
    AuthSession.__table__,
    ApiKey.__table__,
    MFAMethod.__table__,
    Role.__table__,
    Permission.__table__,
    RolePermission.__table__,
    PersonRole.__table__,
    CoachInsight.__table__,
    CoachReport.__table__,
    OrgMetricSnapshot.__table__,
    AuditEvent.__table__,
    DomainSetting.__table__,
    DomainSettingHistory.__table__,
    ScheduledTask.__table__,
    ExpenseCategory.__table__,
    ExpenseClaim.__table__,
    ExpenseClaimItem.__table__,
    ExpenseClaimAction.__table__,
    IdempotencyRecord.__table__,
]

# Create only SQLite-compatible tables, tolerating per-table failures
import warnings  # noqa: E402


def _strip_sqlite_server_defaults(tables):
    for table in tables:
        for column in table.columns:
            default = column.server_default
            if default is None:
                continue
            default_text = str(getattr(default, "arg", default)).lower()
            if "gen_random_uuid" in default_text or "uuid_generate" in default_text:
                column.server_default = None


_strip_sqlite_server_defaults(SQLITE_COMPATIBLE_TABLES)
for table in SQLITE_COMPATIBLE_TABLES:
    try:
        table.create(_test_engine, checkfirst=True)
    except Exception as e:
        warnings.warn(f"Could not create test table {table.name}: {e}", stacklevel=2)

# Re-export Base for compatibility
Base = TestBase


@pytest.fixture(scope="session")
def engine():
    return _test_engine


@pytest.fixture()
def db_session(engine):
    """Create a database session for testing.

    Uses the same connection as the StaticPool engine to ensure
    all operations see the same data.
    """
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


DEFAULT_TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture()
def person(db_session):
    person = Person(
        first_name="Test",
        last_name="User",
        email=_unique_email(),
        organization_id=DEFAULT_TEST_ORG_ID,
    )
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)
    return person


@pytest.fixture(autouse=True)
def auth_env():
    # Environment variables are set at module level above
    # This fixture ensures they're available for each test
    pass


# ============ FastAPI Test Client Fixtures ============


@pytest.fixture()
def client(db_session):
    """Create a test client with database dependency override."""
    from fastapi import APIRouter, Depends, FastAPI

    from app.api.audit import get_db as audit_get_db
    from app.api.audit import router as audit_router
    from app.api.auth_flow import get_db as auth_flow_get_db
    from app.api.auth_flow import router as auth_flow_router
    from app.api.people.discipline import get_db as discipline_get_db
    from app.api.people.discipline import router as discipline_router
    from app.api.persons import get_db as persons_get_db
    from app.api.persons import router as persons_router
    from app.api.rbac import get_db as rbac_get_db
    from app.api.rbac import router as rbac_router
    from app.api.scheduler import get_db as scheduler_get_db
    from app.api.scheduler import router as scheduler_router
    from app.api.service_hooks import get_db as service_hooks_get_db
    from app.api.service_hooks import router as service_hooks_router
    from app.api.settings import get_db as settings_get_db
    from app.api.settings import router as settings_router
    from app.errors import register_error_handlers
    from app.services.auth_dependencies import (
        _get_db as auth_deps_get_db,
    )
    from app.services.auth_dependencies import (
        require_tenant_auth,
    )
    from app.services.settings_seed import (
        seed_audit_settings,
        seed_auth_settings,
        seed_automation_settings,
        seed_email_settings,
        seed_features_settings,
        seed_reporting_settings,
        seed_scheduler_settings,
    )

    Session = sessionmaker(bind=db_session.bind, autoflush=False, autocommit=False)

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    def _include_api_router(router, dependencies=None):
        app.include_router(router, dependencies=dependencies)
        app.include_router(router, prefix="/api/v1", dependencies=dependencies)

    # Build a minimal app for API tests to avoid costly full app import.
    app = FastAPI()
    register_error_handlers(app)
    _include_api_router(auth_flow_router)
    _include_api_router(audit_router)
    _include_api_router(rbac_router, dependencies=[Depends(require_tenant_auth)])
    _include_api_router(persons_router, dependencies=[Depends(require_tenant_auth)])
    _include_api_router(settings_router, dependencies=[Depends(require_tenant_auth)])
    _include_api_router(scheduler_router, dependencies=[Depends(require_tenant_auth)])
    _include_api_router(
        service_hooks_router, dependencies=[Depends(require_tenant_auth)]
    )

    # Discipline routes live under /api/v1/people/discipline in tests.
    people_v1 = APIRouter(
        prefix="/api/v1/people", dependencies=[Depends(require_tenant_auth)]
    )
    people_v1.include_router(discipline_router)
    app.include_router(people_v1)

    # Override all get_db dependencies
    app.dependency_overrides[persons_get_db] = override_get_db
    app.dependency_overrides[auth_flow_get_db] = override_get_db
    app.dependency_overrides[rbac_get_db] = override_get_db
    app.dependency_overrides[audit_get_db] = override_get_db
    app.dependency_overrides[settings_get_db] = override_get_db
    app.dependency_overrides[scheduler_get_db] = override_get_db
    app.dependency_overrides[service_hooks_get_db] = override_get_db
    app.dependency_overrides[discipline_get_db] = override_get_db
    app.dependency_overrides[auth_deps_get_db] = override_get_db

    # Convert sync endpoints to async wrappers to avoid threadpool usage.
    import inspect

    from fastapi.routing import APIRoute, request_response

    for route in app.router.routes:
        if isinstance(route, APIRoute) and not inspect.iscoroutinefunction(
            route.endpoint
        ):
            endpoint = route.endpoint

            async def _async_endpoint(*args, __endpoint=endpoint, **kwargs):
                return __endpoint(*args, **kwargs)

            _async_endpoint.__signature__ = inspect.signature(endpoint)
            route.endpoint = _async_endpoint
            route.dependant.call = _async_endpoint
            route.dependant.is_coroutine = True
            route.app = request_response(route.get_route_handler())

    # Seed the settings in the test database
    seed_auth_settings(db_session)
    seed_audit_settings(db_session)
    seed_scheduler_settings(db_session)
    seed_email_settings(db_session)
    seed_features_settings(db_session)
    seed_automation_settings(db_session)
    seed_reporting_settings(db_session)

    import asyncio
    import json as _json
    from http.cookies import SimpleCookie
    from urllib.parse import urlsplit

    class _Response:
        def __init__(
            self, status_code: int, headers: list[tuple[bytes, bytes]], body: bytes
        ):
            self.status_code = status_code
            self._headers = {k.decode().lower(): v.decode() for k, v in headers}
            self._body = body

        @property
        def text(self):
            return self._body.decode(errors="replace")

        def json(self):
            return _json.loads(self._body.decode())

        @property
        def headers(self):
            return self._headers

    class _ASGITestClient:
        def __init__(self, asgi_app):
            self._app = asgi_app
            self._cookies = SimpleCookie()
            self._cookie_view = None

        def request(self, method: str, url: str, **kwargs):
            body = b""
            headers = {k.lower(): v for k, v in (kwargs.get("headers") or {}).items()}
            if "json" in kwargs and kwargs["json"] is not None:
                body = _json.dumps(kwargs["json"]).encode()
                headers.setdefault("content-type", "application/json")
            elif "data" in kwargs and kwargs["data"] is not None:
                data = kwargs["data"]
                body = (
                    data if isinstance(data, (bytes, bytearray)) else str(data).encode()
                )

            if self._cookies and "cookie" not in headers:
                headers["cookie"] = "; ".join(
                    f"{m.key}={m.value}" for m in self._cookies.values()
                )

            parsed = urlsplit(url)
            path = parsed.path or "/"
            query_string = parsed.query.encode()

            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": method.upper(),
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": query_string,
                "headers": [
                    (k.encode(), v.encode() if isinstance(v, str) else v)
                    for k, v in headers.items()
                ],
                "client": ("testclient", 123),
                "server": ("testserver", 80),
            }

            async def _call_app():
                response_headers: list[tuple[bytes, bytes]] = []
                response_body_parts: list[bytes] = []
                status_code = 500

                request_sent = False

                async def receive():
                    nonlocal body, request_sent
                    if not request_sent:
                        request_sent = True
                        data = body
                        body = b""
                        return {
                            "type": "http.request",
                            "body": data,
                            "more_body": False,
                        }
                    return {"type": "http.disconnect"}

                async def send(message):
                    nonlocal status_code
                    if message["type"] == "http.response.start":
                        status_code = message["status"]
                        response_headers.extend(message.get("headers", []))
                    elif message["type"] == "http.response.body":
                        response_body_parts.append(message.get("body", b""))

                await self._app(scope, receive, send)
                return status_code, response_headers, b"".join(response_body_parts)

            status_code, response_headers, response_body = asyncio.run(_call_app())

            response = _Response(status_code, response_headers, response_body)
            for header_name, header_value in response_headers:
                if header_name.lower() == b"set-cookie":
                    self._cookies.load(header_value.decode())
            return response

        def get(self, url: str, **kwargs):
            return self.request("GET", url, **kwargs)

        def post(self, url: str, **kwargs):
            return self.request("POST", url, **kwargs)

        def put(self, url: str, **kwargs):
            return self.request("PUT", url, **kwargs)

        def patch(self, url: str, **kwargs):
            return self.request("PATCH", url, **kwargs)

        def delete(self, url: str, **kwargs):
            return self.request("DELETE", url, **kwargs)

        @property
        def cookies(self):
            if self._cookie_view is None:

                class _CookieView:
                    def __init__(self, jar):
                        self._jar = jar

                    def get(self, key, default=None):
                        morsel = self._jar.get(key)
                        if morsel is None:
                            return default
                        return morsel.value

                self._cookie_view = _CookieView(self._cookies)
            return self._cookie_view

    yield _ASGITestClient(app)

    app.dependency_overrides.clear()


def _create_access_token(
    person_id: str, session_id: str, roles: list[str] = None, scopes: list[str] = None
) -> str:
    """Create a JWT access token for testing."""
    secret = os.getenv("JWT_SECRET", "test-secret")
    algorithm = os.getenv("JWT_ALGORITHM", "HS256")
    now = datetime.now(UTC)
    expire = now + timedelta(minutes=15)
    payload = {
        "sub": person_id,
        "session_id": session_id,
        "roles": roles or [],
        "scopes": scopes or [],
        "typ": "access",
        "exp": int(expire.timestamp()),
        "iat": int(now.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


@pytest.fixture()
def auth_session(db_session, person):
    """Create an authenticated session for a person."""
    session = AuthSession(
        person_id=person.id,
        token_hash=f"test-token-{uuid.uuid4().hex}",
        status=SessionStatus.active,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session


@pytest.fixture()
def auth_token(person, auth_session):
    """Create a valid JWT token for authenticated requests."""
    scopes = [
        "rbac:manage",
        "people:read",
        "people:write",
        "settings:manage",
    ]
    return _create_access_token(str(person.id), str(auth_session.id), scopes=scopes)


@pytest.fixture()
def auth_headers(auth_token):
    """Return authorization headers for authenticated requests."""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture()
def admin_role(db_session):
    """Create an admin role."""
    role = db_session.query(Role).filter(Role.name == "admin").first()
    if role:
        return role
    role = Role(name="admin", description="Administrator role")
    db_session.add(role)
    db_session.commit()
    db_session.refresh(role)
    return role


@pytest.fixture()
def admin_person(db_session, admin_role):
    """Create a person with admin role."""
    person = Person(
        first_name="Admin",
        last_name="User",
        email=_unique_email(),
        organization_id=DEFAULT_TEST_ORG_ID,
    )
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)

    # Assign admin role
    person_role = PersonRole(person_id=person.id, role_id=admin_role.id)
    db_session.add(person_role)
    db_session.commit()

    return person


@pytest.fixture()
def admin_session(db_session, admin_person):
    """Create an authenticated session for admin."""
    session = AuthSession(
        person_id=admin_person.id,
        token_hash=f"admin-token-{uuid.uuid4().hex}",
        status=SessionStatus.active,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session


@pytest.fixture()
def admin_token(admin_person, admin_session):
    """Create a valid JWT token for admin requests."""
    return _create_access_token(
        str(admin_person.id),
        str(admin_session.id),
        roles=["admin"],
        scopes=["audit:read", "audit:*"],
    )


@pytest.fixture()
def admin_headers(admin_token):
    """Return authorization headers for admin requests."""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture()
def user_credential(db_session, person):
    """Create a user credential for testing."""
    from app.services.auth_flow import hash_password

    credential = UserCredential(
        person_id=person.id,
        username=f"testuser_{uuid.uuid4().hex[:8]}",
        password_hash=hash_password("testpassword123"),
        is_active=True,
    )
    db_session.add(credential)
    db_session.commit()
    db_session.refresh(credential)
    return credential


@pytest.fixture()
def role(db_session):
    """Create a test role."""
    role = Role(name=f"test_role_{uuid.uuid4().hex[:8]}", description="Test role")
    db_session.add(role)
    db_session.commit()
    db_session.refresh(role)
    return role


@pytest.fixture()
def permission(db_session):
    """Create a test permission."""
    perm = Permission(
        key=f"test:permission:{uuid.uuid4().hex[:8]}",
        description="Test permission",
    )
    db_session.add(perm)
    db_session.commit()
    db_session.refresh(perm)
    return perm


@pytest.fixture()
def audit_event(db_session, person):
    """Create a test audit event."""
    event = AuditEvent(
        actor_id=str(person.id),
        actor_type=AuditActorType.user,
        action="test_action",
        entity_type="test_entity",
        entity_id=str(uuid.uuid4()),
        is_success=True,
        status_code=200,
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)
    return event


@pytest.fixture()
def domain_setting(db_session):
    """Create a test domain setting."""
    setting = DomainSetting(
        domain=SettingDomain.auth,
        key=f"test_setting_{uuid.uuid4().hex[:8]}",
        value_text="test_value",
    )
    db_session.add(setting)
    db_session.commit()
    db_session.refresh(setting)
    return setting


@pytest.fixture()
def scheduled_task(db_session):
    """Create a test scheduled task."""
    task = ScheduledTask(
        name=f"test_task_{uuid.uuid4().hex[:8]}",
        task_name="app.tasks.test_task",
        schedule_type=ScheduleType.interval,
        interval_seconds=300,
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    return task


# Avoid anyio threadpool hangs by running sync callables inline.
async def _run_in_threadpool(func, *args, **kwargs):
    return func(*args, **kwargs)


async def _run_sync_no_limiter(func, *args, **kwargs):
    kwargs.pop("limiter", None)
    return func(*args, **kwargs)


fastapi_concurrency.run_in_threadpool = _run_in_threadpool
starlette_concurrency.run_in_threadpool = _run_in_threadpool
fastapi_deps.run_in_threadpool = _run_in_threadpool
fastapi_routing.run_in_threadpool = _run_in_threadpool
starlette_background.run_in_threadpool = _run_in_threadpool
anyio_to_thread.run_sync = _run_sync_no_limiter
