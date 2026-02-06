"""
Shared fixtures for core services tests.

Provides mock objects and common utilities for testing services
like common.py, bulk_actions.py, scheduler.py, audit.py, and scheduler_config.py.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


# ============ UUID Fixtures ============


@pytest.fixture
def organization_id():
    """Provide a consistent organization UUID for tests."""
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def user_id():
    """Provide a consistent user UUID for tests."""
    return uuid.UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def entity_id():
    """Provide a consistent entity UUID for tests."""
    return uuid.UUID("00000000-0000-0000-0000-000000000003")


# ============ Mock Database Session ============


@pytest.fixture
def mock_db():
    """Create a mock database session with chained query methods."""
    db = MagicMock()

    # Setup query chaining
    mock_query = MagicMock()
    db.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.filter_by.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.offset.return_value = mock_query
    mock_query.all.return_value = []
    mock_query.first.return_value = None
    mock_query.count.return_value = 0

    return db


# ============ Mock Entity Classes ============


class MockEntity:
    """Base mock entity for testing bulk actions."""

    def __init__(
        self,
        id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        is_active: bool = True,
        name: str = "Test Entity",
        created_at: datetime | None = None,
    ):
        self.id = id or uuid.uuid4()
        self.organization_id = organization_id or uuid.UUID(
            "00000000-0000-0000-0000-000000000001"
        )
        self.is_active = is_active
        self.name = name
        self.created_at = created_at or datetime.now(timezone.utc)


class MockEntityWithRelation(MockEntity):
    """Mock entity with a nested relationship for export testing."""

    def __init__(
        self,
        related_entity: "MockRelatedEntity | None" = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.related_entity = related_entity
        self.tags = ["tag1", "tag2"]
        self.metadata_ = {"key": "value"}


class MockRelatedEntity:
    """Mock related entity for testing nested attribute access."""

    def __init__(
        self,
        name: str = "Related",
        email: str = "related@example.com",
        phone: str | None = None,
    ):
        self.name = name
        self.email = email
        self.phone = phone


# ============ Mock Model Class ============


@pytest.fixture
def mock_model_class():
    """Create a mock SQLAlchemy model class for testing."""
    model = MagicMock()
    model.id = MagicMock()
    model.organization_id = MagicMock()
    model.is_active = MagicMock()
    model.name = MagicMock()
    model.created_at = MagicMock()

    # Configure column descriptors
    model.id.in_ = MagicMock(return_value=MagicMock())
    model.id.desc = MagicMock(return_value=MagicMock())
    model.id.asc = MagicMock(return_value=MagicMock())
    model.name.desc = MagicMock(return_value=MagicMock())
    model.name.asc = MagicMock(return_value=MagicMock())
    model.created_at.desc = MagicMock(return_value=MagicMock())
    model.created_at.asc = MagicMock(return_value=MagicMock())

    return model


# ============ Request/Response Mocks ============


@pytest.fixture
def mock_request():
    """Create a mock FastAPI Request object."""
    request = MagicMock()
    request.method = "GET"
    request.url.path = "/api/test"
    request.headers = {
        "x-actor-type": "user",
        "x-actor-id": "00000000-0000-0000-0000-000000000002",
        "x-request-id": "req-123",
        "x-entity-id": "00000000-0000-0000-0000-000000000003",
        "user-agent": "pytest/1.0",
    }
    request.headers.get = lambda key, default=None: request.headers.get(key, default)

    # Mock the get method properly
    headers_dict = dict(request.headers)
    request.headers = MagicMock()
    request.headers.get = lambda key, default=None: headers_dict.get(key, default)

    # Mock client
    request.client = MagicMock()
    request.client.host = "127.0.0.1"

    # Mock query params
    request.query_params = {"page": "1", "limit": "10"}

    return request


@pytest.fixture
def mock_response():
    """Create a mock FastAPI Response object."""
    response = MagicMock()
    response.status_code = 200
    return response


# ============ Scheduler Fixtures ============


@pytest.fixture
def mock_scheduled_task():
    """Create a mock ScheduledTask object."""
    from app.models.scheduler import ScheduleType

    task = MagicMock()
    task.id = uuid.uuid4()
    task.name = "test_task"
    task.task_name = "app.tasks.test_task"
    task.schedule_type = ScheduleType.interval
    task.interval_seconds = 300
    task.cron_expression = None
    task.args_json = ["arg1", "arg2"]
    task.kwargs_json = {"key": "value"}
    task.enabled = True
    task.created_at = datetime.now(timezone.utc)
    task.updated_at = datetime.now(timezone.utc)
    return task


@pytest.fixture
def mock_scheduled_task_create():
    """Create a mock ScheduledTaskCreate payload."""
    return MagicMock(
        name="test_task",
        task_name="app.tasks.test_task",
        schedule_type="interval",
        interval_seconds=300,
        cron_expression=None,
        args_json=None,
        kwargs_json=None,
        enabled=True,
        model_dump=lambda: {
            "name": "test_task",
            "task_name": "app.tasks.test_task",
            "schedule_type": "interval",
            "interval_seconds": 300,
            "cron_expression": None,
            "args_json": None,
            "kwargs_json": None,
            "enabled": True,
        },
    )


# ============ Audit Fixtures ============


@pytest.fixture
def mock_audit_event():
    """Create a mock AuditEvent object."""
    from app.models.audit import AuditActorType

    event = MagicMock()
    event.id = uuid.uuid4()
    event.actor_id = str(uuid.uuid4())
    event.actor_type = AuditActorType.user
    event.action = "test_action"
    event.entity_type = "test_entity"
    event.entity_id = str(uuid.uuid4())
    event.status_code = 200
    event.is_success = True
    event.is_active = True
    event.ip_address = "127.0.0.1"
    event.user_agent = "pytest"
    event.request_id = "req-123"
    event.metadata_ = {}
    event.occurred_at = datetime.now(timezone.utc)
    return event


@pytest.fixture
def mock_audit_event_create():
    """Create a mock AuditEventCreate payload."""
    from app.models.audit import AuditActorType

    return MagicMock(
        actor_type=AuditActorType.user,
        actor_id=str(uuid.uuid4()),
        action="test_action",
        entity_type="test_entity",
        entity_id=str(uuid.uuid4()),
        status_code=200,
        is_success=True,
        ip_address="127.0.0.1",
        user_agent="pytest",
        request_id="req-123",
        metadata_={},
        occurred_at=None,
        model_dump=lambda: {
            "actor_type": AuditActorType.user,
            "actor_id": str(uuid.uuid4()),
            "action": "test_action",
            "entity_type": "test_entity",
            "entity_id": str(uuid.uuid4()),
            "status_code": 200,
            "is_success": True,
            "ip_address": "127.0.0.1",
            "user_agent": "pytest",
            "request_id": "req-123",
            "metadata_": {},
            "occurred_at": None,
        },
    )


# ============ Domain Settings Fixtures ============


@pytest.fixture
def mock_domain_setting():
    """Create a mock DomainSetting object."""
    from app.models.domain_settings import SettingDomain

    setting = MagicMock()
    setting.id = uuid.uuid4()
    setting.domain = SettingDomain.scheduler
    setting.key = "test_key"
    setting.value_text = "test_value"
    setting.value_json = None
    setting.is_active = True
    return setting
