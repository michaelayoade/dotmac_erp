"""
Tests for PeriodGuardService.
"""

from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.finance.gl.fiscal_period import PeriodStatus
from app.services.finance.gl.period_guard import (
    PeriodGuardService,
)

MockPeriodStatus = PeriodStatus


class MockColumn:
    """Mock SQLAlchemy column that supports comparison operations."""

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

    def desc(self):
        return MagicMock()

    def asc(self):
        return MagicMock()


class MockFiscalPeriod:
    """Mock FiscalPeriod model for testing."""

    def __init__(
        self,
        fiscal_period_id=None,
        organization_id=None,
        fiscal_year_id=None,
        period_name="2024-01",
        period_number=1,
        start_date=None,
        end_date=None,
        status=None,
        is_adjustment_period=False,
        last_reopen_session_id=None,
        soft_closed_at=None,
        soft_closed_by_user_id=None,
        hard_closed_at=None,
        hard_closed_by_user_id=None,
        reopen_count=0,
    ):
        self.fiscal_period_id = fiscal_period_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.fiscal_year_id = fiscal_year_id or uuid4()
        self.period_name = period_name
        self.period_number = period_number
        self.start_date = start_date or date(2024, 1, 1)
        self.end_date = end_date or date(2024, 1, 31)
        self.status = status or MockPeriodStatus.OPEN
        self.is_adjustment_period = is_adjustment_period
        self.last_reopen_session_id = last_reopen_session_id
        self.soft_closed_at = soft_closed_at
        self.soft_closed_by_user_id = soft_closed_by_user_id
        self.hard_closed_at = hard_closed_at
        self.hard_closed_by_user_id = hard_closed_by_user_id
        self.reopen_count = reopen_count


@contextmanager
def patch_period_guard():
    """Helper context manager that sets up all required patches."""
    with patch("app.services.finance.gl.period_guard.FiscalPeriod") as mock_fp:
        mock_fp.organization_id = MockColumn()
        mock_fp.start_date = MockColumn()
        mock_fp.end_date = MockColumn()
        mock_fp.status = MockColumn()
        mock_fp.period_number = MockColumn()
        with (
            patch(
                "app.services.finance.gl.period_guard.and_", return_value=MagicMock()
            ),
            patch(
                "app.services.finance.gl.period_guard.select", return_value=MagicMock()
            ),
            patch(
                "app.services.finance.gl.period_guard.PeriodStatus", MockPeriodStatus
            ),
        ):
            yield mock_fp


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return MagicMock()


@pytest.fixture
def org_id():
    """Create test organization ID."""
    return uuid4()


@pytest.fixture
def user_id():
    """Create test user ID."""
    return uuid4()


