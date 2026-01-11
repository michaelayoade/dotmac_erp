import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from types import ModuleType

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine, String, TypeDecorator
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import StaticPool
from sqlalchemy import dialects


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


class PatchedUUID(SQLiteUUID):
    """Patched UUID that uses SQLite-compatible storage."""
    cache_ok = True

    def __init__(self, as_uuid=True):
        super().__init__()
        self.as_uuid = as_uuid


# Replace the PostgreSQL UUID with our patched version
pg_dialect.UUID = PatchedUUID


# Create a test engine BEFORE any app imports
_test_engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
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
mock_db_module = ModuleType('app.db')
mock_db_module.Base = TestBase
mock_db_module.SessionLocal = _TestSessionLocal
mock_db_module.AsyncSessionLocal = _MockAsyncSessionLocal
mock_db_module.get_engine = lambda: _test_engine
mock_db_module.get_async_session_local = lambda: _TestSessionLocal

# Also mock app.config to prevent .env loading
mock_config_module = ModuleType('app.config')

# Mock app.rls module with no-op functions for SQLite (RLS uses PostgreSQL-specific features)
mock_rls_module = ModuleType('app.rls')


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


# Assign mock functions to the module
mock_rls_module.set_current_organization_sync = _noop_set_org_sync
mock_rls_module.clear_organization_context_sync = _noop_clear_org_sync
mock_rls_module.enable_rls_bypass_sync = _noop_enable_bypass_sync
mock_rls_module.disable_rls_bypass_sync = _noop_disable_bypass_sync
mock_rls_module.set_current_organization = _noop_set_org_async
mock_rls_module.clear_organization_context = _noop_clear_org_async
mock_rls_module.enable_rls_bypass = _noop_enable_bypass_async
mock_rls_module.disable_rls_bypass = _noop_disable_bypass_async


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


mock_config_module.settings = MockSettings()
mock_config_module.Settings = MockSettings

# Insert mocks before any app imports
sys.modules['app.config'] = mock_config_module
sys.modules['app.db'] = mock_db_module
sys.modules['app.rls'] = mock_rls_module

# Set environment variables
os.environ["JWT_SECRET"] = "test-secret"
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["TOTP_ENCRYPTION_KEY"] = "QLUJktsTSfZEbST4R-37XmQ0tCkiVCBXZN2Zt053w8g="
os.environ["TOTP_ISSUER"] = "StarterTemplate"

# Now import the models - they'll use our mocked db module
from app.models.person import Person
from app.models.auth import UserCredential, Session as AuthSession, SessionStatus, ApiKey, MFAMethod
from app.models.rbac import Role, Permission, RolePermission, PersonRole
from app.models.audit import AuditEvent, AuditActorType
from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.scheduler import ScheduledTask, ScheduleType

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
    ScheduledTask.__table__,
]

# Create only SQLite-compatible tables
try:
    TestBase.metadata.create_all(_test_engine, tables=SQLITE_COMPATIBLE_TABLES)
except Exception as e:
    import warnings
    warnings.warn(f"Could not create test tables: {e}")

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
    from app.services.settings_seed import seed_auth_settings, seed_audit_settings, seed_scheduler_settings

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

    # Mock the app startup seeding to avoid duplicate seeding
    with patch('app.main.seed_auth_settings'), \
         patch('app.main.seed_audit_settings'), \
         patch('app.main.seed_scheduler_settings'), \
         patch('app.main.SessionLocal', return_value=MagicMock()):
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client

    app.dependency_overrides.clear()


def _create_access_token(person_id: str, session_id: str, roles: list[str] = None, scopes: list[str] = None) -> str:
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
