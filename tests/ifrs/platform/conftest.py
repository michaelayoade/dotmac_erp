"""
Fixtures for IFRS Platform Services Tests.

These tests use mock objects to avoid PostgreSQL-specific dependencies
while still testing the service logic.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

# ============ Mock Column for SQLAlchemy Comparisons ============


class MockColumn:
    """
    Mock SQLAlchemy column that supports comparison operations.

    SQLAlchemy expressions like `Column == value` return BinaryExpression objects.
    MagicMock can't properly handle these comparisons with dates and other types.
    This class returns MagicMock for all comparison operations.
    """

    def __le__(self, other):
        return MagicMock()

    def __ge__(self, other):
        return MagicMock()

    def __lt__(self, other):
        return MagicMock()

    def __gt__(self, other):
        return MagicMock()

    def __eq__(self, other):
        return MagicMock()

    def __ne__(self, other):
        return MagicMock()

    def in_(self, values):
        return MagicMock()

    def is_(self, value):
        return MagicMock()

    def isnot(self, value):
        return MagicMock()

    def like(self, pattern):
        return MagicMock()

    def ilike(self, pattern):
        return MagicMock()

    def desc(self):
        return MagicMock()

    def asc(self):
        return MagicMock()

    def startswith(self, prefix):
        return MagicMock()

    def endswith(self, suffix):
        return MagicMock()

    def contains(self, value):
        return MagicMock()


# ============ Mock Model Classes ============


class MockIdempotencyRecord:
    """Mock IdempotencyRecord model."""

    def __init__(
        self,
        record_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        idempotency_key: str = "",
        endpoint: str = "",
        request_hash: str = "",
        response_status: int = 200,
        response_body: dict | None = None,
        created_at: datetime = None,
        expires_at: datetime = None,
    ):
        self.record_id = record_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.idempotency_key = idempotency_key
        self.endpoint = endpoint
        self.request_hash = request_hash
        self.response_status = response_status
        self.response_body = response_body
        self.created_at = created_at or datetime.now(UTC)
        self.expires_at = expires_at or (datetime.now(UTC) + timedelta(hours=24))


class MockNumberingSequence:
    """Mock NumberingSequence model."""

    def __init__(
        self,
        sequence_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        sequence_type: str = "INVOICE",
        prefix: str | None = None,
        suffix: str | None = None,
        current_number: int = 0,
        min_digits: int = 6,
        fiscal_year_reset: bool = False,
        fiscal_year_id: uuid.UUID | None = None,
        last_used_at: datetime | None = None,
        created_at: datetime = None,
    ):
        self.sequence_id = sequence_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.sequence_type = sequence_type
        self.prefix = prefix
        self.suffix = suffix
        self.current_number = current_number
        self.min_digits = min_digits
        self.fiscal_year_reset = fiscal_year_reset
        self.fiscal_year_id = fiscal_year_id
        self.last_used_at = last_used_at
        self.created_at = created_at or datetime.now(UTC)


class MockExchangeRate:
    """Mock ExchangeRate model."""

    def __init__(
        self,
        exchange_rate_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        from_currency_code: str = "USD",
        to_currency_code: str = "EUR",
        rate_type_id: uuid.UUID = None,
        effective_date: date = None,
        exchange_rate: Decimal = Decimal("1.0"),
        source: str | None = None,
        created_by_user_id: uuid.UUID | None = None,
        created_at: datetime = None,
    ):
        self.exchange_rate_id = exchange_rate_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.from_currency_code = from_currency_code
        self.to_currency_code = to_currency_code
        self.rate_type_id = rate_type_id or uuid.uuid4()
        self.effective_date = effective_date or date.today()
        self.exchange_rate = exchange_rate
        self.source = source
        self.created_by_user_id = created_by_user_id
        self.created_at = created_at or datetime.now(UTC)

    @property
    def inverse_rate(self) -> Decimal:
        return Decimal(1) / self.exchange_rate if self.exchange_rate else Decimal(0)


class MockExchangeRateType:
    """Mock ExchangeRateType model."""

    def __init__(
        self,
        rate_type_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        type_code: str = "SPOT",
        type_name: str = "Spot Rate",
        description: str | None = None,
        is_default: bool = False,
        created_at: datetime = None,
    ):
        self.rate_type_id = rate_type_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.type_code = type_code
        self.type_name = type_name
        self.description = description
        self.is_default = is_default
        self.created_at = created_at or datetime.now(UTC)


class MockOrganization:
    """Mock Organization model."""

    def __init__(
        self,
        organization_id: uuid.UUID = None,
        organization_code: str = "TEST",
        legal_name: str = "Test Organization",
        functional_currency_code: str = "USD",
        presentation_currency_code: str = "USD",
        fiscal_year_end_month: int = 12,
        fiscal_year_end_day: int = 31,
        is_active: bool = True,
    ):
        self.organization_id = organization_id or uuid.uuid4()
        self.organization_code = organization_code
        self.legal_name = legal_name
        self.functional_currency_code = functional_currency_code
        self.presentation_currency_code = presentation_currency_code
        self.fiscal_year_end_month = fiscal_year_end_month
        self.fiscal_year_end_day = fiscal_year_end_day
        self.is_active = is_active


class MockSystemConfiguration:
    """Mock SystemConfiguration model."""

    def __init__(
        self,
        config_id: uuid.UUID = None,
        organization_id: uuid.UUID | None = None,
        config_key: str = "",
        config_value: str = "",
        config_type: str = "STRING",
        description: str | None = None,
        is_encrypted: bool = False,
        updated_at: datetime = None,
        updated_by_user_id: uuid.UUID | None = None,
    ):
        self.config_id = config_id or uuid.uuid4()
        self.organization_id = organization_id
        self.config_key = config_key
        self.config_value = config_value
        self.config_type = config_type
        self.description = description
        self.is_encrypted = is_encrypted
        self.updated_at = updated_at or datetime.now(UTC)
        self.updated_by_user_id = updated_by_user_id


class MockEventOutbox:
    """Mock EventOutbox model."""

    def __init__(
        self,
        event_id: uuid.UUID = None,
        event_name: str = "",
        aggregate_type: str = "",
        aggregate_id: str = "",
        payload: dict = None,
        headers: dict = None,
        producer_module: str = "",
        correlation_id: str = "",
        idempotency_key: str = "",
        causation_id: uuid.UUID | None = None,
        event_version: int = 1,
        status: str = "PENDING",
        retry_count: int = 0,
        next_retry_at: datetime | None = None,
        last_error: str | None = None,
        occurred_at: datetime = None,
        published_at: datetime | None = None,
        created_at: datetime = None,
    ):
        self.event_id = event_id or uuid.uuid4()
        self.event_name = event_name
        self.aggregate_type = aggregate_type
        self.aggregate_id = aggregate_id
        self.payload = payload or {}
        self.headers = headers or {}
        self.producer_module = producer_module
        self.correlation_id = correlation_id
        self.idempotency_key = idempotency_key
        self.causation_id = causation_id
        self.event_version = event_version
        self.status = status
        self.retry_count = retry_count
        self.next_retry_at = next_retry_at
        self.last_error = last_error
        self.occurred_at = occurred_at or datetime.now(UTC)
        self.published_at = published_at
        self.created_at = created_at or datetime.now(UTC)


# ============ Fixtures ============


@pytest.fixture
def organization_id() -> uuid.UUID:
    """Generate a test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def user_id() -> uuid.UUID:
    """Generate a test user ID."""
    return uuid.uuid4()


@pytest.fixture
def mock_organization(organization_id) -> MockOrganization:
    """Create a mock organization."""
    return MockOrganization(organization_id=organization_id)


@pytest.fixture
def mock_db_session():
    """Create a mock database session."""
    session = MagicMock()
    session.query = MagicMock(return_value=session)
    session.filter = MagicMock(return_value=session)
    session.first = MagicMock(return_value=None)
    session.all = MagicMock(return_value=[])
    session.add = MagicMock()
    session.commit = MagicMock()
    session.flush = MagicMock()
    session.refresh = MagicMock()
    session.delete = MagicMock()
    session.get = MagicMock(return_value=None)
    session.execute = MagicMock()
    return session