class TestCanPostToDate:
    """Tests for can_post_to_date method."""

    def test_no_period_found_returns_not_allowed(self, mock_db, org_id):
        """Test when no fiscal period contains the date and auto-create fails."""
        mock_db.scalars.return_value.all.return_value = []
        posting_date = date(2024, 1, 15)

        with (
            patch_period_guard(),
            patch.object(
                PeriodGuardService, "_ensure_period_exists", return_value=None
            ),
        ):
            result = PeriodGuardService.can_post_to_date(mock_db, org_id, posting_date)

        assert result.is_allowed is False
        assert result.fiscal_period_id is None
        assert "Failed to create fiscal period" in result.message

    def test_open_period_allows_posting(self, mock_db, org_id):
        """Test that OPEN period allows posting."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=MockPeriodStatus.OPEN,
        )
        mock_db.scalars.return_value.all.return_value = [period]
        posting_date = date(2024, 1, 15)

        with patch_period_guard():
            result = PeriodGuardService.can_post_to_date(mock_db, org_id, posting_date)

        assert result.is_allowed is True
        assert result.fiscal_period_id == period.fiscal_period_id
        assert "open for posting" in result.message

    def test_future_period_blocks_posting(self, mock_db, org_id):
        """Test that FUTURE period blocks posting."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=MockPeriodStatus.FUTURE,
        )
        mock_db.scalars.return_value.all.return_value = [period]
        posting_date = date(2024, 1, 15)

        with patch_period_guard():
            result = PeriodGuardService.can_post_to_date(mock_db, org_id, posting_date)

        assert result.is_allowed is False
        assert "not yet open" in result.message

    def test_soft_closed_period_blocks_posting(self, mock_db, org_id):
        """Test that SOFT_CLOSED period blocks posting."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=MockPeriodStatus.SOFT_CLOSED,
        )
        mock_db.scalars.return_value.all.return_value = [period]
        posting_date = date(2024, 1, 15)

        with patch_period_guard():
            result = PeriodGuardService.can_post_to_date(mock_db, org_id, posting_date)

        assert result.is_allowed is False
        assert "soft-closed" in result.message

    def test_hard_closed_period_blocks_posting(self, mock_db, org_id):
        """Test that HARD_CLOSED period blocks posting permanently."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=MockPeriodStatus.HARD_CLOSED,
        )
        mock_db.scalars.return_value.all.return_value = [period]
        posting_date = date(2024, 1, 15)

        with patch_period_guard():
            result = PeriodGuardService.can_post_to_date(mock_db, org_id, posting_date)

        assert result.is_allowed is False
        assert "permanently closed" in result.message

    def test_reopened_period_requires_session_id(self, mock_db, org_id):
        """Test that REOPENED period requires reopen session ID."""
        session_id = uuid4()
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=MockPeriodStatus.REOPENED,
            last_reopen_session_id=session_id,
        )
        mock_db.scalars.return_value.all.return_value = [period]
        posting_date = date(2024, 1, 15)

        with patch_period_guard():
            result = PeriodGuardService.can_post_to_date(mock_db, org_id, posting_date)

        assert result.is_allowed is False
        assert "reopen session ID required" in result.message

    def test_reopened_period_allows_with_valid_session_id(self, mock_db, org_id):
        """Test that REOPENED period allows posting with valid session ID."""
        session_id = uuid4()
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=MockPeriodStatus.REOPENED,
            last_reopen_session_id=session_id,
        )
        mock_db.scalars.return_value.all.return_value = [period]
        posting_date = date(2024, 1, 15)

        with patch_period_guard():
            result = PeriodGuardService.can_post_to_date(
                mock_db, org_id, posting_date, reopen_session_id=session_id
            )

        assert result.is_allowed is True
        assert result.reopen_session_id == session_id

    def test_adjustment_period_requires_flag(self, mock_db, org_id):
        """Test that adjustment periods require explicit flag."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=MockPeriodStatus.OPEN,
            is_adjustment_period=True,
        )
        mock_db.scalars.return_value.all.return_value = [period]
        posting_date = date(2024, 1, 15)

        with patch_period_guard():
            result = PeriodGuardService.can_post_to_date(mock_db, org_id, posting_date)

        assert result.is_allowed is False
        assert "Adjustment periods require explicit allowance" in result.message

    def test_adjustment_period_allows_with_flag(self, mock_db, org_id):
        """Test that adjustment periods allow posting with flag."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=MockPeriodStatus.OPEN,
            is_adjustment_period=True,
        )
        mock_db.scalars.return_value.all.return_value = [period]
        posting_date = date(2024, 1, 15)

        with patch_period_guard():
            result = PeriodGuardService.can_post_to_date(
                mock_db, org_id, posting_date, allow_adjustment=True
            )

        assert result.is_allowed is True


