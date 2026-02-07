import os
import sys
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from types import ModuleType

import pytest
import asyncio
import json
from http.cookies import SimpleCookie
from urllib.parse import urlencode, urlparse

import httpx
from jose import jwt
from sqlalchemy import create_engine, String, TypeDecorator, Text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
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
import sqlalchemy.dialects.postgresql as pg_dialect

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

# Now import the models - they'll use our mocked db module
from app.models.person import Person
from app.models.auth import (
    UserCredential,
    Session as AuthSession,
    SessionStatus,
    ApiKey,
    MFAMethod,
)
from app.models.rbac import Role, Permission, RolePermission, PersonRole
from app.models.audit import AuditEvent, AuditActorType
from app.models.domain_settings import (
    DomainSetting,
    DomainSettingHistory,
    SettingDomain,
)
from app.models.scheduler import ScheduledTask, ScheduleType
from app.models.expense import (
    ExpenseClaim,
    ExpenseClaimItem,
    ExpenseCategory,
    ExpenseClaimAction,
)
from app.models.finance.platform.idempotency_record import IdempotencyRecord

# Import discipline models to resolve Employee relationship
from app.models.people.discipline import DisciplinaryCase  # noqa: F401

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
import warnings


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
        warnings.warn(f"Could not create test table {table.name}: {e}")

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
    from app.main import app
    from app.api.persons import get_db as persons_get_db
    from app.api.auth_flow import get_db as auth_flow_get_db
    from app.api.rbac import get_db as rbac_get_db
    from app.api.audit import get_db as audit_get_db
    from app.api.settings import get_db as settings_get_db
    from app.api.scheduler import get_db as scheduler_get_db
    from app.services.auth_dependencies import _get_db as auth_deps_get_db
    from app.services.settings_seed import (
        seed_auth_settings,
        seed_audit_settings,
        seed_scheduler_settings,
    )

    def override_get_db():
        yield db_session

    # Override all get_db dependencies
    app.dependency_overrides[persons_get_db] = override_get_db
    app.dependency_overrides[auth_flow_get_db] = override_get_db
    app.dependency_overrides[rbac_get_db] = override_get_db
    app.dependency_overrides[audit_get_db] = override_get_db
    app.dependency_overrides[settings_get_db] = override_get_db
    app.dependency_overrides[scheduler_get_db] = override_get_db
    app.dependency_overrides[auth_deps_get_db] = override_get_db

    # Seed the settings in the test database
    seed_auth_settings(db_session)
    seed_audit_settings(db_session)
    seed_scheduler_settings(db_session)

    class _ASGIResponse:
        def __init__(
            self, status_code: int, headers: list[tuple[str, str]], body: bytes
        ):
            self.status_code = status_code
            self.headers = httpx.Headers(headers)
            self.content = body
            self.text = body.decode(errors="replace")
            self._cookies = httpx.Cookies()

        def json(self):
            return json.loads(self.content.decode() or "null")

        @property
        def cookies(self):
            return self._cookies

    class _ASGIClient:
        def __init__(self, app):
            self._app = app
            self._cookies = httpx.Cookies()

        def _build_headers(
            self, headers: dict | None, body: bytes
        ) -> list[tuple[bytes, bytes]]:
            hdrs = []
            if headers:
                for k, v in headers.items():
                    hdrs.append((k.lower().encode(), str(v).encode()))
            if self._cookies:
                cookie_header = "; ".join(
                    f"{name}={value}" for name, value in self._cookies.items()
                )
                hdrs.append((b"cookie", cookie_header.encode()))
            if body and not any(k == b"content-length" for k, _ in hdrs):
                hdrs.append((b"content-length", str(len(body)).encode()))
            return hdrs

        async def _request(
            self,
            method: str,
            url: str,
            json_data=None,
            data=None,
            headers: dict | None = None,
        ) -> _ASGIResponse:
            parsed = urlparse(url)
            path = parsed.path or "/"
            query = parsed.query.encode()

            body = b""
            req_headers = headers.copy() if headers else {}

            if json_data is not None:
                body = json.dumps(json_data).encode()
                req_headers.setdefault("content-type", "application/json")
            elif data is not None:
                if isinstance(data, dict):
                    body = urlencode(data, doseq=True).encode()
                else:
                    body = str(data).encode()
                req_headers.setdefault(
                    "content-type", "application/x-www-form-urlencoded"
                )

            scope = {
                "type": "http",
                "method": method,
                "path": path,
                "raw_path": path.encode(),
                "query_string": query,
                "headers": self._build_headers(req_headers, body),
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
                "scheme": "http",
            }

            response_status = 500
            response_headers: list[tuple[bytes, bytes]] = []
            response_body_parts: list[bytes] = []

            async def receive():
                nonlocal body
                if body is None:
                    return {"type": "http.disconnect"}
                data = body
                body = None
                return {"type": "http.request", "body": data, "more_body": False}

            async def send(message):
                nonlocal response_status, response_headers, response_body_parts
                if message["type"] == "http.response.start":
                    response_status = message["status"]
                    response_headers = message.get("headers", [])
                elif message["type"] == "http.response.body":
                    response_body_parts.append(message.get("body", b""))

            await self._app(scope, receive, send)

            decoded_headers = [
                (k.decode("latin-1"), v.decode("latin-1")) for k, v in response_headers
            ]
            response = _ASGIResponse(
                response_status, decoded_headers, b"".join(response_body_parts)
            )

            # Update cookies from Set-Cookie headers
            for name, value in decoded_headers:
                if name.lower() == "set-cookie":
                    cookie = SimpleCookie()
                    cookie.load(value)
                    for key, morsel in cookie.items():
                        response._cookies.set(key, morsel.value)
                        self._cookies.set(key, morsel.value)

            return response

        def request(self, method: str, url: str, **kwargs):
            return asyncio.run(self._request(method, url, **kwargs))

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

    # Mock the app startup seeding to avoid duplicate seeding
    with (
        patch("app.main.seed_auth_settings", create=True),
        patch("app.main.seed_audit_settings", create=True),
        patch("app.main.seed_scheduler_settings", create=True),
        patch("app.main.SessionLocal", return_value=MagicMock(), create=True),
    ):
        yield _ASGIClient(app)

    app.dependency_overrides.clear()


def _create_access_token(
    person_id: str, session_id: str, roles: list[str] = None, scopes: list[str] = None
) -> str:
    """Create a JWT access token for testing."""
    secret = os.getenv("JWT_SECRET", "test-secret")
    algorithm = os.getenv("JWT_ALGORITHM", "HS256")
    now = datetime.now(timezone.utc)
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
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session


@pytest.fixture()
def auth_token(person, auth_session):
    """Create a valid JWT token for authenticated requests."""
    return _create_access_token(str(person.id), str(auth_session.id))


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
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
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