class TestRequireOpenPeriod:
    """Tests for require_open_period method."""

    def test_returns_period_id_when_open(self, mock_db, org_id):
        """Test returns fiscal period ID when period is open."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=MockPeriodStatus.OPEN,
        )
        mock_db.scalars.return_value.all.return_value = [period]
        posting_date = date(2024, 1, 15)

        with patch_period_guard():
            result = PeriodGuardService.require_open_period(
                mock_db, org_id, posting_date
            )

        assert result == period.fiscal_period_id

    def test_raises_exception_when_closed(self, mock_db, org_id):
        """Test raises HTTPException when period is closed."""
        from fastapi import HTTPException

        period = MockFiscalPeriod(
            organization_id=org_id,
            status=MockPeriodStatus.HARD_CLOSED,
        )
        mock_db.scalars.return_value.all.return_value = [period]
        posting_date = date(2024, 1, 15)

        with patch_period_guard(), pytest.raises(HTTPException) as exc:
            PeriodGuardService.require_open_period(mock_db, org_id, posting_date)

        assert exc.value.status_code == 400


class TestPeriodOperations:
    """Tests for period state change operations."""

    def test_open_period_changes_status(self, mock_db, org_id, user_id):
        """Test opening a future period."""
        period_id = uuid4()
        period = MockFiscalPeriod(
            fiscal_period_id=period_id,
            organization_id=org_id,
            status=MockPeriodStatus.FUTURE,
        )
        mock_db.get.return_value = period

        with patch_period_guard():
            result = PeriodGuardService.open_period(mock_db, org_id, period_id, user_id)

        assert result.status == MockPeriodStatus.OPEN
        mock_db.commit.assert_called_once()

    def test_open_period_not_found_raises(self, mock_db, org_id, user_id):
        """Test opening non-existent period raises error."""
        from fastapi import HTTPException

        mock_db.get.return_value = None
        period_id = uuid4()

        with patch_period_guard(), pytest.raises(HTTPException) as exc:
            PeriodGuardService.open_period(mock_db, org_id, period_id, user_id)

        assert exc.value.status_code == 404

    def test_soft_close_period(self, mock_db, org_id, user_id):
        """Test soft-closing an open period."""
        period_id = uuid4()
        period = MockFiscalPeriod(
            fiscal_period_id=period_id,
            organization_id=org_id,
            status=MockPeriodStatus.OPEN,
        )
        mock_db.get.return_value = period

        with patch_period_guard():
            result = PeriodGuardService.soft_close_period(
                mock_db, org_id, period_id, user_id
            )

        assert result.status == MockPeriodStatus.SOFT_CLOSED
        mock_db.commit.assert_called_once()

    def test_hard_close_period(self, mock_db, org_id, user_id):
        """Test hard-closing a soft-closed period."""
        period_id = uuid4()
        period = MockFiscalPeriod(
            fiscal_period_id=period_id,
            organization_id=org_id,
            status=MockPeriodStatus.SOFT_CLOSED,
        )
        mock_db.get.return_value = period

        with patch_period_guard():
            result = PeriodGuardService.hard_close_period(
                mock_db, org_id, period_id, user_id
            )

        assert result.status == MockPeriodStatus.HARD_CLOSED
        mock_db.commit.assert_called_once()

    def test_reopen_period(self, mock_db, org_id, user_id):
        """Test reopening a soft-closed period."""
        period_id = uuid4()
        period = MockFiscalPeriod(
            fiscal_period_id=period_id,
            organization_id=org_id,
            status=MockPeriodStatus.SOFT_CLOSED,
            reopen_count=0,
        )
        mock_db.get.return_value = period

        with patch_period_guard():
            result, session_id = PeriodGuardService.reopen_period(
                mock_db, org_id, period_id, user_id, "Correction needed"
            )

        assert result.status == MockPeriodStatus.REOPENED
        assert result.reopen_count == 1
        assert session_id is not None
        mock_db.commit.assert_called_once()

    def test_close_reopen_session(self, mock_db, org_id, user_id):
        """Test closing a reopen session."""
        period_id = uuid4()
        session_id = uuid4()
        period = MockFiscalPeriod(
            fiscal_period_id=period_id,
            organization_id=org_id,
            status=MockPeriodStatus.REOPENED,
            last_reopen_session_id=session_id,
        )
        mock_db.get.return_value = period

        with patch_period_guard():
            result = PeriodGuardService.close_reopen_session(
                mock_db, org_id, period_id, session_id, user_id
            )

        assert result.status == MockPeriodStatus.SOFT_CLOSED
        mock_db.commit.assert_called_once()


class TestGetPeriodMethods:
    """Tests for period retrieval methods."""

    def test_get_period_for_date(self, mock_db, org_id):
        """Test getting period containing a specific date."""
        period = MockFiscalPeriod(organization_id=org_id)
        mock_db.scalars.return_value.all.return_value = [period]
        target_date = date(2024, 1, 15)

        with patch_period_guard():
            result = PeriodGuardService.get_period_for_date(
                mock_db, org_id, target_date
            )

        assert result == period

    def test_get_open_periods(self, mock_db, org_id):
        """Test getting all open periods."""
        periods = [
            MockFiscalPeriod(organization_id=org_id, status=MockPeriodStatus.OPEN),
            MockFiscalPeriod(organization_id=org_id, status=MockPeriodStatus.REOPENED),
        ]
        mock_db.scalars.return_value.all.return_value = periods

        with patch_period_guard():
            result = PeriodGuardService.get_open_periods(mock_db, org_id)

        assert len(result) == 2

    def test_get_current_period(self, mock_db, org_id):
        """Test getting period for today."""
        period = MockFiscalPeriod(organization_id=org_id)
        mock_db.scalars.return_value.all.return_value = [period]

        with patch_period_guard():
            result = PeriodGuardService.get_current_period(mock_db, org_id)

        assert result == period

    def test_list_periods(self, mock_db, org_id):
        """Test listing periods with filters."""
        periods = [MockFiscalPeriod(organization_id=org_id)]
        mock_db.scalars.return_value.all.return_value = periods

        with patch_period_guard():
            result = PeriodGuardService.list(
                mock_db,
                organization_id=str(org_id),
                limit=50,
                offset=0,
            )

        assert result == periods
